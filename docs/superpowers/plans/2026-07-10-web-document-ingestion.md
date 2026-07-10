# Web 文档入库工作台 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在现有 React + FastAPI 应用中增加支持单文档/ZIP、案例库/课程库选择、持久化异步执行、全批失败回滚和从头重试的文档入库工作台。

**Architecture:** FastAPI 通过独立 SQLite Store、上传预检服务和单 Worker Runner 编排现有案例/课程 Python 管线；所有 chunks 与 embeddings 完整生成后，由带持久化 journal 的 AtomicIndexer 一次提交到目标 Milvus collection，并在异常或重启后补偿恢复。React 继续使用现有三栏壳层，通过 URL query 切换问答与入库视图，XHR 展示上传进度，轮询 API 恢复任务状态。

**Tech Stack:** Python 3.12、FastAPI、SQLite、pymilvus、pytest、React 19、TypeScript 6、Vite 8、Vitest、Testing Library、Lucide React、原生 XMLHttpRequest。

## Global Constraints

- 上传格式仅允许 `docx / pptx / pdf / txt / zip`；第一版不支持 `rar / 7z`。
- 页面必须选择 `case` 或 `course`；不允许任意 collection 名称。
- `case` 必须执行完整 `generate-insights → parse → chunk → embed → index` 管线，且生成结果只有 `ok` 可继续，`partial` 视为失败。
- `course` 执行现有课程解析与切分；课程级 LLM 增值失败仅记录 warning，文档解析/切分失败使整批失败。
- 任一必需步骤失败时整批不入库；写库异常必须删除本批新记录并恢复同 ID 旧记录。
- 重试保留 `source/`，删除其他 attempt 产物，并从第一个输入项重新执行。
- 原始上传文件保留到删除任务；删除成功任务不撤销已经提交的知识。
- Web 仅允许 incremental upsert，不暴露 rebuild、取消、单项重试或部分成功。
- 默认限制：上传 512 MiB、ZIP 2000 项、解压总量 2 GiB、单条目 512 MiB、压缩比 100:1、路径 512 字符。
- 单 FastAPI 实例、单入库 Worker；项目内 CLI 与 Web 使用同一 collection 文件锁，外部分布式写入不在范围内。
- API 和 SQLite 不保存/返回文档正文、模型原始响应、密钥、绝对路径或完整堆栈。
- 前端状态必须同时使用图标与中文文字；上传/错误/进度具备键盘操作、`role="alert"` 和 `aria-live`。
- 设计规格是 `docs/superpowers/specs/2026-07-10-web-document-ingestion-design.md`，实现不得偏离其中已确认的输入映射、状态机与验收标准。

---

## File Structure

### Backend production files

- `src/xhbx_rag/web/ingestion_uploads.py`：上传限制、文件名归一、ZIP 安全预检、输入映射、attempt 解压与目录清理。
- `src/xhbx_rag/web/ingestion_store.py`：任务/输入项/attempt/事件 SQLite schema、状态机、查询与重启恢复动作。
- `src/xhbx_rag/index_lock.py`：按 Milvus URI + collection 派生的跨进程文件锁。
- `src/xhbx_rag/atomic_indexer.py`：全批 embedding、rollback snapshot、commit journal、原子式提交与补偿恢复。
- `src/xhbx_rag/web/ingestion_pipeline.py`：案例与课程严格加工、staging 合并、全批 chunk 校验。
- `src/xhbx_rag/web/ingestion_runner.py`：单 Worker 队列、阶段持久化、失败清理、rollback 恢复与退避。
- `src/xhbx_rag/web/ingestion_routes.py`：multipart 创建、start/list/detail/progress/retry/delete API。
- `src/xhbx_rag/course_parser.py`：增加保持 CLI 默认宽松语义的 `fail_fast`/进度回调接口。
- `src/xhbx_rag/milvus_store.py`：完整原始行读取、按 ID 删除、原始行恢复、collection existence/flush 原语。
- `src/xhbx_rag/indexer.py`：现有 CLI index 写路径接入同一 collection 文件锁。
- `src/xhbx_rag/web/app.py`：注入 ingestion Store/Runner、lifespan 恢复、路由挂载。
- `src/xhbx_rag/web/safe_errors.py`：入库异常到固定错误码/中文 detail 的映射。
- `src/xhbx_rag/config.py`：读取并校验 Web 入库限制默认值。

### Frontend production files

- `web/src/ingestion.ts`：状态标签、进度映射、终态判断和请求结果归一纯函数。
- `web/src/workspaceLocation.ts`：`view=ingestion&job=` 与 History API 同步。
- `web/src/ingestionUpload.ts`：XHR multipart 上传与进度回调。
- `web/src/hooks/useIngestionJobPolling.ts`：所选任务 2 秒轮询，终态停止。
- `web/src/components/WorkspaceNav.tsx`：知识问答/文档入库顶层导航。
- `web/src/components/IngestionSidebar.tsx`：新建按钮和任务历史。
- `web/src/components/IngestionCreateView.tsx`：目标选择、拖放上传、预检、确认启动。
- `web/src/components/IngestionRunView.tsx`：四阶段、输入项、warning、失败与重试。
- `web/src/components/IngestionDetailPanel.tsx`：任务摘要、attempt、统计与事件时间线。
- `web/src/types.ts`、`web/src/api.ts`、`web/src/App.tsx`、`web/src/styles.css`：类型/API/壳层/响应式样式集成。

### Tests and documentation

- `tests/test_web_ingestion_uploads.py`
- `tests/test_web_ingestion_store.py`
- `tests/test_index_lock.py`
- `tests/test_atomic_indexer.py`
- `tests/test_web_ingestion_pipeline.py`
- `tests/test_web_ingestion_runner.py`
- `tests/test_web_ingestion_routes.py`
- `tests/test_web_app.py`
- `tests/test_docker_deployment.py`
- `web/src/ingestion.test.ts`
- `web/src/workspaceLocation.test.ts`
- `web/src/ingestionUpload.test.ts`
- `web/src/hooks/useIngestionJobPolling.test.tsx`
- `web/src/App.ingestion.test.tsx`
- `.env.example`、`README.md`、`web/nginx.conf`、`docker-compose.yml`、`pyproject.toml`、`uv.lock`

---

### Task 1: 安全上传、ZIP 预检与输入映射

**Files:**
- Create: `src/xhbx_rag/web/ingestion_uploads.py`
- Create: `tests/test_web_ingestion_uploads.py`

**Interfaces:**
- Produces: `IngestionLimits`, `PreflightItem`, `PreflightResult`, `UploadValidationError`, `save_upload_file()`, `preflight_upload()`, `materialize_attempt_inputs()`, `clear_attempt_workspaces()`, `delete_job_workspace()`。
- Consumers: Task 2 保存 `PreflightResult`；Task 5 用 `materialize_attempt_inputs()`；Task 7 路由调用 `save_upload_file()` 和 `preflight_upload()`。

- [ ] **Step 1: 写单文件与 ZIP 映射失败测试**

