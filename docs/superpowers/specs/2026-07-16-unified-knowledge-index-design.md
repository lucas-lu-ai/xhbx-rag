# 统一知识库一级标签与本地批量入库设计

日期：2026-07-16

## 1. 目标

把 `parsed/` 下现有绩优案例 chunk 和培训资料 chunk 统一规范化并写入一个 Milvus collection，同时只使用 Excel 知识体系的一级标签作为业务分类元数据。

本次改造必须满足：

- 不重新解析原始 PPT、PDF、DOCX，也不改变现有 chunk 边界。
- 递归处理 `parsed/` 下全部 1,078 个 chunk JSONL 文件。
- `parsed/chunk/` 下的 977 个 `*.chunks.jsonl` 固定识别为“培训资料”。
- `parsed/` 其他子目录下的 101 个 `chunks.jsonl` 固定识别为“绩优案例”。
- 两类数据进入同一个 collection。
- 只生成一级领域标签，不生成二级、三级标签路径。
- 本地命令先规范化和输出检查报告，人工可审查后再执行 embedding 与入库。
- 全量校验必须在 embedding 和 collection 切换前完成。

## 2. 非目标

本次不做：

- 重新切片或重新抽取原始培训文档。
- 银保部、团险部、党课内勤等独立标签体系或独立 collection。
- 二级、三级目录以及“细化分类”标签。
- 新增模型打标调用。
- 根据文件名猜测“培训资料/绩优案例”来源类型。
- 修改 chunk 正文来插入一级标签；一级标签只保存在 metadata 和 Milvus 字段中。
- 产品在售/停售、法规有效期等版本治理；这些作为后续独立任务。

## 3. 数据范围与来源判定

### 3.1 扫描规则

规范化命令以 `parsed/` 为输入根目录，递归匹配：

```text
**/chunks.jsonl
**/*.chunks.jsonl
```

扫描结果必须去重并按相对路径稳定排序。输出目录不能位于输入目录内部，避免重复执行时把产物再次扫描。

### 3.2 来源类型

`source_kind` 只根据输入相对路径判定：

| 输入位置 | `source_kind` |
| --- | --- |
| `parsed/chunk/*.chunks.jsonl` | `培训资料` |
| `parsed/<其他目录>/chunks.jsonl` | `绩优案例` |

任何不符合这两个规则的 JSONL 都应报告为 `unsupported_path`，不能靠内容或文件名猜测来源。

## 4. 一级标签合同

Excel 知识体系是一级标签的业务依据。运行时代码使用固化后的七值枚举，不在每次命令执行时解析 Excel，避免为本地入库增加 XLSX 运行时依赖。

允许的一级标签：

```text
产品知识
合规与风控
销售技能
客户经营
行业与公司
个人成长
组织发展
```

通用工作表中的一级目录统一归并：

| Excel 一级目录 | 规范一级标签 |
| --- | --- |
| 公司品牌 | 行业与公司 |
| 法律合规 | 合规与风控 |

每个 chunk 新增以下 metadata：

```json
{
  "source_kind": "培训资料",
  "primary_domain": "产品知识",
  "domain_tags": ["产品知识", "合规与风控"],
  "domain_tagging_method": "规则匹配",
  "domain_tagging_version": "2026-07-16"
}
```

字段约束：

- `source_kind` 必须是 `培训资料 | 绩优案例`。
- `primary_domain` 必须且只能是七个一级标签之一。
- `domain_tags` 必须非空、去重、顺序稳定，只能包含七个一级标签。
- `primary_domain` 必须包含在 `domain_tags` 中。
- 原有 metadata、text、citations、source_file 和 chunk_id 必须保留。
- 重复执行规范化必须得到相同结果，不得重复添加标签或改变顺序。

## 5. 一级标签推断

新增独立的本地规则模块。规则输入按重要性分层：

1. `metadata.title/category/scenario/tags`。
2. `source_file` 和 citation 文件名。
3. `chunk.text`。

不同输入层使用不同权重，避免长正文中的偶然词覆盖标题和现有结构化标签。每个一级领域得到一个可解释分数：

- metadata 中直接出现规范标签或其明确别名：每个唯一规则计 10 分。
- `title/category/scenario/tags` 中命中领域关键词：每个唯一规则计 4 分。
- `source_file` 或 citation 文件名中命中：每个唯一规则计 2 分。
- chunk 正文中命中：每个唯一规则计 1 分，同一规则在正文重复出现不重复计分。
- 得分至少 4 分的领域进入 `domain_tags`。
- 最高分领域成为 `primary_domain`。
- 同分时按“合规与风控、组织发展、产品知识、客户经营、销售技能、行业与公司、个人成长”确定主标签，保证确定性并优先暴露高风险内容。
- 报告中记录命中的规则和分数，便于抽样审查。
- 完全无法分类的 chunk 标记为 `unclassified`；规范化命令返回非零，不允许进入后续入库。

