# Task 6 报告：IngestionRunner、失败清理与重启恢复

## 结果

- 新增 `src/xhbx_rag/web/ingestion_runner.py` 与 `tests/test_web_ingestion_runner.py`。
- 扩展 `src/xhbx_rag/web/safe_errors.py`，为九个 ingestion code 提供固定中文 detail，并严格限制 Pipeline code 白名单。
- 为跨任务集成最小扩展 `AtomicIndexer.inspect_journal_state()`，完整复用既有 journal/snapshot 校验与 committed ID 核验，不在 Runner 复制 checksum 或 snapshot 算法；对应测试补在 `tests/test_atomic_indexer.py`。

## 核心契约

- 同步执行顺序为 claim → 创建/begin attempt → Pipeline 事件映射 → indexing → AtomicIndexer callbacks → journal 权威调和 → 清理 attempt → 终态；源文件始终保留。
- Pipeline 仅映射 `item_started/item_completed/item_failed`，未知事件（包括携带路径/正文的 `course_file`）完全忽略，不写 Store。
- pre-commit Pipeline/embedding/indexer 失败只在 attempt 清理成功后进入 failed；清理失败保持可恢复的 running 状态。
- commit 异常不按异常类型猜测提交结果：
  - verified `committed`（含 ID 集合核验）调和 SQLite 为 committed，再清理并 succeeded，绝不回滚；
  - verified `rolled_back` 调和 `prepared → rolling_back → rolled_back`，再清理并 failed；
  - `RollbackPendingError` 即使 prepared callback 未落 SQLite，也使用异常 journal path 调和 `not_started → prepared → rolling_back`；
  - journal 损坏或无法验证时保持 rolling_back/rollback_pending，保留 staging、snapshot、journal 等全部材料。
- `execute_recovery()` 先调用 recover，再用只读验证状态区分 committed/rolled_back；失败按 2、4、8…60 秒退避，支持注入 sleep 且 stop 可中断。
- `recover_after_restart()` 先同步处理 fail_interrupted，再把 rollback/finish_committed/delete 恢复项以高优先级 typed queue item 入队，queued job 使用较低优先级，避免恢复窗口新写。
- 删除严格先幂等删除 workspace，再 `finish_delete()`；失败保持 deleting，供下次恢复重试。
- worker 为单 daemon 线程；start/stop/enqueue 语义幂等，stop 最多 join 10 秒，执行中 stop 不遗留会杀死下一轮 start 的旧 sentinel，单项异常不终止 worker。
- 所有终态 success/failed 都在 Task 1 `clear_attempt_workspaces()` 成功后落 Store；committed 或 rolled_back 后清理失败均保留可恢复状态。
- committed 清理采用同文件系统恢复材料暂存：先把 rollback 目录原子移出 attempts 并 fsync，再调用 Task 1 清理；若 `succeed_job()` 失败则恢复原 journal 路径，重启也能发现暂存材料，避免 running/committed 失去 durable 证据。

## TDD 证据

### Atomic journal inspect

首次 RED：

```text
uv run pytest tests/test_atomic_indexer.py -q -k inspect_journal_state
1 failed: AttributeError: 'AtomicIndexer' object has no attribute 'inspect_journal_state'
```

最小实现后 GREEN：

```text
uv run pytest tests/test_atomic_indexer.py::test_inspect_journal_state_validates_prepared_snapshot_without_writing tests/test_atomic_indexer.py::test_inspect_committed_journal_verifies_durable_ids -q
2 passed
```

### Runner 主流程

首次 RED：

```text
uv run pytest tests/test_web_ingestion_runner.py -q
ModuleNotFoundError: No module named 'xhbx_rag.web.ingestion_runner'
```

首轮 GREEN：

```text
17 passed in 0.88s
```

自检补充三个并发/集成回归测试，RED 精确命中：重复构造 indexer、rolled_back 清理恢复错误码、执行中 stop 遗留 sentinel：

```text
3 failed, 17 deselected
```

修复后：

```text
3 passed, 17 deselected
```

恢复工厂临时不可用的 RED/GREEN：

```text
1 failed: RuntimeError: Milvus /private/path token=secret
1 passed
```

Runner 最终聚焦：

```text
uv run pytest tests/test_web_ingestion_runner.py -q
24 passed in 0.98s
```

独立只读审查首次发现并复现三个窗口：SQLite `not_started` 但 workspace journal 已 rolling_back、清理后 SQLite 终态写失败丢 journal、并发 stop 遗留 sentinel。三条回归测试先全部 RED，修复后：

```text
uv run pytest tests/test_web_ingestion_runner.py -q -k 'discovers_durable or sqlite_success_failure or concurrent_stop'
3 passed
```

同一审查者复核确认此前 2 个 Critical 与 1 个 Important 均已修复，无新增阻塞项。

## 最终验证

```text
uv run pytest tests/test_web_ingestion_store.py tests/test_web_ingestion_runner.py tests/test_atomic_indexer.py -q
127 passed in 3.77s

uv run python -m compileall -q src/xhbx_rag/atomic_indexer.py src/xhbx_rag/web/ingestion_runner.py src/xhbx_rag/web/safe_errors.py tests/test_atomic_indexer.py tests/test_web_ingestion_runner.py
exit 0

git diff --check
exit 0

uv run pytest -q
683 passed, 1 warning in 15.58s
```

唯一 warning 是仓库既存 FastAPI TestClient 的 `StarletteDeprecationWarning`，与 Task 6 无关。

## 安全说明

- Store 只接收固定 code/detail；raw exception、绝对路径、token、模型正文都不会通过 Runner 写入 SQLite/API。
- AtomicIndexer 的“向量生成失败”固定映射为 `embedding_failed`，其他 AtomicIndexError 固定映射为 `index_failed`。
- rollback pending、journal corruption、清理失败均不会伪装为普通失败或成功，也不会提前删除恢复材料。