```python
from pathlib import Path
from zipfile import ZipFile

import pytest

from xhbx_rag.web.ingestion_uploads import (
    IngestionLimits,
    UploadValidationError,
    preflight_upload,
)


def _zip(path: Path, entries: dict[str, bytes]) -> Path:
    with ZipFile(path, "w") as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return path


def test_case_zip_maps_root_files_and_first_level_directories(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "优秀案例.zip",
        {
            "根目录.txt": b"root",
            "王女士/第一节/讲义.txt": b"case-a",
            "李先生/需求分析.docx": b"case-b",
            "__MACOSX/._meta": b"ignored",
            "王女士/video.mp4": b"ignored",
        },
    )

    result = preflight_upload(source, target="case", limits=IngestionLimits())

    assert [(item.unit_key, item.document_count) for item in result.items] == [
        ("__root__", 1),
        ("李先生", 1),
        ("王女士", 1),
    ]
    assert result.document_total == 3
    assert result.ignored_total == 2


def test_course_zip_maps_each_document_and_keeps_relative_path(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "新人课程.zip",
        {
            "新人培训/促成课.pptx": b"pptx",
            "新人培训/异议处理/讲义.pdf": b"pdf",
        },
    )

    result = preflight_upload(source, target="course", limits=IngestionLimits())

    assert [item.unit_key for item in result.items] == [
        "新人培训/促成课.pptx",
        "新人培训/异议处理/讲义.pdf",
    ]


def test_case_zip_rejects_normalized_name_collision(tmp_path: Path) -> None:
    source = _zip(
        tmp_path / "案例.zip",
        {"A/B.txt": b"a", "A /C.txt": b"b"},
    )

    with pytest.raises(UploadValidationError, match="案例名称冲突"):
        preflight_upload(source, target="case", limits=IngestionLimits())
```

- [ ] **Step 2: 运行测试并确认因为模块不存在而失败**

Run: `uv run pytest tests/test_web_ingestion_uploads.py -q`

Expected: FAIL with `ModuleNotFoundError: No module named 'xhbx_rag.web.ingestion_uploads'`。

- [ ] **Step 3: 实现公开数据契约与基础映射**

```python
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

IngestionTarget = Literal["case", "course"]


class UploadValidationError(ValueError):
    """上传格式、ZIP 安全或输入映射无效。"""


@dataclass(frozen=True)
class IngestionLimits:
    max_upload_bytes: int = 536_870_912
    max_zip_entries: int = 2_000
    max_extracted_bytes: int = 2_147_483_648
    max_entry_bytes: int = 536_870_912
    max_compression_ratio: float = 100.0
    max_path_chars: int = 512


@dataclass(frozen=True)
class PreflightItem:
    item_index: int
    unit_key: str
    display_name: str
    relative_paths: tuple[str, ...]
    document_count: int


@dataclass(frozen=True)
class PreflightResult:
    source_name: str
    source_kind: Literal["file", "zip"]
    target: IngestionTarget
    items: tuple[PreflightItem, ...]
    ignored_entries: tuple[str, ...] = field(default_factory=tuple)

    @property
    def document_total(self) -> int:
        return sum(item.document_count for item in self.items)

    @property
    def ignored_total(self) -> int:
        return len(self.ignored_entries)
```

Implementation rules: sort normalized paths lexicographically; assign `item_index` from 1; normalize `\\` to `/`; treat single file as one item; use the exact ignore rules and case/course mappings from the spec; reject no-supported-document and case display-name collisions.

- [ ] **Step 4: 写 ZIP 安全与容量失败测试**

```python
import stat
from zipfile import ZipInfo


@pytest.mark.parametrize(
    "entry",
    ["../secret.txt", "/etc/passwd", "C:/secret.txt", "safe/../../secret.txt"],
)
def test_zip_rejects_unsafe_paths(tmp_path: Path, entry: str) -> None:
    source = _zip(tmp_path / "bad.zip", {entry: b"bad"})
    with pytest.raises(UploadValidationError, match="ZIP 路径不安全"):
        preflight_upload(source, target="course", limits=IngestionLimits())


def test_zip_rejects_symlink(tmp_path: Path) -> None:
    source = tmp_path / "link.zip"
    info = ZipInfo("link.txt")
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with ZipFile(source, "w") as archive:
        archive.writestr(info, "target.txt")
    with pytest.raises(UploadValidationError, match="符号链接"):
        preflight_upload(source, target="course", limits=IngestionLimits())


def test_zip_rejects_declared_extracted_total(tmp_path: Path) -> None:
    source = _zip(tmp_path / "large.zip", {"course.txt": b"123456"})
    limits = IngestionLimits(max_extracted_bytes=5)
    with pytest.raises(UploadValidationError, match="解压后总大小"):
        preflight_upload(source, target="course", limits=limits)
```

- [ ] **Step 5: 实现流式保存、安全解压与幂等清理**

Public signatures must be exactly:

```text
async save_upload_file(upload: object, destination: Path, *, max_bytes: int, chunk_bytes: int = 1_048_576) -> int
preflight_upload(source_path: Path, *, target: IngestionTarget, limits: IngestionLimits) -> PreflightResult
materialize_attempt_inputs(source_path: Path, preflight: PreflightResult, attempt_dir: Path, *, limits: IngestionLimits) -> dict[int, Path]
clear_attempt_workspaces(job_dir: Path) -> None
delete_job_workspace(job_dir: Path) -> None
```

`save_upload_file()` writes `<name>.uploading`, counts bytes while reading, fsyncs, then `replace()`s; all failure paths unlink the temporary file. `materialize_attempt_inputs()` rechecks every ZIP limit during extraction and verifies `resolved_path.is_relative_to(extracted_root.resolve())` before writing.

- [ ] **Step 6: 运行上传安全测试**

Run: `uv run pytest tests/test_web_ingestion_uploads.py -q`

Expected: PASS，且无 warning。

- [ ] **Step 7: 提交**

```bash
git add src/xhbx_rag/web/ingestion_uploads.py tests/test_web_ingestion_uploads.py
git commit -m "feat: add secure ingestion upload preflight"
```

---

### Task 2: SQLite 入库任务 Store 与状态机

**Files:**
- Create: `src/xhbx_rag/web/ingestion_store.py`
- Create: `tests/test_web_ingestion_store.py`

**Interfaces:**
- Consumes: Task 1 `PreflightResult`。
- Produces: `IngestionStore`, `RecoveryAction` and all state transition methods used by Tasks 6–7。

- [ ] **Step 1: 写 draft、start、claim、fail、retry、delete 状态测试**

```python
from pathlib import Path

from xhbx_rag.web.ingestion_store import IngestionStore
from xhbx_rag.web.ingestion_uploads import PreflightItem, PreflightResult


def _store(tmp_path: Path) -> IngestionStore:
    return IngestionStore(tmp_path / "ingestion.sqlite3")


def _preflight() -> PreflightResult:
    return PreflightResult(
        source_name="cases.zip",
        source_kind="zip",
        target="case",
        items=(
            PreflightItem(1, "case-a", "案例A", ("case-a/a.txt",), 1),
            PreflightItem(2, "case-b", "案例B", ("case-b/b.txt",), 1),
        ),
    )


def test_job_lifecycle_requires_valid_state_transitions(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.create_draft(
        preflight=_preflight(),
        source_path=tmp_path / "jobs" / "job" / "source" / "cases.zip",
    )
    assert job["status"] == "draft"
    assert store.start_job(job["job_id"]) == "ok"
    assert store.start_job(job["job_id"]) == "conflict"
    assert store.claim_job(job["job_id"]) is True
    assert store.claim_job(job["job_id"]) is False
    store.fail_job(job["job_id"], code="parse_failed", detail="案例解析失败")
    retried = store.retry_job(job["job_id"])
    assert retried == {"result": "ok", "attempt_no": 2}
    assert store.get_job(job["job_id"])["status"] == "queued"


def test_delete_is_two_phase_and_idempotent(tmp_path: Path) -> None:
    store = _store(tmp_path)
    job = store.create_draft(preflight=_preflight(), source_path=tmp_path / "source.zip")
    assert store.begin_delete(job["job_id"]) == "ok"
    assert store.get_job(job["job_id"])["status"] == "deleting"
    assert store.begin_delete(job["job_id"]) == "ok"
    store.finish_delete(job["job_id"])
    assert store.get_job(job["job_id"]) is None
```

- [ ] **Step 2: 运行测试并确认失败**

Run: `uv run pytest tests/test_web_ingestion_store.py -q`

Expected: FAIL because `ingestion_store` does not exist。

- [ ] **Step 3: 实现 schema 与查询 shape**