规则模块不能使用大模型，也不能把未分类内容静默归入“行业与公司”。

## 6. Chunk 兼容

现有 977 个培训资料 chunk 使用 `chunk_type=knowledge_entry`，当前 `RagChunk` 合同不接受该类型。

改造要求：

- `ChunkType` 增加 `knowledge_entry`。
- 课程/培训知识类型集合同时接受 `training_course` 和 `knowledge_entry`。
- 绩优案例现有四种类型保持不变。
- 规范化不把 `knowledge_entry` 伪装成 `training_course`。

## 7. 本地命令

### 7.1 规范化命令

新增：

```bash
uv run xhbx-rag normalize-knowledge \
  --input-dir parsed \
  --out parsed_normalized
```

行为：

1. 扫描并稳定排序全部 chunk 文件。
2. 加载和校验每条 JSONL。
3. 根据路径写入 `source_kind`。
4. 根据本地规则写入一级标签 metadata。
5. 保持输入相对路径写到输出目录。
6. 生成 `classification_report.json`。
7. 发现无效 JSON、重复 chunk_id、未分类 chunk 或不支持路径时返回非零；空文件作为显式 warning 记录并跳过，不阻塞其余有效数据。

报告至少包含：

- 输入文件数、有效文件数、空文件数。
- chunk 总数以及按 `source_kind` 的数量。
- `primary_domain` 与 `domain_tags` 分布。
- 多标签 chunk 数量。
- 无效记录、未分类记录、重复 chunk_id 的文件和行号。
- 每个领域的代表性样本和规则命中信息。
- 输入文件与输出文件的 SHA-256，支持审计和重复运行比对。

规范化失败时可以保留报告，但不能发布一个看似成功的完整输出目录。应先写临时目录，全部成功后原子替换正式输出。

### 7.2 目录入库命令

新增：

```bash
uv run xhbx-rag index-dir \
  --chunks-dir parsed_normalized \
  --collection-name xhbx_knowledge_chunks \
  --mode rebuild \
  --batch-size 64
```

行为：

1. 递归扫描规范化后的两种 chunk 文件名。
2. 在创建 Milvus client 和调用 embedding 之前完成全量加载、schema 校验、全局 chunk_id 去重和一级标签合同校验。
3. 按 `batch-size` 分批调用 embedding，不能把 16,299 条文本一次性发送。
4. `collection-name` 必须通过 Milvus collection 名称白名单校验。
5. 写入临时 staging collection。
6. 全部写入后校验 row count、向量维度和全部 chunk_id。
7. 在 collection 写锁内通过 rename 完成 staging 与目标 collection 切换。
8. 失败时保留原目标 collection，并清理 staging collection。
9. 成功后输出 JSON 摘要，包含 collection、文件数、chunk 数、向量维度和各一级标签数量。

现有单文件 `index` 命令继续保留，避免破坏已有调用；`scripts/index_parsed.sh` 改为调用新目录命令或明确标记为旧流程。

## 8. Milvus schema

统一 collection 增加可过滤字段：

```text
source_kind      VARCHAR
primary_domain   VARCHAR
```

`domain_tags` 继续保存在 `metadata_json` 中，用于召回后的软加权；首版不增加数组字段，避免 Milvus Lite 兼容性风险。

`MilvusChunkRecord.to_row()` 必须把 `source_kind` 和 `primary_domain` 写入独立字段。所有搜索、按 ID 获取、回滚快照和原子索引字段集合必须同步更新。

## 9. 单 collection 查询行为

物理上只使用 `xhbx_knowledge_chunks`，逻辑上保留两条来源通道：

- `绩优案例`：真实案例、某位绩优、实战经验类问题。
- `培训资料`：课程、标准流程、产品知识、合规规定类问题。

当前查询理解中的 `case | course` 语义继续保留，并在搜索阶段映射：

| `collection_targets` | `source_kind` 过滤 |
| --- | --- |
| `case` | `绩优案例` |
| `course` | `培训资料` |
| `case + course` | 不限制来源 |

这样可以最大限度保持查询理解合同兼容，同时不再根据 `collection_targets` 创建不同物理 store。

一级标签查询使用本地规则：

