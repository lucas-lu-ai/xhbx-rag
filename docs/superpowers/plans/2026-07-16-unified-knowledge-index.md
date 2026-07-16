# 统一知识库一级标签与本地批量入库 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在不改变现有切片边界的前提下，为 `parsed/` 全部 chunk 增加来源类型和七类一级标签，并通过本地两步命令安全重建单一 Milvus collection。

**Architecture:** 新增纯规则领域分类器、无副作用的目录规范化器和 staging collection 目录入库器；规范化阶段先完成全量校验并原子发布文件，入库阶段先完成全量校验，再批量 embedding、校验 staging，最后在 collection 写锁内切换。查询侧继续接受 `case | course` 语义，但只打开一个物理 collection，并将语义转换成 `source_kind` 过滤。

**Tech Stack:** Python 3.12、Pydantic 2、argparse、pymilvus/Milvus Lite、pytest、uv。

## Global Constraints

- 不重新解析原始 PPT、PDF、DOCX，也不改变现有 chunk 边界。
- 输入扫描必须递归覆盖 `**/chunks.jsonl` 与 `**/*.chunks.jsonl`，去重并按相对路径稳定排序。
- `parsed/chunk/*.chunks.jsonl` 固定为 `培训资料`；其他 `parsed/<目录>/chunks.jsonl` 固定为 `绩优案例`；不根据正文或文件名猜来源。
- 一级标签只能是：`产品知识`、`合规与风控`、`销售技能`、`客户经营`、`行业与公司`、`个人成长`、`组织发展`。
- 每条 chunk 必须有非空 `domain_tags`，且 `primary_domain` 必须包含于其中；规则版本固定为 `2026-07-16`。
- 规范化不得调用大模型；运行时不得引入 XLSX 依赖。
- 全量文件、schema、同来源重复 ID 审计、跨来源 ID 冲突和标签合同校验必须发生在 Milvus client 创建与 embedding 调用之前。
- embedding 必须按 `--batch-size` 分批，默认 `64`。
- 重建必须写 staging collection；失败保留旧目标 collection，成功后在写锁中 rename 切换。
- `MILVUS_COURSE_COLLECTION` 继续解析兼容，但统一读路径只暴露 `MILVUS_COLLECTION=xhbx_knowledge_chunks`。
- 两个已知空培训文件作为 `skipped_empty` warning，不阻塞其余有效数据。
- 不删除旧的 `xhbx_sales_chunks` 和 `xhbx_course_chunks`。
- 保留现有单文件 `index` 命令，不重构无关 Web、MCP、评测和回答生成逻辑。

---

## 文件结构

### 新增文件

- `src/xhbx_rag/knowledge_domain.py`：七个一级标签的枚举、可解释规则评分、查询标签推断和 metadata 合同校验。
- `src/xhbx_rag/knowledge_normalizer.py`：目录发现、来源判定、逐行校验、同来源 ID 稳定去重、跨来源冲突检查、报告生成和原子目录发布。
- `src/xhbx_rag/directory_indexer.py`：规范化目录预检、批量 embedding、staging 写入校验和原子 collection 切换。
- `tests/test_knowledge_domain.py`：规则、权重、多标签、主标签、幂等与合同测试。
- `tests/test_knowledge_normalizer.py`：双 glob、来源路径、失败报告、空文件、同来源重复版本、跨来源 ID 冲突和原子发布测试。
- `tests/test_directory_indexer.py`：零副作用预检、batch、staging、校验、切换和失败回滚测试。

### 修改文件

- `src/xhbx_rag/models.py`：允许 `knowledge_entry`。
- `src/xhbx_rag/milvus_store.py`：增加两个标量字段、过滤条件、全量 ID/row count/rename 能力，统一配置只返回一个物理 collection。
- `src/xhbx_rag/atomic_indexer.py`：原始快照字段集合加入 `source_kind` 与 `primary_domain`。
- `src/xhbx_rag/query_understanding.py`：课程类型同时接受 `training_course` 和 `knowledge_entry`。
- `src/xhbx_rag/search.py`：`case | course` 映射为 `source_kinds`，一级标签用于软加权。
- `src/xhbx_rag/config.py`：统一 collection 默认值。
- `src/xhbx_rag/cli.py`：注册 `normalize-knowledge` 与 `index-dir`。
- `src/xhbx_rag/web/services.py`：状态与模型目标路由只选择统一 collection。
- `src/xhbx_rag/web/app.py`：Web 上传的两个逻辑目标写入同一物理 collection。
- `src/xhbx_rag/mcp_server.py`：状态不再把旧课程 collection 暴露为生产读库。
- `scripts/index_parsed.sh`：改成先规范化、后目录重建的本地入口。
- `.env.example`、`.env.mcp.example`、`docker-compose.yml`、`docker-compose.mcp.yml`、`docker-compose.offline.yml`：统一默认 collection。
- `README.md`：记录两步命令、来源/一级标签合同和旧库保留策略。
- 相关现有测试：更新默认 collection 和单库路由断言。

---

### Task 1: 一级标签领域模型与 chunk 合同

**Files:**
- Create: `src/xhbx_rag/knowledge_domain.py`
- Modify: `src/xhbx_rag/models.py:441-456`
- Test: `tests/test_knowledge_domain.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: `RagChunk`、`metadata.title/category/scenario/tags/business_domains/tag_paths`、citation filename、`source_file`、正文。
- Produces: `CANONICAL_DOMAINS: Sequence[str]`、`DomainClassification`、`infer_chunk_domains(chunk: RagChunk) -> DomainClassification | None`、`infer_query_domains(text: str) -> list[str]`、`apply_domain_metadata(chunk, classification, source_kind) -> RagChunk`、`validate_domain_metadata(metadata) -> list[str]`。

- [ ] **Step 1: 写 `knowledge_entry` 与七领域规则的失败测试**

```python
@pytest.mark.parametrize("domain", CANONICAL_DOMAINS)
def test_every_canonical_domain_has_a_structured_positive_example(domain: str) -> None:
    chunk = _chunk(metadata={"tags": [domain]})
    result = infer_chunk_domains(chunk)
    assert result is not None
    assert result.primary_domain == domain
    assert result.domain_tags == [domain]