Create four tables exactly named `ingestion_jobs`, `ingestion_items`, `ingestion_attempts`, `ingestion_events`. Use the columns and enum values from the spec. Required method surface:

```text
IngestionStore.__init__(db_path: Path | None = None) -> None
IngestionStore.create_draft(*, preflight: PreflightResult, source_path: Path) -> dict
IngestionStore.list_jobs(limit: int = 200) -> list[dict]
IngestionStore.get_job(job_id: str, *, include_events: bool = True) -> dict | None
IngestionStore.get_job_for_execution(job_id: str) -> dict | None
IngestionStore.get_progress(job_id: str) -> dict | None
IngestionStore.start_job(job_id: str) -> str
IngestionStore.claim_job(job_id: str) -> bool
IngestionStore.begin_attempt(job_id: str, workspace_path: Path) -> int
IngestionStore.mark_stage(job_id: str, stage: str, message: str) -> None
IngestionStore.mark_item_running(job_id: str, item_index: int, stage: str) -> bool
IngestionStore.complete_item(job_id: str, item_index: int, chunk_count: int, warning_count: int) -> None
IngestionStore.fail_item(job_id: str, item_index: int, detail: str) -> None
IngestionStore.mark_commit_state(job_id: str, state: str, journal_path: Path | None = None) -> None
IngestionStore.succeed_job(job_id: str, chunk_total: int, warning_count: int) -> None
IngestionStore.fail_job(job_id: str, *, code: str, detail: str) -> None
IngestionStore.mark_rolling_back(job_id: str, *, code: str, detail: str) -> None
IngestionStore.retry_job(job_id: str) -> dict[str, object]
IngestionStore.abort_retry(job_id: str, attempt_no: int, detail: str) -> None
IngestionStore.begin_delete(job_id: str) -> str
IngestionStore.finish_delete(job_id: str) -> None
IngestionStore.recovery_actions() -> list[RecoveryAction]
```

All read-modify-write methods use `BEGIN IMMEDIATE`. `create_draft()` inserts job and all preflight items in one transaction. `start_job()` creates queued attempt `#1`; `retry_job()` increments `attempt_count` and creates the next queued attempt; `begin_attempt()` marks that already-created current attempt running. `abort_retry()` is valid only while the matching new attempt is still queued: it removes that never-started attempt, restores `failed`, decrements `attempt_count`, and records the cleanup error. `get_job()` never includes `source_path`, `workspace_path`, or `journal_path` in its returned public dict; `get_job_for_execution()` is Runner-only and returns those internal paths plus all preflight items.

- [ ] **Step 4: 写重启恢复与事件上限测试**

```python
def test_recovery_actions_requeue_and_rollback_correct_jobs(tmp_path: Path) -> None:
    store = _store(tmp_path)
    queued = store.create_draft(preflight=_preflight(), source_path=tmp_path / "q.zip")
    store.start_job(queued["job_id"])
    running = store.create_draft(preflight=_preflight(), source_path=tmp_path / "r.zip")
    store.start_job(running["job_id"])
    store.claim_job(running["job_id"])
    store.begin_attempt(running["job_id"], tmp_path / "attempt")
    prepared = store.create_draft(preflight=_preflight(), source_path=tmp_path / "p.zip")
    store.start_job(prepared["job_id"])
    store.claim_job(prepared["job_id"])
    store.begin_attempt(prepared["job_id"], tmp_path / "prepared")
    store.mark_commit_state(prepared["job_id"], "prepared", tmp_path / "journal.json")

    actions = {(item.job_id, item.action) for item in store.recovery_actions()}

    assert (queued["job_id"], "enqueue") in actions
    assert (running["job_id"], "fail_interrupted") in actions
    assert (prepared["job_id"], "rollback") in actions
```

- [ ] **Step 5: 实现 attempt、事件与恢复动作**

Use immutable dataclass:

```python
@dataclass(frozen=True)
class RecoveryAction:
    job_id: str
    action: Literal["enqueue", "fail_interrupted", "rollback", "finish_committed", "delete"]
    journal_path: Path | None = None
```

Cap each attempt at 2000 events. After the cap, update one `progress_compacted` event instead of appending high-frequency item progress. Preserve milestone events.

- [ ] **Step 6: 运行 Store 测试**

Run: `uv run pytest tests/test_web_ingestion_store.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/xhbx_rag/web/ingestion_store.py tests/test_web_ingestion_store.py
git commit -m "feat: persist ingestion job lifecycle"
```

---

### Task 3: Milvus 回滚原语与 collection 写锁

**Files:**
- Create: `src/xhbx_rag/index_lock.py`
- Create: `tests/test_index_lock.py`
- Modify: `src/xhbx_rag/milvus_store.py`
- Modify: `src/xhbx_rag/indexer.py`
- Modify: `tests/test_milvus_store.py`
- Modify: `tests/test_indexer_search.py`

**Interfaces:**
- Produces: `collection_write_lock(uri, collection_name, lock_root=None)`, `MilvusStore.collection_exists()`, `fetch_raw_rows_by_ids()`, `delete_by_ids()`, `upsert_raw_rows()`, `flush()`。
- Consumers: Task 4 AtomicIndexer; existing CLI `index_chunks()` uses the same lock.

- [ ] **Step 1: 写 Milvus 原始行 round-trip 与删除测试**

```python
def test_store_fetches_raw_rows_with_vectors_and_restores_them(tmp_path) -> None:
    store = MilvusLiteStore(db_path=tmp_path / "rag.db", collection_name="chunks")
    original = MilvusChunkRecord.from_chunk(_chunk("same", "旧文本"), [0.1, 0.2])
    replacement = MilvusChunkRecord.from_chunk(_chunk("same", "新文本"), [0.9, 0.8])
    store.ensure_collection(2)
    store.upsert([original])

    snapshot = store.fetch_raw_rows_by_ids(["same"])
    store.upsert([replacement])
    store.delete_by_ids(["same"])
    store.upsert_raw_rows(list(snapshot.values()))
    store.flush()

    restored = store.fetch_raw_rows_by_ids(["same"])["same"]
    assert restored["text"] == "旧文本"
    assert restored["vector"] == pytest.approx([0.1, 0.2])
```

- [ ] **Step 2: 运行定向测试并确认缺少方法**

Run: `uv run pytest tests/test_milvus_store.py -q -k raw_rows`

Expected: FAIL with `AttributeError` for `fetch_raw_rows_by_ids`。

- [ ] **Step 3: 实现 Milvus 原语**

Add `_ROLLBACK_OUTPUT_FIELDS = [*_CHUNK_OUTPUT_FIELDS, "text_hash", "vector"]` and methods:

```python
def collection_exists(self) -> bool:
    return bool(self.client.has_collection(self.collection_name))


def fetch_raw_rows_by_ids(self, chunk_ids: list[str]) -> dict[str, dict[str, Any]]:
    if not chunk_ids or not self.collection_exists():
        return {}
    rows = self.client.query(
        collection_name=self.collection_name,
        filter=_chunk_id_filter_expr(chunk_ids),
        limit=len(chunk_ids),
        output_fields=_ROLLBACK_OUTPUT_FIELDS,
    )
    return {str(row["chunk_id"]): dict(row) for row in rows}


def delete_by_ids(self, chunk_ids: list[str]) -> None:
    if chunk_ids and self.collection_exists():
        self.client.delete(
            collection_name=self.collection_name,
            filter=_chunk_id_filter_expr(chunk_ids),
        )


def upsert_raw_rows(self, rows: list[dict[str, Any]]) -> None:
    if rows:
        self.client.upsert(collection_name=self.collection_name, data=rows)


def flush(self) -> None:
    if self.collection_exists():
        self.client.flush(self.collection_name)
```

Keep existing `upsert()` behavior unchanged for callers.