- `infer_query_domains(query)` 只返回七个一级标签。
- 一级标签默认作为软加权信号，不做硬过滤，避免规则漏标导致漏召回。
- `primary_domain` 仍作为 Milvus 可过滤字段，为后续显式筛选保留能力。
- 命中“合规与风控”的候选应参与回答侧合规提示，但本次不新增二级合规规则。

检索顺序：

```text
用户问题
  -> 查询理解（case/course）
  -> 映射 source_kind 过滤
  -> 向量召回 + BM25
  -> 一级标签软加权
  -> rerank
  -> 回答
```

## 10. 配置与兼容

新增统一 collection 配置：

```env
MILVUS_COLLECTION=xhbx_knowledge_chunks
```

`MILVUS_COURSE_COLLECTION` 暂时保留解析兼容，但统一检索模式下不再作为第二个生产读库。配置摘要、状态接口和 Web collection 展示都只暴露去重后的统一 collection。

现有 `xhbx_sales_chunks` 和 `xhbx_course_chunks` 不在迁移成功前删除；统一 collection 完成验证后再由用户决定是否清理旧库。

## 11. 错误处理

- 原始 JSONL 不可解析：报告文件与行号，规范化失败。
- 记录不符合 RagChunk：报告字段错误，规范化失败。
- 空文件：报告为 `skipped_empty` warning，不产生输出 chunk，但不阻塞其余有效数据。
- 全局 chunk_id 重复：报告全部冲突来源，规范化失败。
- 一级标签无法确定：报告规则输入摘要，规范化失败。
- embedding 失败：停止批次、删除 staging、保留旧 collection。
- staging 写入或校验失败：删除 staging、保留旧 collection。
- collection 切换失败：尝试恢复旧 collection 名称；恢复失败时给出明确恢复命令，不宣称成功。

所有错误不得输出 API key、token、原始客户隐私信息或完整长文本。

## 12. 测试与验收

### 12.1 单元测试

- 两种路径正确映射 `source_kind`。
- 七个一级标签规则均有正例和反例。
- 多标签、主标签包含关系、顺序稳定。
- 重复规范化幂等。
- `knowledge_entry` 可以通过 RagChunk 校验。
- Milvus row 包含 `source_kind` 和 `primary_domain`。
- Milvus filter 支持 `source_kinds` 和 `primary_domains`。
- case/course 正确映射为来源过滤。

### 12.2 命令测试

- 递归发现两种文件模式且不重复。
- 空文件被明确报告并跳过；坏 JSON、重复 ID、未分类返回非零并出报告。
- 全量校验失败时不创建 Milvus client、不调用 embedding。
- embedding 按 batch size 分批。
- staging 成功切换目标 collection。
- 中途失败保留旧 collection。

### 12.3 仓库回归

```bash
uv run pytest tests/test_knowledge_domain.py \
  tests/test_knowledge_normalizer.py \
  tests/test_directory_indexer.py \
  tests/test_milvus_store.py \
  tests/test_query_understanding.py \
  tests/test_search.py -q

uv run pytest -q
```

### 12.4 数据验收

在真实 `parsed/` 上先运行规范化，不调用 embedding。验收要求：

- 发现 1,078 个输入 chunk 文件。
- 来源类型数量与目录规则一致。
- 无静默丢弃记录。
- 所有非空有效 chunk 均满足一级标签合同。
- 两个已知空文件必须明确出现在报告中，不能当作成功入库。
- 抽样检查每个一级领域以及绩优案例/培训资料两类来源。

真实 embedding 和 collection 切换属于有成本的本地运行步骤，代码测试通过后再执行。

## 13. 预计代码范围

新增：

- `src/xhbx_rag/knowledge_domain.py`
- `src/xhbx_rag/knowledge_normalizer.py`
- `src/xhbx_rag/directory_indexer.py`
- 对应三个测试文件

修改：

- `src/xhbx_rag/models.py`
- `src/xhbx_rag/cli.py`
- `src/xhbx_rag/milvus_store.py`
- `src/xhbx_rag/query_understanding.py`
- `src/xhbx_rag/search.py`
- `src/xhbx_rag/config.py`
- `scripts/index_parsed.sh`
- `.env.example`、README 和相关测试

实现必须保持改动聚焦，不重构无关的 Web、MCP、评测或回答生成逻辑。

## 14. 已确认决策

- 单一物理 collection。
- `parsed/chunk` 是培训资料；`parsed` 其他案例目录是绩优案例。
- 处理 `parsed/` 下全部 chunk。
- 只使用一级业务标签。
- 一条 chunk 可以有多个一级标签，但必须有且只有一个主标签。
- 不重新切片，只做规范化、重新 embedding 和重新入库。
- 本地两步命令优先：先规范化审查，再批量入库。