def test_chunk_can_have_multiple_domains_with_deterministic_primary() -> None:
    chunk = _chunk(metadata={"tags": ["合规要求", "产品知识"]})
    result = infer_chunk_domains(chunk)
    assert result is not None
    assert result.primary_domain == "合规与风控"
    assert result.domain_tags == ["合规与风控", "产品知识"]


def test_body_only_single_weak_hit_is_unclassified() -> None:
    assert infer_chunk_domains(_chunk(text="这里只提到一次合规")) is None


def test_rag_chunk_accepts_knowledge_entry() -> None:
    assert _chunk(chunk_type="knowledge_entry").chunk_type == "knowledge_entry"
```

- [ ] **Step 2: 运行测试并确认因接口不存在或类型不接受而失败**

Run: `uv run pytest tests/test_knowledge_domain.py tests/test_models.py -q`

Expected: FAIL，包含 `ModuleNotFoundError: xhbx_rag.knowledge_domain` 或 `knowledge_entry` literal validation error。

- [ ] **Step 3: 实现确定性领域分类器和 metadata 合同**

核心类型和分数逻辑必须写成以下接口；关键词表逐项归属到七个 domain，不能出现兜底 domain：

```python
Domain = Literal[
    "产品知识", "合规与风控", "销售技能", "客户经营",
    "行业与公司", "个人成长", "组织发展",
]
SourceKind = Literal["培训资料", "绩优案例"]
CANONICAL_DOMAINS: tuple[Domain, Domain, Domain, Domain, Domain, Domain, Domain] = (
    "产品知识", "合规与风控", "销售技能", "客户经营",
    "行业与公司", "个人成长", "组织发展",
)
DOMAIN_TIE_ORDER: tuple[Domain, Domain, Domain, Domain, Domain, Domain, Domain] = (
    "合规与风控", "组织发展", "产品知识", "客户经营",
    "销售技能", "行业与公司", "个人成长",
)
DOMAIN_TAGGING_VERSION = "2026-07-16"

@dataclass(frozen=True)
class RuleHit:
    domain: Domain
    field: str
    rule: str
    points: int

@dataclass(frozen=True)
class DomainClassification:
    primary_domain: Domain
    domain_tags: list[Domain]
    scores: dict[Domain, int]
    hits: list[RuleHit]

def infer_chunk_domains(chunk: RagChunk) -> DomainClassification | None:
    hits = [
        *_direct_domain_hits(chunk.metadata),
        *_keyword_hits(_structured_text(chunk.metadata), field="metadata", points=4),
        *_keyword_hits(_source_text(chunk), field="source", points=2),
        *_keyword_hits(chunk.text, field="text", points=1),
    ]
    hits = _dedupe_rule_hits(hits)
    scores = _scores(hits)
    tags = [domain for domain in DOMAIN_TIE_ORDER if scores.get(domain, 0) >= 4]
    if not tags:
        return None
    primary = min(tags, key=lambda domain: (-scores[domain], DOMAIN_TIE_ORDER.index(domain)))
    ordered = [primary, *[domain for domain in tags if domain != primary]]
    return DomainClassification(primary, ordered, scores, hits)

def apply_domain_metadata(
    chunk: RagChunk,
    classification: DomainClassification,
    source_kind: SourceKind,
) -> RagChunk:
    metadata = dict(chunk.metadata)
    metadata.update({
        "source_kind": source_kind,
        "primary_domain": classification.primary_domain,
        "domain_tags": classification.domain_tags,
        "domain_tagging_method": "规则匹配",
        "domain_tagging_version": DOMAIN_TAGGING_VERSION,
    })
    return chunk.model_copy(update={"metadata": metadata})
```

明确规则词至少覆盖：

```python
DOMAIN_KEYWORDS = {
    "产品知识": ("保险产品", "产品知识", "险种产品", "保障责任", "保险责任", "条款", "保单", "年金", "寿险", "重疾险", "医疗险", "意外险", "长护险", "养老险", "分红险"),
    "合规与风控": ("法律合规", "合规要求", "合规", "风控", "监管", "反洗钱", "销售误导", "适当性", "双录", "消费者权益", "禁止事项", "风险提示", "民法典", "保险法", "税法", "纠纷"),
    "销售技能": ("销售技能", "销售切入", "产品营销", "产品推介", "话术", "沟通", "面谈", "促成", "异议处理", "需求挖掘", "需求唤醒", "方案设计", "成交"),
    "客户经营": ("客户经营", "客户服务", "客群管理", "客户管理", "客户关系", "转介绍", "高净值", "客户画像", "需求分析", "服务经营", "客户筛选", "孤儿单"),
    "行业与公司": ("行业与公司", "公司品牌", "新华保险", "公司介绍", "企业文化", "保险行业", "行业趋势", "市场分析", "宏观经济", "品牌"),
    "个人成长": ("个人成长", "职业素养", "自我管理", "时间管理", "情绪管理", "学习能力", "专业成长", "职业生涯", "绩效提升", "目标管理", "心态", "习惯"),
    "组织发展": ("组织发展", "增员", "招募", "团队管理", "组织管理", "主管", "人才培养", "绩效管理", "晨会", "早会", "会议经营", "活动量管理", "基础管理", "党课"),
}
```

`validate_domain_metadata()` 必须返回具体错误字符串，并检查五个字段、允许值、非空去重、顺序稳定和主标签包含关系；重复执行 `apply_domain_metadata()` 不得改变输出。

- [ ] **Step 4: 运行领域与模型测试确认通过**

Run: `uv run pytest tests/test_knowledge_domain.py tests/test_models.py -q`

Expected: PASS。

- [ ] **Step 5: 提交领域模型**

```bash
git add src/xhbx_rag/knowledge_domain.py src/xhbx_rag/models.py tests/test_knowledge_domain.py tests/test_models.py
git commit -m "feat: add first-level knowledge domains"
```

---

### Task 2: 目录规范化、审计报告与原子发布

**Files:**
- Create: `src/xhbx_rag/knowledge_normalizer.py`
- Create: `tests/test_knowledge_normalizer.py`

**Interfaces:**
- Consumes: Task 1 的 `infer_chunk_domains()`、`apply_domain_metadata()`、`RagChunk`。
- Produces: `discover_chunk_files(root: Path) -> list[Path]`、`source_kind_for_path(root, path) -> SourceKind`、`normalize_knowledge(input_dir, out_dir) -> NormalizationResult`。

- [ ] **Step 1: 写扫描、来源和成功规范化的失败测试**

```python
def test_discovery_dedupes_both_patterns_and_sorts_relative_paths(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "chunk" / "b.chunks.jsonl", [_chunk("b")])
    _write_jsonl(tmp_path / "案例A" / "chunks.jsonl", [_chunk("a")])
    assert [p.relative_to(tmp_path).as_posix() for p in discover_chunk_files(tmp_path)] == [
        "chunk/b.chunks.jsonl", "案例A/chunks.jsonl",
    ]