- [ ] **Step 4: 写跨进程锁序列化测试**

```python
import threading

from xhbx_rag.index_lock import collection_write_lock


def test_collection_write_lock_serializes_same_collection(tmp_path: Path) -> None:
    entered: list[str] = []
    first_ready = threading.Event()
    release_first = threading.Event()

    def first() -> None:
        with collection_write_lock("db", "chunks", lock_root=tmp_path):
            entered.append("first")
            first_ready.set()
            release_first.wait(timeout=5)

    def second() -> None:
        first_ready.wait(timeout=5)
        with collection_write_lock("db", "chunks", lock_root=tmp_path):
            entered.append("second")

    a = threading.Thread(target=first)
    b = threading.Thread(target=second)
    a.start()
    b.start()
    first_ready.wait(timeout=5)
    assert entered == ["first"]
    release_first.set()
    a.join(timeout=5)
    b.join(timeout=5)
    assert entered == ["first", "second"]
```

- [ ] **Step 5: 实现文件锁并接入现有 indexer**

`collection_write_lock()` hashes `f"{uri}\0{collection_name}"` with SHA-256 and locks `.local/index-locks/<hash>.lock` via `fcntl.flock(fd, LOCK_EX)`; always unlock/close in `finally`. Modify `index_chunks()` so collection drop/create/upsert occurs inside this lock when store exposes `uri` and `collection_name`; fake stores without attributes use `nullcontext()` to preserve existing unit tests.

- [ ] **Step 6: 运行 Milvus 与 indexer 回归**

Run: `uv run pytest tests/test_index_lock.py tests/test_milvus_store.py tests/test_indexer_search.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/xhbx_rag/index_lock.py src/xhbx_rag/milvus_store.py src/xhbx_rag/indexer.py tests/test_index_lock.py tests/test_milvus_store.py tests/test_indexer_search.py
git commit -m "feat: add reversible milvus write primitives"
```

---

### Task 4: 持久化 AtomicIndexer

**Files:**
- Create: `src/xhbx_rag/atomic_indexer.py`
- Create: `tests/test_atomic_indexer.py`

**Interfaces:**
- Consumes: Task 3 lock and Milvus raw primitives; existing `load_chunks_jsonl()` and `MilvusChunkRecord`。
- Produces: `AtomicIndexer.commit()`, `AtomicIndexer.recover()`, `AtomicIndexResult`, `AtomicIndexError`, `RollbackPendingError` used by Task 6。

- [ ] **Step 1: 写新记录与覆盖旧记录的回滚测试**

```python
from pathlib import Path

import pytest

from xhbx_rag.atomic_indexer import AtomicIndexer, RollbackPendingError


def test_commit_failure_deletes_new_rows_and_restores_old_rows(tmp_path: Path) -> None:
    store = FakeTransactionalStore(
        existing={"same": raw_row("same", "旧文本", [0.1, 0.2])},
        fail_after_upsert=True,
    )
    indexer = AtomicIndexer(
        embedding_client=FakeEmbedding([[0.9, 0.8], [0.7, 0.6]]),
        store=store,
    )
    chunks = write_chunks(tmp_path / "chunks.jsonl", [
        chunk("same", "新文本"),
        chunk("new", "新增文本"),
    ])

    with pytest.raises(Exception, match="flush failed"):
        indexer.commit(chunks, journal_dir=tmp_path / "rollback")

    assert store.rows["same"]["text"] == "旧文本"
    assert "new" not in store.rows
    assert read_journal(tmp_path / "rollback")["state"] == "rolled_back"
```

The test file defines complete in-memory `FakeTransactionalStore`, `FakeEmbedding`, `chunk()`, `raw_row()`, `write_chunks()`, and `read_journal()` helpers; no mocks of internal AtomicIndexer methods.

- [ ] **Step 2: 运行测试并确认模块不存在**

Run: `uv run pytest tests/test_atomic_indexer.py -q`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现 journal/snapshot 格式与 commit 主路径**

Required public contract:

```python
@dataclass(frozen=True)
class AtomicIndexResult:
    indexed: int
    vector_dim: int


class AtomicIndexError(RuntimeError):
    pass


class RollbackPendingError(AtomicIndexError):
    def __init__(self, journal_path: Path, detail: str) -> None:
        super().__init__(detail)
        self.journal_path = journal_path


```

Required method signatures:

```text
AtomicIndexer.__init__(*, embedding_client: object, store: object) -> None
AtomicIndexer.commit(chunks_path: Path, *, journal_dir: Path, on_state: Callable[[str, Path], None] | None = None) -> AtomicIndexResult
AtomicIndexer.recover(journal_path: Path) -> None
```

Persist `snapshot.jsonl` with one raw row per line and `journal.json` containing version `1`, collection identity, `collection_existed`, sorted `chunk_ids`, `snapshot_path`, and state. Write temp + fsync + replace for both files. Call `on_state("prepared", journal_path)` only after both are durable; call `on_state("committed", journal_path)` only after write verification.

- [ ] **Step 4: 写崩溃恢复、原 collection 不存在与 rollback pending 测试**

```python
def test_recover_prepared_journal_rolls_back(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={"same": raw_row("same", "新文本", [0.9, 0.8])})
    journal = write_prepared_journal(
        tmp_path / "rollback",
        chunk_ids=["same", "new"],
        old_rows=[raw_row("same", "旧文本", [0.1, 0.2])],
        collection_existed=True,
    )
    AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)
    assert store.rows["same"]["text"] == "旧文本"
    assert "new" not in store.rows
    assert read_journal(journal.parent)["state"] == "rolled_back"


def test_rollback_failure_raises_pending_and_keeps_journal(tmp_path: Path) -> None:
    store = FakeTransactionalStore(existing={}, fail_delete=True)
    journal = write_prepared_journal(
        tmp_path / "rollback",
        chunk_ids=["new"],
        old_rows=[],
        collection_existed=True,
    )
    with pytest.raises(RollbackPendingError):
        AtomicIndexer(embedding_client=FakeEmbedding([]), store=store).recover(journal)
    assert read_journal(journal.parent)["state"] == "rolling_back"
```

- [ ] **Step 5: 实现补偿、写后校验与 committed 恢复**

Commit order: load/validate chunks → embed all → lock → fetch old rows → durable prepared journal → ensure collection → one upsert → flush → fetch IDs and compare set → durable committed journal → return. On any exception after prepared, call rollback under the held lock. `recover()` returns immediately for `rolled_back`; for `committed`, it verifies IDs exist and returns without rollback; for `prepared/rolling_back`, it performs compensation.

- [ ] **Step 6: 运行 AtomicIndexer 测试与相关回归**

Run: `uv run pytest tests/test_atomic_indexer.py tests/test_milvus_store.py tests/test_indexer_search.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/xhbx_rag/atomic_indexer.py tests/test_atomic_indexer.py
git commit -m "feat: add atomic ingestion index commits"
```

---

### Task 5: 严格案例/课程加工与 staging 校验

**Files:**
- Create: `src/xhbx_rag/web/ingestion_pipeline.py`
- Create: `tests/test_web_ingestion_pipeline.py`
- Modify: `src/xhbx_rag/course_parser.py`
- Modify: `tests/test_course_parser.py`

**Interfaces:**
- Consumes: Task 1 materialized input paths; existing case generation/parser/chunk builder and course parser。
- Produces: `PreparedIngestion`, `IngestionPipeline.prepare()` used by Task 6。

- [ ] **Step 1: 为课程解析器写 fail-fast 兼容测试**