def test_source_kind_is_path_derived_only(tmp_path: Path) -> None:
    assert source_kind_for_path(tmp_path, tmp_path / "chunk" / "课件.chunks.jsonl") == "培训资料"
    assert source_kind_for_path(tmp_path, tmp_path / "绩优案例" / "chunks.jsonl") == "绩优案例"
    with pytest.raises(UnsupportedKnowledgePath):
        source_kind_for_path(tmp_path, tmp_path / "other.jsonl")


def test_normalize_preserves_relative_path_and_original_chunk_fields(tmp_path: Path) -> None:
    input_dir, out_dir = tmp_path / "parsed", tmp_path / "normalized"
    original = _chunk("id-1", metadata={"title": "保险产品保障责任"})
    _write_jsonl(input_dir / "chunk" / "产品.chunks.jsonl", [original])
    result = normalize_knowledge(input_dir, out_dir)
    normalized = load_chunks_jsonl(out_dir / "chunk" / "产品.chunks.jsonl")[0]
    assert result.success is True
    assert normalized.chunk_id == original.chunk_id
    assert normalized.text == original.text
    assert normalized.citations == original.citations
    assert normalized.source_file == original.source_file
    assert normalized.metadata["source_kind"] == "培训资料"
    assert normalized.metadata["primary_domain"] == "产品知识"
```

- [ ] **Step 2: 运行测试并确认接口不存在**

Run: `uv run pytest tests/test_knowledge_normalizer.py -q`

Expected: FAIL，包含 `ModuleNotFoundError: xhbx_rag.knowledge_normalizer`。

- [ ] **Step 3: 实现逐行预检、报告结构和临时目录写入**

```python
@dataclass(frozen=True)
class NormalizationResult:
    success: bool
    report_path: Path
    input_files: int
    chunks: int

def discover_chunk_files(root: Path) -> list[Path]:
    candidates = {*root.rglob("chunks.jsonl"), *root.rglob("*.chunks.jsonl")}
    return sorted((p for p in candidates if p.is_file()), key=lambda p: p.relative_to(root).as_posix())

def source_kind_for_path(root: Path, path: Path) -> SourceKind:
    relative = path.relative_to(root)
    if len(relative.parts) == 2 and relative.parts[0] == "chunk" and relative.name.endswith(".chunks.jsonl"):
        return "培训资料"
    if len(relative.parts) == 2 and relative.parts[0] != "chunk" and relative.name == "chunks.jsonl":
        return "绩优案例"
    raise UnsupportedKnowledgePath(relative.as_posix())
```

`normalize_knowledge()` 必须：

1. 拒绝不存在的输入目录、相同输入输出、输出位于输入内部。
2. 在 `out_dir.parent/.<name>.tmp-<uuid>` 写数据。
3. 逐行 `json.loads` + `RagChunk.model_validate`，错误记录 `relative_path` 和 `line` 后继续扫描。
4. 空文件记录 `status=skipped_empty`，不创建空产物。
5. 用 `dict[chunk_id, location]` 检测全局重复；同一来源类型稳定保留首条并记录 `deduplicated_chunk_id` warning，跨来源类型记录阻塞错误。
6. 每条有效记录调用领域分类；`None` 时记录 `unclassified`，不得写正式输出。
7. 按输入相对路径写 compact JSONL，每行 `model_dump(mode="json")`，结尾保留换行。
8. 报告包含 `counts.input_chunks/chunks/deduplicated_chunks`、`source_kind_distribution`、`primary_domain_distribution`、`domain_tag_distribution`、`multi_domain_chunks`、`warnings`、`errors`、`samples`、`files[{input_sha256, output_sha256}]`。
9. 成功时把报告写进 staging 后原子发布；失败时删除 staging，并把报告写到 `out_dir.with_name(out_dir.name + ".classification_report.json")`。

- [ ] **Step 4: 写失败安全测试**

```python
def test_empty_file_and_same_source_duplicates_warn_but_bad_json_fails_without_publish(tmp_path: Path) -> None:
    input_dir, out_dir = tmp_path / "parsed", tmp_path / "normalized"
    _write_jsonl(input_dir / "chunk" / "ok.chunks.jsonl", [_chunk("same", metadata={"tags": ["销售技能"]})])
    (input_dir / "chunk" / "empty.chunks.jsonl").write_text("", encoding="utf-8")
    _write_jsonl(input_dir / "chunk" / "duplicate.chunks.jsonl", [_chunk("same", metadata={"tags": ["客户经营"]})])
    (input_dir / "案例B").mkdir(parents=True)
    (input_dir / "案例B" / "chunks.jsonl").write_text("{bad json}\n", encoding="utf-8")
    result = normalize_knowledge(input_dir, out_dir)
    report = json.loads(result.report_path.read_text(encoding="utf-8"))
    assert result.success is False
    assert not out_dir.exists()
    assert report["counts"]["empty_files"] == 1
    assert {error["code"] for error in report["errors"]} == {"invalid_json"}
    assert "deduplicated_chunk_id" in {warning["code"] for warning in report["warnings"]}


def test_successful_rerun_atomically_replaces_old_output(tmp_path: Path) -> None:
    input_dir, out_dir = tmp_path / "parsed", tmp_path / "normalized"
    _write_jsonl(
        input_dir / "chunk" / "产品.chunks.jsonl",
        [_chunk("id-1", metadata={"tags": ["产品知识"]})],
    )
    first = normalize_knowledge(input_dir, out_dir)
    first_snapshot = {
        path.relative_to(out_dir).as_posix(): path.read_bytes()
        for path in out_dir.rglob("*")
        if path.is_file()
    }
    second = normalize_knowledge(input_dir, out_dir)
    second_snapshot = {
        path.relative_to(out_dir).as_posix(): path.read_bytes()
        for path in out_dir.rglob("*")
        if path.is_file()
    }
    assert first.success and second.success
    assert first_snapshot == second_snapshot
    assert not list(tmp_path.glob(".normalized.tmp-*"))
```

- [ ] **Step 5: 运行规范化测试确认通过**

Run: `uv run pytest tests/test_knowledge_domain.py tests/test_knowledge_normalizer.py -q`

Expected: PASS。

- [ ] **Step 6: 提交规范化器**

```bash
git add src/xhbx_rag/knowledge_normalizer.py tests/test_knowledge_normalizer.py
git commit -m "feat: normalize knowledge directories"
```

---

### Task 3: Milvus 一级字段、过滤和 collection 操作

**Files:**
- Modify: `src/xhbx_rag/milvus_store.py:22-196,453-555`
- Modify: `src/xhbx_rag/atomic_indexer.py:21-33`
- Modify: `tests/test_milvus_store.py`
- Modify: `tests/test_atomic_indexer.py`

**Interfaces:**
- Consumes: normalized `RagChunk.metadata[source_kind, primary_domain]`。
- Produces: row fields `source_kind`/`primary_domain`、filter keys `source_kinds`/`primary_domains`、`row_count()`、`fetch_all_chunk_ids()`、`rename_collection(new_name)`，且 `configured_collection_names(config)` 只返回统一 collection。

- [ ] **Step 1: 写 row、schema、filter 与单物理 collection 的失败测试**

```python
def test_milvus_row_contains_source_kind_and_primary_domain() -> None:
    row = MilvusChunkRecord.from_chunk(_normalized_chunk(), [0.1, 0.2]).to_row()
    assert row["source_kind"] == "培训资料"
    assert row["primary_domain"] == "产品知识"


def test_filter_expr_supports_source_and_primary_domain_lists() -> None:
    assert _build_filter_expr({
        "source_kinds": ["培训资料"],
        "primary_domains": ["产品知识", "合规与风控"],
    }) == 'source_kind in ["培训资料"] and primary_domain in ["产品知识", "合规与风控"]'


def test_configured_collection_names_returns_only_unified_collection() -> None:
    config = SimpleNamespace(milvus_collection="xhbx_knowledge_chunks", milvus_course_collection="legacy")
    assert configured_collection_names(config) == ["xhbx_knowledge_chunks"]
```

- [ ] **Step 2: 运行测试确认失败**

Run: `uv run pytest tests/test_milvus_store.py tests/test_atomic_indexer.py -q`

Expected: FAIL，缺少 row 字段、filter 片段和新 store 方法。

- [ ] **Step 3: 扩展 schema、原始字段集合与 store 能力**

```python
_CHUNK_OUTPUT_FIELDS = [
    "chunk_id", "text", "case_name", "chunk_type", "stage", "scenario",
    "source_kind", "primary_domain", "metadata_json", "citations_json",
]

def to_row(self) -> dict[str, Any]:
    metadata = self.chunk.metadata
    return {
        "chunk_id": self.chunk.chunk_id,
        "vector": self.vector,
        "text": self.chunk.text,
        "text_hash": self.text_hash,
        "case_name": str(metadata.get("case_name", "")),
        "chunk_type": self.chunk.chunk_type,
        "stage": str(metadata.get("stage", "")),
        "scenario": str(metadata.get("scenario", "")),
        "source_kind": str(metadata.get("source_kind", "")),
        "primary_domain": str(metadata.get("primary_domain", "")),
        "metadata_json": json.dumps(metadata, ensure_ascii=False),
        "citations_json": _citations_json(self.chunk.chunk_id, self.chunk.citations),
    }

def row_count(self) -> int:
    stats = self.client.get_collection_stats(self.collection_name)
    return int(stats.get("row_count", 0))

def fetch_all_chunk_ids(self, batch_size: int = 1000) -> set[str]:
    iterator = self.client.query_iterator(
        collection_name=self.collection_name,
        filter="",
        output_fields=["chunk_id"],
        batch_size=batch_size,
    )
    try:
        return {str(row["chunk_id"]) for batch in iter(iterator.next, []) for row in batch}
    finally:
        iterator.close()

def rename_collection(self, new_name: str) -> None:
    self.client.rename_collection(self.collection_name, new_name)
    self.collection_name = new_name