```python
def test_parse_course_dir_fail_fast_raises_on_supported_file_failure(tmp_path) -> None:
    course_dir = tmp_path / "培训数据"
    _write_pptx(course_dir / "好课件.pptx", ["销售流程" * 50])
    (course_dir / "坏课件.pptx").write_bytes(b"broken")

    with pytest.raises(CourseFileParseError, match="坏课件.pptx"):
        parse_course_dir(course_dir, tmp_path / "out", fail_fast=True)


def test_parse_course_dir_default_still_isolates_file_failure(tmp_path) -> None:
    course_dir = tmp_path / "培训数据"
    _write_pptx(course_dir / "好课件.pptx", ["销售流程" * 50])
    (course_dir / "坏课件.pptx").write_bytes(b"broken")
    report = parse_course_dir(course_dir, tmp_path / "out")
    assert report.counts["files_parsed"] == 1
    assert report.counts["files_failed"] == 1
```

- [ ] **Step 2: 实现 `CourseFileParseError`、`fail_fast=False` 和进度回调**

Extend signature without breaking CLI:

```python
def parse_course_dir(
    course_dir: Path,
    out_dir: Path,
    enrichment_agent: CourseEnrichmentAgent | None = None,
    trace: TraceSink | None = None,
    *,
    fail_fast: bool = False,
    on_file: Callable[[str, str, int], None] | None = None,
) -> CourseParseReport:
```

When a supported file fails and `fail_fast=True`, write no final `chunks.jsonl`, call `on_file(relative, "failed", 0)`, and raise `CourseFileParseError(relative, safe_detail)`. Enrichment exceptions remain in `enrich_failures` and do not raise.

- [ ] **Step 3: 写案例 partial、课程 warning 与全批校验测试**

```python
def test_case_partial_generation_fails_entire_prepare(tmp_path: Path) -> None:
    pipeline = pipeline_with_case_result(status="partial")
    with pytest.raises(IngestionPipelineError, match="案例洞察生成不完整"):
        pipeline.prepare(case_job(tmp_path), tmp_path / "attempt")
    assert not (tmp_path / "attempt" / "staging" / "chunks.jsonl").exists()


def test_course_enrichment_warning_keeps_prepare_successful(tmp_path: Path) -> None:
    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("course__a__0001")],
        enrich_failures=["促成课: 模型不可用"],
    )
    prepared = pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")
    assert prepared.chunk_count == 1
    assert prepared.warnings == ("促成课: 模型不可用",)


def test_duplicate_chunk_ids_fail_before_index(tmp_path: Path) -> None:
    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("duplicate"), course_chunk("duplicate")],
        enrich_failures=[],
    )
    with pytest.raises(IngestionPipelineError, match="chunk_id 重复"):
        pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")
```

The test file must define complete `pipeline_with_case_result()`, `pipeline_with_course_report()`, `case_job()`, `course_job()`, and `course_chunk()` fakes/builders. They return real `RagChunk` objects and write real staging files; only external LLM/course parsing boundaries are faked.

- [ ] **Step 4: 实现 Pipeline 依赖注入与 staging**

```python
@dataclass(frozen=True)
class PreparedIngestion:
    chunks_path: Path
    chunk_count: int
    warning_count: int
    warnings: tuple[str, ...]


class IngestionPipelineError(RuntimeError):
    def __init__(self, code: str, detail: str, item_index: int | None = None) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail
        self.item_index = item_index


```

Required method signature:

```text
IngestionPipeline.prepare(job: Mapping[str, object], attempt_dir: Path, *, on_event: Callable[[str, dict[str, object]], None] | None = None) -> PreparedIngestion
```

Production constructor accepts config provider, case generation factory, course enrichment factory, and upload materializer as injectable callables. Case items run in item order and reject status other than `ok`. Course path calls `parse_course_dir(course_dir, out_dir, enrichment_agent, trace, fail_fast=True, on_file=callback)`. Merge only after all items succeed; validate non-empty text, UTF-8, unique IDs, target chunk types, JSON serialization and Milvus field byte limits.

- [ ] **Step 5: 运行课程与 Pipeline 测试**

Run: `uv run pytest tests/test_course_parser.py tests/test_web_ingestion_pipeline.py -q`

Expected: PASS，现有宽松课程 CLI 测试不变。

- [ ] **Step 6: 提交**

```bash
git add src/xhbx_rag/course_parser.py src/xhbx_rag/web/ingestion_pipeline.py tests/test_course_parser.py tests/test_web_ingestion_pipeline.py
git commit -m "feat: prepare strict ingestion artifacts"
```

---

### Task 6: IngestionRunner、失败清理与重启恢复

**Files:**
- Create: `src/xhbx_rag/web/ingestion_runner.py`
- Create: `tests/test_web_ingestion_runner.py`
- Modify: `src/xhbx_rag/web/safe_errors.py`

**Interfaces:**
- Consumes: Task 2 Store, Task 4 AtomicIndexer, Task 5 Pipeline, Task 1 cleanup。
- Produces: `IngestionRunner.start/stop/enqueue/execute_job/execute_recovery/recover_after_restart` used by Task 7 app lifespan。

- [ ] **Step 1: 写成功、解析失败与 rollback pending Runner 测试**

```python
def test_runner_prepares_commits_and_marks_success(tmp_path: Path) -> None:
    store, job_id = queued_store(tmp_path)
    pipeline = FakePipeline(prepared=prepared_ingestion(tmp_path, chunk_count=3))
    indexer = FakeAtomicIndexer(result=AtomicIndexResult(indexed=3, vector_dim=2))
    runner = IngestionRunner(store=store, pipeline=pipeline, indexer_factory=lambda target: indexer)

    runner.execute_job(job_id)

    job = store.get_job(job_id)
    assert job["status"] == "succeeded"
    assert job["chunk_total"] == 3
    assert pipeline.calls == [job_id]
    assert indexer.commit_calls == 1


def test_runner_pipeline_failure_never_calls_indexer_and_cleans_attempt(tmp_path: Path) -> None:
    store, job_id = queued_store(tmp_path)
    pipeline = FakePipeline(error=IngestionPipelineError("parse_failed", "案例解析失败", 1))
    indexer = FakeAtomicIndexer()
    runner = IngestionRunner(store=store, pipeline=pipeline, indexer_factory=lambda target: indexer)

    runner.execute_job(job_id)

    assert store.get_job(job_id)["status"] == "failed"
    assert indexer.commit_calls == 0
    assert not attempt_dir(store, job_id).exists()


def test_runner_keeps_rollback_workspace_when_compensation_is_pending(tmp_path: Path) -> None:
    store, job_id = queued_store(tmp_path)
    pipeline = FakePipeline(prepared=prepared_ingestion(tmp_path, chunk_count=1))
    indexer = FakeAtomicIndexer(error=RollbackPendingError(tmp_path / "journal.json", "Milvus 不可用"))
    runner = IngestionRunner(store=store, pipeline=pipeline, indexer_factory=lambda target: indexer)

    runner.execute_job(job_id)

    assert store.get_job(job_id)["status"] == "rolling_back"
    assert attempt_dir(store, job_id).exists()
```

The test file must define complete `queued_store()`, `FakePipeline`, `FakeAtomicIndexer`, `prepared_ingestion()`, and `attempt_dir()` helpers backed by the real Task 2 SQLite Store. Do not mock Store state transitions.

- [ ] **Step 2: 运行测试并确认模块不存在**

Run: `uv run pytest tests/test_web_ingestion_runner.py -q`

Expected: FAIL with `ModuleNotFoundError`。

- [ ] **Step 3: 实现队列生命周期和同步 `execute_job()`**

Mirror `BatchRunner` lifecycle: daemon thread, `queue.Queue[str | None]`, idempotent start/stop, 10-second join. `execute_job()` order is claim → begin attempt → pipeline.prepare → mark indexing → AtomicIndexer.commit with Store state callback → succeed → cleanup. Pre-commit failures call safe error mapping, mark failed, then clear attempt workspace.

- [ ] **Step 4: 写重启恢复和 2–60 秒退避测试**