```

`ensure_collection()` 增加两个 `VARCHAR(max_length=64)`；`_build_filter_expr()` 对两个列表使用和 `chunk_types` 相同的安全转义；filter options 增加 `source_kinds`、`primary_domains`。`atomic_indexer._RAW_ROW_STRING_FIELDS` 同步加入两个字段，保证快照/回滚 schema 一致。

- [ ] **Step 4: 运行 Milvus Lite 往返、过滤和 atomic 回归测试**

Run: `uv run pytest tests/test_milvus_store.py tests/test_atomic_indexer.py -q`

Expected: PASS，包括真实临时 Milvus Lite collection 的 source/domain filter。

- [ ] **Step 5: 提交 store 变更**

```bash
git add src/xhbx_rag/milvus_store.py src/xhbx_rag/atomic_indexer.py tests/test_milvus_store.py tests/test_atomic_indexer.py
git commit -m "feat: store knowledge metadata in Milvus"
```

---

### Task 4: 目录批量 embedding 与 staging 原子重建

**Files:**
- Create: `src/xhbx_rag/directory_indexer.py`
- Create: `tests/test_directory_indexer.py`

**Interfaces:**
- Consumes: `discover_chunk_files()`、`validate_domain_metadata()`、`MilvusChunkRecord` 和可注入 `store_factory(collection_name)`。
- Produces: `validate_collection_name(name: str) -> str`、`load_normalized_directory(path: Path) -> LoadedKnowledgeDirectory`、`index_directory(chunks_dir: Path, embedding_client: object, store_factory: Callable[[str], object], collection_name: str, batch_size: int = 64, mode: str = "rebuild") -> DirectoryIndexResult`。

- [ ] **Step 1: 写预检必须零副作用和 batch 行为的失败测试**

```python
def test_invalid_metadata_fails_before_embedding_or_store_creation(tmp_path: Path) -> None:
    _write_jsonl(tmp_path / "chunk" / "bad.chunks.jsonl", [_chunk(metadata={})])
    embedding = FakeEmbedding()
    stores = FakeStoreFactory()
    with pytest.raises(DirectoryIndexError, match="一级标签合同"):
        index_directory(tmp_path, embedding, stores, "xhbx_knowledge_chunks", batch_size=2)
    assert embedding.calls == []
    assert stores.calls == []


def test_embedding_is_batched_and_staging_is_verified_before_swap(tmp_path: Path) -> None:
    _write_normalized_chunks(tmp_path, count=5)
    result = index_directory(tmp_path, embedding, stores, "xhbx_knowledge_chunks", batch_size=2)
    assert [len(call) for call in embedding.calls] == [2, 2, 1]
    assert result.indexed == 5
    assert stores.events[-3:] == ["verify:5", "rename:staging->target", "drop:backup"]
```

- [ ] **Step 2: 运行测试确认模块不存在**

Run: `uv run pytest tests/test_directory_indexer.py -q`

Expected: FAIL，包含 `ModuleNotFoundError: xhbx_rag.directory_indexer`。

- [ ] **Step 3: 实现全量加载、collection 白名单和批量写 staging**

```python
_COLLECTION_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,219}$")

@dataclass(frozen=True)
class LoadedKnowledgeDirectory:
    files: list[Path]
    chunks: list[RagChunk]
    primary_domain_counts: dict[str, int]

@dataclass(frozen=True)
class DirectoryIndexResult:
    collection: str
    files: int
    indexed: int
    vector_dim: int
    primary_domain_counts: dict[str, int]

def load_normalized_directory(root: Path) -> LoadedKnowledgeDirectory:
    files = discover_chunk_files(root)
    chunks: list[RagChunk] = []
    locations: dict[str, str] = {}
    errors: list[str] = []
    for path in files:
        for line_no, chunk in _load_with_locations(path):
            errors.extend(f"{path}:{line_no}: {message}" for message in validate_domain_metadata(chunk.metadata))
            if chunk.chunk_id in locations:
                errors.append(f"重复 chunk_id {chunk.chunk_id}: {locations[chunk.chunk_id]} / {path}:{line_no}")
            locations[chunk.chunk_id] = f"{path}:{line_no}"
            chunks.append(chunk)
    if errors:
        raise DirectoryIndexError("规范化目录预检失败：\n" + "\n".join(errors[:20]))
    counts = Counter(str(chunk.metadata["primary_domain"]) for chunk in chunks)
    return LoadedKnowledgeDirectory(files, chunks, dict(sorted(counts.items())))
```

`index_directory()` 的顺序必须是：

```python
loaded = load_normalized_directory(chunks_dir)       # 无 embedding、无 store
target = validate_collection_name(collection_name)
staging_name = f"{target}__staging__{uuid4().hex[:8]}"
backup_name = f"{target}__backup__{uuid4().hex[:8]}"
staging = store_factory(staging_name)                # 预检后才创建 client
vector_dim: int | None = None
try:
    for batch in batched(loaded.chunks, batch_size):
        vectors = embedding_client.embed_documents([chunk.text for chunk in batch])
        vector_dim = _validate_batch_vectors(vectors, expected=len(batch), expected_dim=vector_dim)
        if not staging.collection_exists():
            staging.ensure_collection(vector_dim)
        staging.upsert([
            MilvusChunkRecord.from_chunk(chunk, vector)
            for chunk, vector in zip(batch, vectors, strict=True)
        ])
    staging.flush()
    if staging.row_count() != len(loaded.chunks):
        raise DirectoryIndexError("staging row count 校验失败")
    if staging.fetch_all_chunk_ids() != {chunk.chunk_id for chunk in loaded.chunks}:
        raise DirectoryIndexError("staging chunk_id 集合校验失败")
    _swap_collections(store_factory, target, staging_name, backup_name)
except Exception:
    staging.drop_collection()
    raise
```

`_swap_collections()` 必须获取 `collection_write_lock(uri, target)`；目标存在时先 rename 为 backup，再把 staging rename 为 target。第二次 rename 失败时把 backup rename 回 target；成功后删除 backup。异常必须包含可执行的手工恢复名称，且不得宣称成功。

- [ ] **Step 4: 写并通过失败保旧库测试**

```python
def test_embedding_failure_drops_staging_and_keeps_target(tmp_path: Path) -> None:
    _write_normalized_chunks(tmp_path, count=3)
    embedding = FakeEmbedding(fail_on_call=1)
    stores = FakeStoreFactory(target_ids={"old"})
    with pytest.raises(DirectoryIndexError, match="向量生成失败"):
        index_directory(
            tmp_path,
            embedding,
            stores,
            "xhbx_knowledge_chunks",
            batch_size=2,
        )
    assert stores.target_ids == {"old"}
    assert not stores.staging_names()


def test_second_rename_failure_restores_backup(tmp_path: Path) -> None:
    _write_normalized_chunks(tmp_path, count=2)
    embedding = FakeEmbedding()
    stores = FakeStoreFactory(target_ids={"old"}, fail_rename_to_target=True)
    with pytest.raises(DirectoryIndexError, match="collection 切换失败"):
        index_directory(
            tmp_path,
            embedding,
            stores,
            "xhbx_knowledge_chunks",
            batch_size=2,
        )
    assert stores.collection_ids("xhbx_knowledge_chunks") == {"old"}
```

Run: `uv run pytest tests/test_directory_indexer.py -q`

Expected: PASS。

- [ ] **Step 5: 提交目录入库器**

```bash
git add src/xhbx_rag/directory_indexer.py tests/test_directory_indexer.py
git commit -m "feat: rebuild knowledge collection atomically"
```

---

### Task 5: 单 collection 查询路由与一级标签软加权

**Files:**
- Modify: `src/xhbx_rag/query_understanding.py:20-27,110-123,199-216`
- Modify: `src/xhbx_rag/search.py:1-270`
- Modify: `src/xhbx_rag/answer.py:589-630`
- Modify: `tests/test_query_understanding.py`
- Modify: `tests/test_indexer_search.py`
- Modify: `tests/test_answer.py`

**Interfaces:**
- Consumes: `QueryUnderstanding.collection_targets`、Task 1 的 `infer_query_domains()`、normalized metadata。
- Produces: `case -> source_kinds=[绩优案例]`、`course -> source_kinds=[培训资料]`、双目标不限制来源，以及 `domain_tags` 软加权。

- [ ] **Step 1: 写来源映射、knowledge_entry 和领域加权失败测试**

```python
def test_query_understanding_accepts_knowledge_entry_as_course_type() -> None:
    result = QueryUnderstanding.model_validate({
        "intent": "general_sales_qa",
        "rewritten_query": "培训课件里的产品责任是什么？",
        "needs_retrieval": True,
        "collection_targets": ["course"],
        "filters": {"chunk_types": ["knowledge_entry"]},
    })
    assert result.filters.chunk_types == ["knowledge_entry"]


@pytest.mark.parametrize(("targets", "expected"), [
    (["case"], {"source_kinds": ["绩优案例"]}),
    (["course"], {"source_kinds": ["培训资料"]}),
    (["case", "course"], {}),
])
def test_search_maps_logical_targets_to_source_filter(targets, expected) -> None:
    understanding = QueryUnderstanding(
        intent="general_sales_qa",
        rewritten_query="保险知识怎么讲？",
        needs_retrieval=True,
        collection_targets=targets,
        filters={},
    )
    assert _search_filters(understanding) == expected


def test_domain_boost_uses_domain_tags_and_keeps_it_soft() -> None:
    hits = [_hit("a", domains=["产品知识"]), _hit("b", domains=["客户经营"])]
    boosted, details = _apply_domain_boost(hits, ["产品知识"])
    assert {hit.chunk.chunk_id for hit in boosted} == {"a", "b"}
    assert details[0]["matched_domains"] == ["产品知识"]


def test_compliance_domain_adds_generic_answer_guardrail() -> None:
    search_result = _search_result_fixture()
    search_result["results"][0]["metadata"].update({
        "primary_domain": "合规与风控",
        "domain_tags": ["合规与风控"],
    })
    prompt = _build_user_prompt(search_result)
    assert "证据属于合规与风控领域" in prompt
    assert "不得扩展承诺" in prompt
```

- [ ] **Step 2: 运行查询测试确认失败**

Run: `uv run pytest tests/test_query_understanding.py tests/test_indexer_search.py -q`

Expected: FAIL，`knowledge_entry` 被丢弃、没有 `source_kinds`、仍读取 `tag_paths`。

- [ ] **Step 3: 修改查询理解合同和检索排序**

```python
_COURSE_CHUNK_TYPES = frozenset({"training_course", "knowledge_entry"})

def _source_kinds(targets: list[CollectionTarget]) -> list[str]:
    unique = set(targets)
    if unique == {"case"}:
        return ["绩优案例"]
    if unique == {"course"}:
        return ["培训资料"]
    return []

def _search_filters(understanding: QueryUnderstanding) -> dict:
    filters = understanding.filters
    result: dict = {}
    if filters.chunk_types and understanding.intent != "general_sales_qa":
        result["chunk_types"] = filters.chunk_types
    if filters.stage:
        result["stage"] = filters.stage
    source_kinds = _source_kinds(understanding.collection_targets)
    if source_kinds:
        result["source_kinds"] = source_kinds
    return result