```python
def test_execute_recovery_retries_and_finishes_rollback(tmp_path: Path) -> None:
    store = recovery_store_with_queued_and_prepared(tmp_path)
    sleeps: list[float] = []
    indexer = FakeAtomicIndexer(recover_failures=2)
    runner = IngestionRunner(
        store=store,
        pipeline=FakePipeline(),
        indexer_factory=lambda target: indexer,
        sleep_fn=sleeps.append,
    )

    runner.execute_recovery("prepared")

    assert sleeps == [2.0, 4.0]
    assert store.get_job("prepared")["status"] == "failed"
```

- [ ] **Step 5: 实现恢复动作与安全错误**

`recover_after_restart()` must not block FastAPI startup. It consumes Task 2 `RecoveryAction` by immediately cleaning/failing non-commit interruptions and placing queued, rollback, finish-committed, and deleting actions onto the Runner queue. `execute_recovery(job_id)` is the synchronous test seam: recover prepared/rolling_back with delay `min(2 * 2**attempt, 60)` until success or stop event; mark committed journal succeeded; finish deleting. The worker accepts typed job/recovery queue entries and processes them serially. `safe_errors.py` must map codes `upload_invalid`, `upload_too_large`, `parse_failed`, `chunk_failed`, `embedding_failed`, `index_failed`, `rollback_pending`, `service_restarted`, `storage_unavailable` to fixed Chinese details without interpolating raw paths or secrets.

- [ ] **Step 6: 运行 Runner 和 Store 测试**

Run: `uv run pytest tests/test_web_ingestion_store.py tests/test_web_ingestion_runner.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/xhbx_rag/web/ingestion_runner.py src/xhbx_rag/web/safe_errors.py tests/test_web_ingestion_runner.py
git commit -m "feat: run and recover ingestion jobs"
```

---

### Task 7: FastAPI 路由、lifespan、配置与部署

**Files:**
- Create: `src/xhbx_rag/web/ingestion_routes.py`
- Create: `tests/test_web_ingestion_routes.py`
- Modify: `src/xhbx_rag/web/app.py`
- Modify: `tests/test_web_app.py`
- Modify: `src/xhbx_rag/config.py`
- Modify: `tests/test_config.py`
- Modify: `pyproject.toml`
- Modify: `uv.lock`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `web/nginx.conf`
- Modify: `tests/test_docker_deployment.py`

**Interfaces:**
- Consumes: Tasks 1, 2, 6。
- Produces: REST API consumed by Task 8 frontend。

- [ ] **Step 1: 写 multipart 创建、start/list/detail/progress/retry/delete API 测试**

```python
def test_create_ingestion_job_returns_draft_preflight(tmp_path: Path) -> None:
    client, store, runner = make_client(tmp_path)
    response = client.post(
        "/api/ingestion-jobs",
        data={"target": "course"},
        files={"file": ("course.txt", b"课程内容", "text/plain")},
    )
    assert response.status_code == 201
    body = response.json()
    assert body["status"] == "draft"
    assert body["target"] == "course"
    assert body["item_total"] == 1
    assert body["document_total"] == 1
    assert "source_path" not in body
    assert runner.enqueued == []


def test_start_commits_before_enqueue(tmp_path: Path) -> None:
    client, store, runner = make_client(tmp_path)
    job_id = create_draft(client)
    response = client.post(f"/api/ingestion-jobs/{job_id}/start")
    assert response.status_code == 200
    assert store.get_job(job_id)["status"] == "queued"
    assert runner.enqueued == [job_id]


def test_retry_and_delete_enforce_state_conflicts(tmp_path: Path) -> None:
    client, store, runner = make_client(tmp_path)
    job_id = create_draft(client)
    assert client.post(f"/api/ingestion-jobs/{job_id}/retry").status_code == 409
    client.post(f"/api/ingestion-jobs/{job_id}/start")
    assert client.delete(f"/api/ingestion-jobs/{job_id}").status_code == 409
```

The test file must define complete `make_client()` and `create_draft()` helpers. `make_client()` injects a real temporary `IngestionStore`, a fake upload workspace root, and a fake Runner that records enqueued job IDs while implementing `start/stop/recover_after_restart` for lifespan compatibility.

- [ ] **Step 2: 运行路由测试并确认 404/模块缺失**

Run: `uv run pytest tests/test_web_ingestion_routes.py -q`

Expected: FAIL because routes are not registered。

- [ ] **Step 3: 实现路由与 Pydantic/Form 校验**

Endpoints and statuses must match the spec exactly:

```text
POST   /api/ingestion-jobs                 201
POST   /api/ingestion-jobs/{job_id}/start 200
GET    /api/ingestion-jobs                200
GET    /api/ingestion-jobs/{job_id}       200/404
GET    /api/ingestion-jobs/{job_id}/progress 200/404
POST   /api/ingestion-jobs/{job_id}/retry 200/404/409/500
DELETE /api/ingestion-jobs/{job_id}       200/404/409/500
```

Use `Annotated[UploadFile, File()]` and `Annotated[Literal["case", "course"], Form()]`. Route catches `UploadValidationError` as 400, byte overflow as 413, SQLite errors as fixed 500. Create failure cleans temporary/job directory and does not persist draft.

Retry order is exact: call `store.retry_job()` to reserve the next attempt; synchronously delete every non-source attempt workspace; if cleanup fails call `store.abort_retry(job_id, attempt_no, "无法清理旧任务产物")` and return fixed 500; only after cleanup succeeds enqueue the job. This prevents a cleanup failure from leaving a queued task.

- [ ] **Step 4: 修改 `create_app()` 注入与 lifespan**

Signature becomes:

```python
def create_app(
    batch_store: Any | None = None,
    batch_runner: Any | None = None,
    ingestion_store: Any | None = None,
    ingestion_runner: Any | None = None,
) -> FastAPI:
```

Initialize batch and ingestion subsystems in separate `try` blocks so either can degrade independently. For ingestion: create Store → Pipeline/AtomicIndexer factory Runner → `runner.recover_after_restart()` → `runner.start()`. Injected doubles go directly to `app.state`. Stop both runners independently in `finally`. Include `ingestion_router` and keep existing routes unchanged.

- [ ] **Step 5: 添加配置、依赖和部署失败测试**

```python
def test_ingestion_limits_use_defaults_and_clamp_invalid_values() -> None:
    limits = ingestion_limits_from_env({})
    assert limits.max_upload_bytes == 536_870_912
    assert limits.max_zip_entries == 2_000
    assert limits.max_extracted_bytes == 2_147_483_648
    with pytest.raises(ConfigError, match="WEB_INGEST_MAX_UPLOAD_BYTES"):
        ingestion_limits_from_env({"WEB_INGEST_MAX_UPLOAD_BYTES": "0"})
```

Add `python-multipart>=0.0.20` then run `uv lock`. Set `client_max_body_size 512m;`. Add the five environment defaults from the spec to `.env.example` and docker compose. Extend deployment tests to assert `.local` persistence and Nginx limit.

- [ ] **Step 6: 运行后端 API、配置和部署测试**

Run: `uv run pytest tests/test_web_ingestion_routes.py tests/test_web_app.py tests/test_config.py tests/test_docker_deployment.py -q`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add src/xhbx_rag/web/ingestion_routes.py src/xhbx_rag/web/app.py src/xhbx_rag/config.py tests/test_web_ingestion_routes.py tests/test_web_app.py tests/test_config.py tests/test_docker_deployment.py pyproject.toml uv.lock .env.example docker-compose.yml web/nginx.conf
git commit -m "feat: expose ingestion job api"
```

---

### Task 8: 前端类型、API、XHR 上传、URL 与轮询

**Files:**
- Create: `web/src/ingestion.ts`
- Create: `web/src/ingestion.test.ts`
- Create: `web/src/workspaceLocation.ts`
- Create: `web/src/workspaceLocation.test.ts`
- Create: `web/src/ingestionUpload.ts`
- Create: `web/src/ingestionUpload.test.ts`
- Create: `web/src/hooks/useIngestionJobPolling.ts`
- Create: `web/src/hooks/useIngestionJobPolling.test.tsx`
- Modify: `web/src/types.ts`
- Modify: `web/src/api.ts`
- Modify: `web/src/api.test.ts`

**Interfaces:**
- Consumes: Task 7 response shapes。
- Produces: typed client and hooks used by Task 9 components。

- [ ] **Step 1: 写状态纯函数与 URL 同步测试**

```typescript
test("ingestion status labels and active state are stable", () => {
  expect(ingestionStatusLabel("draft")).toBe("待确认");
  expect(ingestionStatusLabel("rolling_back")).toBe("清理中");
  expect(ingestionStatusLabel("deleting")).toBe("删除中");
  expect(isIngestionJobActive("queued")).toBe(true);
  expect(isIngestionJobActive("succeeded")).toBe(false);
});

test("workspace location round trips ingestion job selection", () => {
  expect(parseWorkspaceLocation("?view=ingestion&job=job-1")).toEqual({
    view: "ingestion",
    jobId: "job-1"
  });
  expect(workspaceSearch({ view: "ingestion", jobId: "job-1" })).toBe(
    "?view=ingestion&job=job-1"
  );
});
```

The tests define complete `ingestionDraftPayload()` and `progressPayload()` builders whose fields satisfy the Task 8 TypeScript types; do not use `as unknown as` casts.

- [ ] **Step 2: 运行纯函数测试并确认模块不存在**

Run: `cd web && npm test -- src/ingestion.test.ts src/workspaceLocation.test.ts`

Expected: FAIL with import resolution errors。

- [ ] **Step 3: 添加完整 TypeScript 类型与 API 函数**

Define exact unions:

```typescript
export type IngestionTarget = "case" | "course";
export type IngestionJobStatus =
  | "draft"
  | "queued"
  | "running"
  | "rolling_back"
  | "succeeded"
  | "failed"
  | "deleting";
export type IngestionStage =
  | "uploaded"
  | "parsing"
  | "chunking"
  | "indexing"
  | "completed";