```

把 `infer_query_tags()`/`tag_paths` 加权替换为 `infer_query_domains()`/`domain_tags`；仍使用每个匹配 `+0.1`、最多 3 个的软信号，不把一级领域加入 Milvus 硬过滤。返回结果字段使用 `matched_domains` 和 `domain_boost_factor`，同时保留旧 `matched_tag_paths=[]`、`tag_boost_factor=1.0` 两个兼容字段一个版本。

在 `answer._collect_compliance_risks()` 中保留现有细粒度 `compliance_risks` 收集；若任一证据 `primary_domain == "合规与风控"` 或 `domain_tags` 包含该值，再追加一次通用约束 `证据属于合规与风控领域，回答需保留原文限制条件，不得扩展承诺`。这样一级标签能进入已有合规提示块，但不会伪造二级风险类型。

Query prompt 必须明确：`course` 包含 `training_course | knowledge_entry`，两个 target 只是来源语义，不代表两个物理 collection。

- [ ] **Step 4: 运行查询与检索回归**

Run: `uv run pytest tests/test_query_understanding.py tests/test_indexer_search.py tests/test_search.py tests/test_answer.py -q`

Expected: PASS。

- [ ] **Step 5: 提交查询改造**

```bash
git add src/xhbx_rag/query_understanding.py src/xhbx_rag/search.py src/xhbx_rag/answer.py tests/test_query_understanding.py tests/test_indexer_search.py tests/test_search.py tests/test_answer.py
git commit -m "feat: route unified knowledge retrieval by metadata"
```

---

### Task 6: CLI、统一配置与本地脚本

**Files:**
- Modify: `src/xhbx_rag/cli.py:1-25,100-260,400-665`
- Modify: `src/xhbx_rag/config.py:84-117`
- Modify: `src/xhbx_rag/web/services.py:68-152,230-252`
- Modify: `src/xhbx_rag/web/app.py:205-229`
- Modify: `src/xhbx_rag/mcp_server.py:675-704`
- Modify: `scripts/index_parsed.sh`
- Modify: `.env.example`
- Modify: `.env.mcp.example`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.mcp.yml`
- Modify: `docker-compose.offline.yml`
- Modify: `tests/test_cli.py`
- Modify: `tests/test_config.py`
- Modify: `tests/test_web_services.py`
- Modify: `tests/test_web_app.py`
- Modify: `tests/test_mcp_server.py`
- Modify: `tests/test_docker_deployment.py`
- Modify: `tests/test_web_offline_deployment.py`

**Interfaces:**
- Consumes: `normalize_knowledge()`、`index_directory()`、`create_milvus_store(config, collection_name)`。
- Produces: 两个本地命令和所有生产读路径的单一 collection 配置。

- [ ] **Step 1: 写两个 CLI 命令的失败测试**

```python
def test_cli_normalize_knowledge_prints_report_and_returns_result_code(monkeypatch, capsys) -> None:
    monkeypatch.setattr(cli, "normalize_knowledge", lambda input_dir, out: NormalizationResult(True, out / "classification_report.json", 2, 3))
    assert main(["normalize-knowledge", "--input-dir", "parsed", "--out", "parsed_normalized"]) == 0
    assert json.loads(capsys.readouterr().out)["chunks"] == 3


def test_cli_index_dir_passes_batch_size_and_explicit_collection(monkeypatch, capsys) -> None:
    calls: dict[str, object] = {}
    monkeypatch.setattr(cli.RetrievalConfig, "from_env", lambda require_chat=False: object())
    monkeypatch.setattr(cli, "_embedding_client", lambda config: "embedding")
    monkeypatch.setattr(cli, "create_milvus_store", lambda config, collection_name: (config, collection_name))

    def fake_index_directory(chunks_dir, embedding_client, store_factory, collection_name, batch_size, mode):
        calls.update({
            "chunks_dir": chunks_dir,
            "embedding_client": embedding_client,
            "store_collection": store_factory(collection_name)[1],
            "collection_name": collection_name,
            "batch_size": batch_size,
            "mode": mode,
        })
        return DirectoryIndexResult(collection_name, 2, 3, 1024, {"产品知识": 3})

    monkeypatch.setattr(cli, "index_directory", fake_index_directory)
    assert main(["index-dir", "--chunks-dir", "normalized", "--collection-name", "xhbx_knowledge_chunks", "--mode", "rebuild", "--batch-size", "64"]) == 0
    assert calls["collection_name"] == "xhbx_knowledge_chunks"
    assert calls["batch_size"] == 64
    assert calls["mode"] == "rebuild"
```

- [ ] **Step 2: 运行 CLI/config 测试确认失败**

Run: `uv run pytest tests/test_cli.py tests/test_config.py tests/test_web_services.py tests/test_mcp_server.py -q`

Expected: FAIL，parser 不认识新命令且默认 collection 仍是旧值。

- [ ] **Step 3: 注册命令并保证错误返回非零**

```python
normalize_parser = subparsers.add_parser("normalize-knowledge", help="规范化 parsed chunk 的来源与一级标签")
normalize_parser.add_argument("--input-dir", required=True, type=Path)
normalize_parser.add_argument("--out", required=True, type=Path)

index_dir_parser = subparsers.add_parser("index-dir", help="从规范化目录原子重建统一知识库")
index_dir_parser.add_argument("--chunks-dir", required=True, type=Path)
index_dir_parser.add_argument("--collection-name", default="xhbx_knowledge_chunks")
index_dir_parser.add_argument("--mode", choices=["rebuild"], default="rebuild")
index_dir_parser.add_argument("--batch-size", type=int, default=64)
```

`_cmd_normalize_knowledge()` 打印 `success/report_path/input_files/chunks` JSON 并按 success 返回 `0|1`；`_cmd_index_dir()` 使用 `RetrievalConfig.from_env(require_chat=False)`，只创建 embedding client，然后把 `lambda name: create_milvus_store(config, name)` 交给目录入库器。`DirectoryIndexError` 只打印安全摘要并返回 1。

- [ ] **Step 4: 把配置、状态和读路径统一为一个 collection**