```

Add `IngestionPreflightItem`, `IngestionJobSummary`, `IngestionJobDetail`, `IngestionJobProgress`, `IngestionEvent` matching API JSON. Add `listIngestionJobs`, `getIngestionJob`, `getIngestionJobProgress`, `startIngestionJob`, `retryIngestionJob`, `deleteIngestionJob` to `api.ts` using existing `requestJson()` and `ApiError`.

- [ ] **Step 4: 写并实现 XHR 上传进度测试**

```typescript
test("uploadIngestionJob sends multipart and reports progress", async () => {
  const xhr = new FakeXMLHttpRequest();
  const progress: number[] = [];
  const promise = uploadIngestionJob(
    new File(["课程"], "课程.txt", { type: "text/plain" }),
    "course",
    { xhrFactory: () => xhr, onProgress: (value) => progress.push(value) }
  );
  xhr.emitProgress(5, 10);
  xhr.resolve(201, ingestionDraftPayload());
  await expect(promise).resolves.toMatchObject({ status: "draft" });
  expect(progress).toEqual([50]);
  expect(xhr.formData.get("target")).toBe("course");
  expect(xhr.formData.get("file")).toBeInstanceOf(File);
});
```

`uploadIngestionJob()` must clamp progress 0–100, parse API error `detail`, support abort, and expose no manual `Content-Type` header so the browser sets the multipart boundary.

- [ ] **Step 5: 写并实现终态停止轮询 hook 测试**

```typescript
test("polls active job and stops after terminal status", async () => {
  const fetchProgress = vi
    .fn()
    .mockResolvedValueOnce(progressPayload({ status: "running" }))
    .mockResolvedValueOnce(progressPayload({ status: "succeeded" }));
  const { result } = renderHook(() =>
    useIngestionJobPolling("job-1", { intervalMs: 20, fetchProgress })
  );
  await waitFor(() => expect(result.current.progress?.status).toBe("succeeded"));
  const count = fetchProgress.mock.calls.length;
  await new Promise((resolve) => setTimeout(resolve, 60));
  expect(fetchProgress).toHaveBeenCalledTimes(count);
});
```

- [ ] **Step 6: 运行前端数据层测试**

Run: `cd web && npm test -- src/ingestion.test.ts src/workspaceLocation.test.ts src/ingestionUpload.test.ts src/hooks/useIngestionJobPolling.test.tsx src/api.test.ts`

Expected: PASS。

- [ ] **Step 7: 提交**

```bash
git add web/src/ingestion.ts web/src/ingestion.test.ts web/src/workspaceLocation.ts web/src/workspaceLocation.test.ts web/src/ingestionUpload.ts web/src/ingestionUpload.test.ts web/src/hooks/useIngestionJobPolling.ts web/src/hooks/useIngestionJobPolling.test.tsx web/src/types.ts web/src/api.ts web/src/api.test.ts
git commit -m "feat: add ingestion frontend data layer"
```

---

### Task 9: 三栏入库工作台 UI 与响应式集成

**Files:**
- Create: `web/src/components/WorkspaceNav.tsx`
- Create: `web/src/components/IngestionSidebar.tsx`
- Create: `web/src/components/IngestionCreateView.tsx`
- Create: `web/src/components/IngestionRunView.tsx`
- Create: `web/src/components/IngestionDetailPanel.tsx`
- Create: `web/src/App.ingestion.test.tsx`
- Modify: `web/src/App.tsx`
- Modify: `web/src/components/SessionSidebar.tsx`
- Modify: `web/src/styles.css`
- Modify: `web/src/test-utils.tsx`

**Interfaces:**
- Consumes: Task 8 types/client/hooks/location。
- Produces: confirmed three-column ingestion UX without regressing chat/batch views。

- [ ] **Step 1: 写顶层导航、上传预检与确认开始 UI 测试**

```typescript
test("opens ingestion workspace, uploads, previews, and starts a draft", async () => {
  const user = userEvent.setup();
  installIngestionApiStub({ draft: ingestionDraftPayload(), jobs: [] });
  render(<App />);

  await user.click(screen.getByRole("button", { name: "文档入库" }));
  expect(window.location.search).toBe("?view=ingestion");
  await user.click(screen.getByRole("radio", { name: "案例知识库" }));
  await user.upload(
    screen.getByLabelText("上传文档或 ZIP"),
    new File(["content"], "优秀案例.zip", { type: "application/zip" })
  );

  expect(await screen.findByText("识别到 3 个案例")).toBeInTheDocument();
  expect(screen.getByText("王女士年金险案例")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "确认并开始" }));
  expect(await screen.findByText("排队中")).toBeInTheDocument();
});
```

- [ ] **Step 2: 写失败、rolling_back、重试和删除测试**

```typescript
test("failed job explains no write and retries from scratch", async () => {
  const user = userEvent.setup();
  installIngestionApiStub({
    jobs: [ingestionSummary({ status: "failed" })],
    detail: ingestionDetail({
      status: "failed",
      error_detail: "案例解析失败",
      item_done: 0,
      chunk_total: 0
    })
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);

  expect(await screen.findByText("任务未写入知识库")).toBeInTheDocument();
  expect(screen.getByText("案例解析失败")).toBeInTheDocument();
  await user.click(screen.getByRole("button", { name: "从头重试" }));
  expect(await screen.findByText("排队中")).toBeInTheDocument();
});

test("rolling back disables retry and delete", async () => {
  installIngestionApiStub({
    jobs: [ingestionSummary({ status: "rolling_back" })],
    detail: ingestionDetail({ status: "rolling_back" })
  });
  window.history.replaceState(null, "", "/?view=ingestion&job=job-1");
  render(<App />);
  expect(await screen.findByText("正在恢复知识库，请勿重试或删除")).toBeInTheDocument();
  expect(screen.getByRole("button", { name: "从头重试" })).toBeDisabled();
  expect(screen.getByRole("button", { name: "删除任务" })).toBeDisabled();
});
```

`web/src/test-utils.tsx` must provide complete `installIngestionApiStub()`, `ingestionDraftPayload()`, `ingestionSummary()`, and `ingestionDetail()` helpers. The stub handles every ingestion endpoint and records method/body; it must keep existing chat/batch default responses unchanged.

- [ ] **Step 3: 运行 UI 测试并确认组件不存在**

Run: `cd web && npm test -- src/App.ingestion.test.tsx`

Expected: FAIL because ingestion workspace components are missing。

- [ ] **Step 4: 实现顶层壳层和五个组件**

`App.tsx` reads `WorkspaceLocation`; in chat mode render current `SessionSidebar/ChatView/source-panel`; in ingestion mode render `IngestionSidebar`, create or run view, and `IngestionDetailPanel`. Keep collection selection/chat state mounted only as needed without resetting localStorage. `WorkspaceNav` uses `MessageSquareText` and `FileUp` Lucide icons with text.

`IngestionCreateView` requirements: visible target radio group; drop zone backed by labeled file input; accept exact extensions; uploading button disabled; progress bar; preflight items and ignored entries; one primary CTA “确认并开始”; `role=alert` errors; reset same-file input value after selection.

`IngestionRunView` requirements: four fixed stages; `aria-current="step"`; status text + icon; item list; success warnings; failure “任务未写入知识库”; retry and delete rules; confirmation dialog for delete using semantic `<dialog>` when supported and accessible fallback component in jsdom.

- [ ] **Step 5: 实现响应式样式和 reduced motion**

Add semantic ingestion CSS classes using existing tokens. Breakpoints exactly `1180px` and `768px`. Desktop three columns; tablet right detail becomes an in-flow section after main; mobile stacks nav/task list/main/detail. All controls min-height 44px. Add:

```css
@media (prefers-reduced-motion: reduce) {
  *,
  *::before,
  *::after {
    scroll-behavior: auto !important;
    transition-duration: 0.01ms !important;
    animation-duration: 0.01ms !important;
    animation-iteration-count: 1 !important;
  }
}
```

- [ ] **Step 6: 运行 UI 与现有前端回归**

Run: `cd web && npm test -- src/App.ingestion.test.tsx src/App.chat.test.tsx src/App.batch.test.tsx src/SessionSidebar.test.tsx`

Expected: PASS，无 act warning。

- [ ] **Step 7: 构建前端**

Run: `cd web && npm run build`

Expected: TypeScript and Vite build exit 0。

- [ ] **Step 8: 提交**

```bash
git add web/src/components/WorkspaceNav.tsx web/src/components/IngestionSidebar.tsx web/src/components/IngestionCreateView.tsx web/src/components/IngestionRunView.tsx web/src/components/IngestionDetailPanel.tsx web/src/App.ingestion.test.tsx web/src/App.tsx web/src/components/SessionSidebar.tsx web/src/styles.css web/src/test-utils.tsx
git commit -m "feat: add document ingestion workspace"
```

---

### Task 10: 文档、完整回归与浏览器验收

**Files:**
- Modify: `README.md`
- Modify: `.env.example`
- Verify: all production and test files from Tasks 1–9

**Interfaces:**
- Consumes: completed backend and frontend feature。
- Produces: operator instructions and final verified deliverable。

- [ ] **Step 1: 写文档回归断言**

Extend `tests/test_docker_deployment.py` with exact assertions:

```python
def test_web_ingestion_docs_and_proxy_limits_are_documented() -> None:
    readme = read_repo_file("README.md")
    env_template = read_repo_file(".env.example")
    nginx = read_repo_file("web/nginx.conf")
    assert "Web 文档入库" in readme
    assert "ZIP 一级子目录" in readme
    assert "失败任务从头重试" in readme
    assert "WEB_INGEST_MAX_UPLOAD_BYTES=536870912" in env_template
    assert "WEB_INGEST_MAX_ZIP_ENTRIES=2000" in env_template
    assert "client_max_body_size 512m;" in nginx
```

- [ ] **Step 2: 运行文档测试并确认因 README 缺少内容而失败**

Run: `uv run pytest tests/test_docker_deployment.py -q -k ingestion`

Expected: FAIL on missing `Web 文档入库` README content。

- [ ] **Step 3: 更新 README 和 `.env.example`**

README section must contain: supported formats; target selection; single-file and ZIP case/course mapping; four stages; all-or-nothing guarantee; course enrichment warning exception; retry cleanup; task deletion does not undo knowledge; five limits; `.local/web_ingestion` persistence; single-writer deployment constraint; start commands. Use the exact env names/defaults from the spec.

- [ ] **Step 4: 运行完整 Python 测试**

Run: `uv run pytest -q`

Expected: all tests PASS, no unhandled thread exception or resource warning。

- [ ] **Step 5: 运行完整前端测试**

Run: `cd web && npm test`

Expected: all Vitest suites PASS, no act warning。

- [ ] **Step 6: 运行前端生产构建**

Run: `cd web && npm run build`

Expected: exit 0 and assets generated under ignored `web/dist/`。

- [ ] **Step 7: 运行静态检查**

Run: `git diff --check`

Expected: no output, exit 0。

- [ ] **Step 8: 浏览器验收**

Start API and Web dev servers. Verify at 1440px, 1024px, 768px, and 375px:

1. URL forward/back switches chat and ingestion without losing state.
2. Keyboard can choose target, select file, confirm, retry, and delete.
3. Single course file and ZIP preflight show exact item/document/ignored counts.
4. Active task refresh restores selected job and stage.
5. Injected failure shows “任务未写入知识库”; rolling_back disables retry/delete.
6. No horizontal overflow; focus ring visible; reduced-motion disables nonessential transitions.

- [ ] **Step 9: 提交文档与最终验证修正**

```bash
git add README.md .env.example tests/test_docker_deployment.py
git commit -m "docs: document web ingestion workflow"
```

---

## Plan Self-Review

### Spec coverage

- 单文件/ZIP、case/course 映射：Task 1。
- ZIP 安全、容量限制、源文件生命周期：Tasks 1 and 7。
- SQLite 历史、状态机、attempt、事件、重启恢复：Tasks 2 and 6。
- 全批 embedding、旧记录快照、补偿恢复、CLI 互斥：Tasks 3 and 4。
- 完整案例管线、课程 fail-fast、课程增值 warning：Task 5。
- 后台 Runner、清理、退避和 rollback pending：Task 6。
- REST API、multipart、lifespan、Docker/Nginx：Task 7。
- URL、XHR、轮询、三栏 UI、可访问性和响应式：Tasks 8 and 9。
- 运维说明、完整测试、构建和浏览器验收：Task 10。

### Interface consistency

- Task 1 `PreflightResult` is persisted by Task 2 and materialized by Task 5.
- Task 2 status values exactly match Task 8 TypeScript unions.
- Task 3 Milvus raw primitives exactly satisfy Task 4 AtomicIndexer.
- Task 4 exposes `commit/recover`; Task 6 is the only Web lifecycle caller.
- Task 5 exposes `PreparedIngestion.chunks_path/chunk_count/warnings`; Task 6 passes those into Task 4 and Store.
- Task 6 exposes lifecycle methods consumed by Task 7 `create_app()`.
- Task 7 endpoints exactly match Task 8 typed API paths.
- Task 8 types and hooks are the only backend-shape dependency used by Task 9 components.

### Placeholder scan

Every production behavior, state transition, default, path, API endpoint, test command, error policy and acceptance criterion is specified in prose or executable test code. The plan contains no production placeholder body, deferred decision, or undefined interface.