将 `MILVUS_COLLECTION` 默认值改为 `xhbx_knowledge_chunks`；`MILVUS_COURSE_COLLECTION` 字段与环境变量继续解析，但 `configured_collection_names()`、Web `_collection_names_for_targets()`、Web upload `indexer_factory` 都只返回/写入 `config.milvus_collection`。MCP/Web status 的 `milvus_collections` 只含统一值；旧 `milvus_course_collection` 状态字段保留一个兼容版本，但不得进入实际 store 列表。

- [ ] **Step 5: 改造本地脚本为两步命令**

```sh
#!/bin/sh
set -eu

PARSED_DIR="${PARSED_DIR:-parsed}"
NORMALIZED_DIR="${NORMALIZED_DIR:-parsed_normalized}"
COLLECTION_NAME="${COLLECTION_NAME:-xhbx_knowledge_chunks}"
BATCH_SIZE="${BATCH_SIZE:-64}"

uv run xhbx-rag normalize-knowledge --input-dir "$PARSED_DIR" --out "$NORMALIZED_DIR"
uv run xhbx-rag index-dir \
  --chunks-dir "$NORMALIZED_DIR" \
  --collection-name "$COLLECTION_NAME" \
  --mode rebuild \
  --batch-size "$BATCH_SIZE"
```

脚本不再逐文件 `index`，从而不会漏掉 `*.chunks.jsonl`，也不会在中途暴露半成品 collection。

- [ ] **Step 6: 运行 CLI、服务和部署配置测试**

Run: `uv run pytest tests/test_cli.py tests/test_config.py tests/test_web_services.py tests/test_web_app.py tests/test_mcp_server.py tests/test_docker_deployment.py tests/test_web_offline_deployment.py -q`

Expected: PASS，所有默认值与状态断言都只把 `xhbx_knowledge_chunks` 作为生产读库。

- [ ] **Step 7: 提交命令和配置**

```bash
git add src/xhbx_rag/cli.py src/xhbx_rag/config.py src/xhbx_rag/web/services.py src/xhbx_rag/web/app.py src/xhbx_rag/mcp_server.py scripts/index_parsed.sh .env.example .env.mcp.example docker-compose.yml docker-compose.mcp.yml docker-compose.offline.yml tests/test_cli.py tests/test_config.py tests/test_web_services.py tests/test_web_app.py tests/test_mcp_server.py tests/test_docker_deployment.py tests/test_web_offline_deployment.py
git commit -m "feat: add unified knowledge indexing commands"
```

---

### Task 7: 文档、真实数据 dry run 与全量回归

**Files:**
- Modify: `README.md:130-170,450-485`
- Test: all repository tests
- Data output: `parsed_normalized/`（本地生成、不得提交）

**Interfaces:**
- Consumes: Tasks 1-6 全部接口和真实 `parsed/`。
- Produces: 可复制的两步操作文档、真实分类报告和完整测试证据。

- [ ] **Step 1: 更新 README 的本地操作合同**

README 必须包含：

```bash
uv run xhbx-rag normalize-knowledge \
  --input-dir parsed \
  --out parsed_normalized

uv run xhbx-rag index-dir \
  --chunks-dir parsed_normalized \
  --collection-name xhbx_knowledge_chunks \
  --mode rebuild \
  --batch-size 64
```

同时说明：不重新切片；`parsed/chunk` 为培训资料；其他目录为绩优案例；只有七个一级标签；先审查 `classification_report.json`；旧两个 collection 在迁移验证前保留；真实 embedding/切换有成本，不在 dry run 阶段自动执行。

- [ ] **Step 2: 运行聚焦测试集**

Run:

```bash
uv run pytest tests/test_knowledge_domain.py \
  tests/test_knowledge_normalizer.py \
  tests/test_directory_indexer.py \
  tests/test_milvus_store.py \
  tests/test_query_understanding.py \
  tests/test_indexer_search.py -q
```

Expected: PASS。

- [ ] **Step 3: 对真实 parsed 执行无 embedding 规范化**

Run:

```bash
rm -rf /tmp/xhbx-parsed-normalized-check
uv run xhbx-rag normalize-knowledge \
  --input-dir parsed \
  --out /tmp/xhbx-parsed-normalized-check
```

Expected: exit 0；报告发现 1,078 个文件；977 个培训文件、101 个绩优案例文件；18,930 条输入记录中 260 条同来源重复版本为 `deduplicated_chunk_id`，得到 18,670 条唯一 chunk；两个已知空文件为 `skipped_empty`；所有有效 chunk 都满足领域合同；无 invalid、跨来源冲突或 unclassified。若真实规则仍有 unclassified，只调整可解释关键词规则并新增对应回归测试，不添加兜底分类。

- [ ] **Step 4: 审计真实报告与幂等性**

Run:

```bash
uv run xhbx-rag normalize-knowledge \
  --input-dir parsed \
  --out /tmp/xhbx-parsed-normalized-check-2
diff -ru /tmp/xhbx-parsed-normalized-check /tmp/xhbx-parsed-normalized-check-2
```

Expected: `diff` 无输出。用 `classification_report.json` 检查七个领域分布、两种来源、multi-domain 数量和每类样本，不输出完整客户正文。

- [ ] **Step 5: 运行全量仓库回归**

Run: `uv run pytest -q`

Expected: PASS，无失败、无 error。

- [ ] **Step 6: 检查工作树和 diff 范围**

Run:

```bash
git status --short
git diff --check
git diff --stat HEAD~6..HEAD
```

Expected: `git diff --check` 无输出；仅有本计划范围内代码、测试和文档，用户原有 `outputs/` 保持未跟踪且未修改。

- [ ] **Step 7: 提交文档与最终规则校准**

```bash
git add README.md src/xhbx_rag/knowledge_domain.py tests/test_knowledge_domain.py
git commit -m "docs: document unified knowledge migration"
```

若 Step 3 没有引发规则校准，只提交 `README.md`。不执行真实 `index-dir`，除非用户明确同意产生 embedding 成本和切换本地 collection。
