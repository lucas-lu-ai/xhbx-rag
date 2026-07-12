from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import os
import shutil
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, cast

from xhbx_rag.chunk_builder import build_chunks
from xhbx_rag.chunk_io import ChunkLoadError, load_chunks_jsonl
from xhbx_rag.config import RetrievalConfig
from xhbx_rag.course_enrichment import (
    CourseEnrichmentAgent,
    CourseEnrichmentAgentScopeAgent,
)
from xhbx_rag.course_parser import (
    CourseFileParseError,
    CourseParseReport,
    parse_course_dir,
)
from xhbx_rag.models import RagChunk
from xhbx_rag.milvus_store import MilvusChunkRecord
from xhbx_rag.normalizer import normalize_case
from xhbx_rag.observability import TraceSink
from xhbx_rag.parser import ParseFatalError, parse_inputs
from xhbx_rag.report import build_parse_report
from xhbx_rag.sales_generation import (
    CaseSalesGenerationResult,
    SalesInsightAgentScopeAgent,
    VisionImageDescriptionAgent,
    generate_case_sales_insights,
)
from xhbx_rag.web.ingestion_uploads import (
    IngestionLimits,
    PreflightItem,
    PreflightResult,
    UploadValidationError,
    materialize_attempt_inputs,
)
from xhbx_rag.writer import write_outputs


_CASE_CHUNK_TYPES = frozenset(
    {"customer_journey", "strategy", "script", "objection_handling"}
)
_MILVUS_FIELD_MAX_BYTES = {
    "chunk_id": 512,
    "text": 65_535,
    "case_name": 512,
    "chunk_type": 64,
    "stage": 256,
    "scenario": 512,
    "metadata_json": 65_535,
    "citations_json": 65_535,
}

ConfigProvider = Callable[[], object]
CaseGenerator = Callable[..., CaseSalesGenerationResult | Any]
CaseGenerationFactory = Callable[[object], CaseGenerator]
CourseEnrichmentFactory = Callable[[object], CourseEnrichmentAgent | None]
UploadMaterializer = Callable[..., dict[int, Path]]
CourseParser = Callable[..., CourseParseReport]
EventCallback = Callable[[str, dict[str, object]], None]


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


class IngestionPipeline:
    def __init__(
        self,
        config_provider: ConfigProvider = RetrievalConfig.from_env,
        case_generation_factory: CaseGenerationFactory | None = None,
        course_enrichment_factory: CourseEnrichmentFactory | None = None,
        upload_materializer: UploadMaterializer = materialize_attempt_inputs,
        *,
        course_parser: CourseParser = parse_course_dir,
        limits: IngestionLimits | None = None,
        trace: TraceSink | None = None,
    ) -> None:
        self.config_provider = config_provider
        self.case_generation_factory = (
            case_generation_factory or _default_case_generation_factory
        )
        self.course_enrichment_factory = (
            course_enrichment_factory or _default_course_enrichment_factory
        )
        self.upload_materializer = upload_materializer
        self.course_parser = course_parser
        self.limits = limits or IngestionLimits()
        self.trace = trace

    def prepare(
        self,
        job: Mapping[str, object],
        attempt_dir: Path,
        *,
        on_event: EventCallback | None = None,
    ) -> PreparedIngestion:
        staging_dir = attempt_dir / "staging"
        try:
            try:
                _clear_staging_artifacts(staging_dir)
            except OSError as exc:
                raise IngestionPipelineError(
                    "chunk_failed", "staging 工作区清理失败"
                ) from exc
            preflight, source_path = _preflight_from_job(job)
            try:
                materialized = self.upload_materializer(
                    source_path,
                    preflight,
                    attempt_dir,
                    limits=self.limits,
                )
            except (OSError, UploadValidationError, ValueError) as exc:
                raise IngestionPipelineError(
                    "upload_invalid", "入库输入物化失败"
                ) from exc
            _validate_materialized_items(preflight.items, materialized)

            all_chunks: list[RagChunk] = []
            warnings: list[str] = []
            seen_chunk_ids: set[str] = set()
            if preflight.target == "case":
                config = _load_required_config(self.config_provider)
                generator = _create_case_generator(
                    self.case_generation_factory, config
                )
                for item in preflight.items:
                    chunks, item_warnings = self._prepare_case_item(
                        item,
                        materialized[item.item_index],
                        attempt_dir,
                        generator,
                        on_event,
                        seen_chunk_ids,
                    )
                    all_chunks.extend(chunks)
                    warnings.extend(item_warnings)
            else:
                enrichment_agent, initialization_warnings = (
                    _create_optional_course_enrichment(
                        self.config_provider,
                        self.course_enrichment_factory,
                    )
                )
                warnings.extend(initialization_warnings)
                for item in preflight.items:
                    chunks, item_warnings = self._prepare_course_item(
                        item,
                        materialized[item.item_index],
                        attempt_dir,
                        enrichment_agent,
                        on_event,
                        seen_chunk_ids,
                    )
                    all_chunks.extend(chunks)
                    warnings.extend(item_warnings)

            _validate_batch(all_chunks, preflight.target)
            try:
                chunks_path = _publish_staging(staging_dir, all_chunks)
            except Exception as exc:  # noqa: BLE001 - 对外只暴露固定错误
                raise IngestionPipelineError(
                    "chunk_failed", "staging 产物发布失败"
                ) from exc
            return PreparedIngestion(
                chunks_path=chunks_path,
                chunk_count=len(all_chunks),
                warning_count=len(warnings),
                warnings=tuple(warnings),
            )
        except BaseException:
            try:
                _clear_staging_artifacts(staging_dir)
            except OSError:
                pass
            raise

    def _prepare_case_item(
        self,
        item: PreflightItem,
        case_dir: Path,
        attempt_dir: Path,
        generator: CaseGenerator,
        on_event: EventCallback | None,
        seen_chunk_ids: set[str],
    ) -> tuple[list[RagChunk], list[str]]:
        _emit_item_event(on_event, "item_started", item, stage="parsing")
        generated_dir = _item_output_dir(attempt_dir / "generated", item)
        parsed_dir = _item_output_dir(attempt_dir / "parsed", item)
        try:
            _prepare_output_dir(generated_dir)
            _prepare_output_dir(parsed_dir)
        except OSError as exc:
            error = IngestionPipelineError(
                "parse_failed", "案例工作区初始化失败", item.item_index
            )
            _emit_failure(on_event, item, error)
            raise error from exc
        try:
            result = generator(
                case_dir=case_dir,
                output_dir=generated_dir,
                case_name=item.display_name,
                trace=self.trace,
            )
            if inspect.isawaitable(result):
                result = asyncio.run(result)
        except Exception as exc:  # noqa: BLE001 - 对外只报告安全错误
            error = IngestionPipelineError(
                "parse_failed", "案例洞察生成失败", item.item_index
            )
            _emit_failure(on_event, item, error)
            raise error from exc

        if not isinstance(result, CaseSalesGenerationResult) or result.status != "ok":
            error = IngestionPipelineError(
                "parse_failed", "案例洞察生成不完整", item.item_index
            )
            _emit_failure(on_event, item, error)
            raise error
        if result.insights_path is None:
            error = IngestionPipelineError(
                "parse_failed", "案例洞察产物缺失", item.item_index
            )
            _emit_failure(on_event, item, error)
            raise error

        try:
            insights_path = _require_confined_regular_file(
                result.insights_path, generated_dir
            )
        except (OSError, ValueError) as exc:
            error = IngestionPipelineError(
                "parse_failed", "案例洞察产物无效", item.item_index
            )
            _emit_failure(on_event, item, error)
            raise error from exc
        playbook_path: Path | None = None
        if result.playbook_path is not None:
            try:
                playbook_path = _require_confined_regular_file(
                    result.playbook_path, generated_dir
                )
            except (OSError, ValueError) as exc:
                error = IngestionPipelineError(
                    "parse_failed", "案例 playbook 产物无效", item.item_index
                )
                _emit_failure(on_event, item, error)
                raise error from exc

        try:
            parsed = parse_inputs(insights_path, playbook_path)
            knowledge = normalize_case(parsed)
        except (OSError, UnicodeError, ParseFatalError, ValueError) as exc:
            error = IngestionPipelineError(
                "parse_failed", "案例结构化解析失败", item.item_index
            )
            _emit_failure(on_event, item, error)
            raise error from exc
        try:
            chunks = build_chunks(knowledge)
            if not chunks:
                raise ValueError("案例未产生 chunk")
            report = build_parse_report(
                insights_path,
                playbook_path,
                parsed_dir,
                knowledge,
                chunks,
                parsed.warnings,
            )
            write_outputs(parsed_dir, knowledge, chunks, report)
        except Exception as exc:  # noqa: BLE001
            # 切分/写中间产物均是必需步骤。
            error = IngestionPipelineError(
                "chunk_failed", "案例切分失败", item.item_index
            )
            _emit_failure(on_event, item, error)
            raise error from exc
        _validate_item_chunks(
            chunks, "case", item, on_event, seen_chunk_ids
        )
        _emit_item_completed(on_event, item, len(chunks), len(parsed.warnings))
        return chunks, list(parsed.warnings)

    def _prepare_course_item(
        self,
        item: PreflightItem,
        course_dir: Path,
        attempt_dir: Path,
        enrichment_agent: CourseEnrichmentAgent | None,
        on_event: EventCallback | None,
        seen_chunk_ids: set[str],
    ) -> tuple[list[RagChunk], list[str]]:
        _emit_item_event(on_event, "item_started", item, stage="parsing")
        parsed_dir = _item_output_dir(attempt_dir / "parsed", item)
        try:
            _prepare_output_dir(parsed_dir)
        except OSError as exc:
            error = IngestionPipelineError(
                "parse_failed", "课程工作区初始化失败", item.item_index
            )
            _emit_failure(on_event, item, error)
            raise error from exc

        def on_file(relative_path: str, status: str, chunk_count: int) -> None:
            if on_event is not None:
                on_event(
                    "course_file",
                    {
                        "item_index": item.item_index,
                        "relative_path": relative_path,
                        "status": status,
                        "chunk_count": chunk_count,
                    },
                )

        try:
            report = self.course_parser(
                course_dir,
                parsed_dir,
                enrichment_agent,
                self.trace,
                fail_fast=True,
                on_file=on_file,
            )
            if report.counts.get("files_failed", 0) or report.counts.get(
                "files_parsed", 0
            ) < 1:
                raise CourseFileParseError(
                    item.unit_key, "课程文件解析不完整"
                )
        except Exception as exc:  # noqa: BLE001
            # 受支持课程文件必须全部成功。
            error = IngestionPipelineError(
                "parse_failed", "课程文件解析或切分失败", item.item_index
            )
            _emit_failure(on_event, item, error)
            raise error from exc
        try:
            chunks_value = report.output_files.get("chunks")
            report_value = report.output_files.get("report")
            if not chunks_value or not report_value:
                raise ValueError("课程解析产物缺失")
            chunks_path = _require_confined_regular_file(
                chunks_value, parsed_dir
            )
            _require_confined_regular_file(report_value, parsed_dir)
            chunks = load_chunks_jsonl(chunks_path)
            if not chunks:
                raise ChunkLoadError("课程未产生 chunk")
        except (ChunkLoadError, OSError, UnicodeError, ValueError) as exc:
            error = IngestionPipelineError(
                "parse_failed", "课程解析产物无效", item.item_index
            )
            _emit_failure(on_event, item, error)
            raise error from exc
        item_warnings = list(report.enrich_failures)
        _validate_item_chunks(
            chunks, "course", item, on_event, seen_chunk_ids
        )
        _emit_item_completed(on_event, item, len(chunks), len(item_warnings))
        return chunks, item_warnings


def _default_case_generation_factory(config: object) -> CaseGenerator:
    resolved = cast(RetrievalConfig, config)
    agent = SalesInsightAgentScopeAgent(
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        model=resolved.model_name,
    )
    vision_agent = (
        VisionImageDescriptionAgent(
            base_url=resolved.base_url,
            api_key=resolved.api_key,
            model=resolved.vision_model_name,
        )
        if resolved.vision_model_name
        else None
    )

    def generate(**kwargs: object) -> CaseSalesGenerationResult:
        return generate_case_sales_insights(
            case_dir=cast(Path, kwargs["case_dir"]),
            output_dir=cast(Path, kwargs["output_dir"]),
            case_name=cast(str, kwargs["case_name"]),
            section_agent=agent,
            case_agent=agent,
            vision_agent=vision_agent,
            trace=cast(TraceSink | None, kwargs.get("trace")),
        )

    return generate


def _default_course_enrichment_factory(
    config: object,
) -> CourseEnrichmentAgentScopeAgent:
    resolved = cast(RetrievalConfig, config)
    return CourseEnrichmentAgentScopeAgent(
        base_url=resolved.base_url,
        api_key=resolved.api_key,
        model=resolved.model_name,
    )


def _create_case_generator(
    factory: CaseGenerationFactory, config: object
) -> CaseGenerator:
    try:
        generator = factory(config)
    except Exception as exc:  # noqa: BLE001
        # 工厂可能初始化外部模型客户端。
        raise IngestionPipelineError(
            "parse_failed", "案例加工服务无法初始化"
        ) from exc
    if not callable(generator):
        raise IngestionPipelineError(
            "parse_failed", "案例加工服务无效"
        )
    return generator


def _load_required_config(provider: ConfigProvider) -> object:
    try:
        return provider()
    except Exception as exc:  # noqa: BLE001 - 不向 Web 暴露配置细节
        raise IngestionPipelineError(
            "parse_failed", "入库加工配置无法加载"
        ) from exc


def _create_optional_course_enrichment(
    provider: ConfigProvider,
    factory: CourseEnrichmentFactory,
) -> tuple[CourseEnrichmentAgent | None, list[str]]:
    try:
        config = provider()
    except Exception:  # noqa: BLE001 - 课程增值不是必需步骤
        return None, ["课程增值服务不可用"]
    return _create_course_enrichment(factory, config)


def _create_course_enrichment(
    factory: CourseEnrichmentFactory, config: object
) -> tuple[CourseEnrichmentAgent | None, list[str]]:
    try:
        agent = factory(config)
    except Exception:  # noqa: BLE001 - 课程增值不是必需步骤
        return None, ["课程增值服务不可用"]
    if agent is not None and not callable(getattr(agent, "enrich", None)):
        return None, ["课程增值服务不可用"]
    return agent, []


def _preflight_from_job(
    job: Mapping[str, object],
) -> tuple[PreflightResult, Path]:
    target = job.get("target")
    source_kind = job.get("source_kind")
    source_name = job.get("source_name")
    source_path_value = job.get("source_path")
    raw_items = job.get("items")
    if target not in ("case", "course"):
        raise IngestionPipelineError("upload_invalid", "入库目标无效")
    if source_kind not in ("file", "zip"):
        raise IngestionPipelineError("upload_invalid", "上传来源类型无效")
    if not isinstance(source_name, str) or not source_name.strip():
        raise IngestionPipelineError("upload_invalid", "上传文件名无效")
    if not isinstance(source_path_value, (str, Path)):
        raise IngestionPipelineError("upload_invalid", "上传源文件路径无效")
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        raise IngestionPipelineError("upload_invalid", "入库输入项无效")

    items: list[PreflightItem] = []
    try:
        for raw_item in raw_items:
            if not isinstance(raw_item, Mapping):
                raise TypeError
            relative_paths = raw_item.get("relative_paths")
            if not isinstance(relative_paths, Sequence) or isinstance(
                relative_paths, (str, bytes)
            ):
                raise TypeError
            item = PreflightItem(
                item_index=int(raw_item["item_index"]),
                unit_key=str(raw_item["unit_key"]),
                display_name=str(raw_item["display_name"]),
                relative_paths=tuple(str(path) for path in relative_paths),
                document_count=int(raw_item["document_count"]),
            )
            if (
                item.item_index < 1
                or not item.unit_key
                or not item.display_name
                or not item.relative_paths
                or item.document_count != len(item.relative_paths)
            ):
                raise ValueError
            items.append(item)
    except (KeyError, TypeError, ValueError) as exc:
        raise IngestionPipelineError("upload_invalid", "入库输入项无效") from exc
    indexes = [item.item_index for item in items]
    if not items or indexes != list(range(1, len(items) + 1)):
        raise IngestionPipelineError("upload_invalid", "入库输入项序号无效")

    ignored = job.get("ignored_entries", ())
    if not isinstance(ignored, Sequence) or isinstance(ignored, (str, bytes)):
        ignored = ()
    preflight = PreflightResult(
        source_name=source_name,
        source_kind=cast(Literal["file", "zip"], source_kind),
        target=cast(Literal["case", "course"], target),
        items=tuple(items),
        ignored_entries=tuple(str(path) for path in ignored),
    )
    return preflight, Path(source_path_value)


def _validate_materialized_items(
    items: tuple[PreflightItem, ...], materialized: Mapping[int, Path]
) -> None:
    expected = {item.item_index for item in items}
    if set(materialized) != expected:
        raise IngestionPipelineError(
            "upload_invalid", "入库输入物化结果不完整"
        )
    if any(not Path(path).is_dir() for path in materialized.values()):
        raise IngestionPipelineError("upload_invalid", "入库输入目录无效")


def _item_output_dir(root: Path, item: PreflightItem) -> Path:
    digest = hashlib.sha256(item.unit_key.encode("utf-8")).hexdigest()[:12]
    return root / f"item-{item.item_index:04d}-{digest}"


def _prepare_output_dir(path: Path) -> None:
    attempt_root = path.parent.parent
    if attempt_root.is_symlink() or path.parent.is_symlink():
        raise OSError("工作区路径无效")
    attempt_root.mkdir(parents=True, exist_ok=True)
    resolved_attempt = attempt_root.resolve(strict=True)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.parent.is_symlink() or not path.parent.resolve(strict=True).is_relative_to(
        resolved_attempt
    ):
        raise OSError("工作区路径无效")
    _remove_path(path)
    path.mkdir()
    if path.is_symlink() or not path.resolve(strict=True).is_relative_to(
        resolved_attempt
    ):
        raise OSError("工作区路径无效")


def _require_confined_regular_file(path_value: object, root: Path) -> Path:
    try:
        path = Path(os.fspath(path_value))  # type: ignore[arg-type]
    except TypeError as exc:
        raise ValueError("产物路径无效") from exc
    if root.is_symlink() or not root.is_dir() or path.is_symlink():
        raise ValueError("产物路径无效")
    try:
        resolved_root = root.resolve(strict=True)
        resolved_path = path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("产物路径无效") from exc
    if not resolved_path.is_relative_to(resolved_root) or not resolved_path.is_file():
        raise ValueError("产物路径无效")
    return resolved_path


def _validate_batch(chunks: list[RagChunk], target: Literal["case", "course"]) -> None:
    if not chunks:
        raise IngestionPipelineError("chunk_failed", "staging chunk 不能为空")
    seen: set[str] = set()
    allowed_types = _CASE_CHUNK_TYPES if target == "case" else {"training_course"}
    for position, chunk in enumerate(chunks, start=1):
        if not chunk.chunk_id.strip():
            raise IngestionPipelineError(
                "chunk_failed", f"第 {position} 个 chunk_id 为空"
            )
        if chunk.chunk_id in seen:
            raise IngestionPipelineError(
                "chunk_failed", f"chunk_id 重复: {chunk.chunk_id}"
            )
        seen.add(chunk.chunk_id)
        if not chunk.text.strip():
            raise IngestionPipelineError(
                "chunk_failed", f"chunk 文本为空: {chunk.chunk_id}"
            )
        if not chunk.source_file.strip():
            raise IngestionPipelineError(
                "chunk_failed", f"chunk source_file 为空: {chunk.chunk_id}"
            )
        if chunk.chunk_type not in allowed_types:
            raise IngestionPipelineError(
                "chunk_failed",
                f"chunk_type 与目标知识库不匹配: {chunk.chunk_id}",
            )
        _validate_chunk_json_and_fields(chunk)


def _validate_item_chunks(
    chunks: list[RagChunk],
    target: Literal["case", "course"],
    item: PreflightItem,
    on_event: EventCallback | None,
    seen_chunk_ids: set[str],
) -> None:
    try:
        _validate_batch(chunks, target)
        duplicate = next(
            (chunk.chunk_id for chunk in chunks if chunk.chunk_id in seen_chunk_ids),
            None,
        )
        if duplicate is not None:
            raise IngestionPipelineError(
                "chunk_failed", f"chunk_id 重复: {duplicate}"
            )
    except IngestionPipelineError as exc:
        error = IngestionPipelineError(exc.code, exc.detail, item.item_index)
        _emit_failure(on_event, item, error)
        raise error from exc
    seen_chunk_ids.update(chunk.chunk_id for chunk in chunks)


def _validate_chunk_json_and_fields(chunk: RagChunk) -> None:
    try:
        chunk.source_file.encode("utf-8")
        chunk.chunk_id.encode("utf-8")
        chunk.text.encode("utf-8")
    except UnicodeEncodeError as exc:
        raise IngestionPipelineError(
            "chunk_failed", "chunk 字段无法编码为 UTF-8"
        ) from exc
    try:
        for citation in chunk.citations:
            citation.model_dump(mode="json")
    except (RecursionError, TypeError, ValueError) as exc:
        raise IngestionPipelineError(
            "chunk_failed", f"citations 无法 JSON 序列化: {chunk.chunk_id}"
        ) from exc

    try:
        row = MilvusChunkRecord.from_chunk(chunk, [0.0]).to_row()
    except (RecursionError, TypeError, UnicodeError, ValueError) as exc:
        raise IngestionPipelineError(
            "chunk_failed", f"metadata 无法 JSON 序列化: {chunk.chunk_id}"
        ) from exc

    metadata_json = str(row["metadata_json"])
    citations_json = str(row["citations_json"])
    try:
        json.loads(metadata_json, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise IngestionPipelineError(
            "chunk_failed", f"metadata 无法 JSON 序列化: {chunk.chunk_id}"
        ) from exc
    try:
        json.loads(citations_json, parse_constant=_reject_json_constant)
    except (json.JSONDecodeError, ValueError) as exc:
        raise IngestionPipelineError(
            "chunk_failed", f"citations 无法 JSON 序列化: {chunk.chunk_id}"
        ) from exc

    fields = {
        "chunk_id": row["chunk_id"],
        "text": row["text"],
        "case_name": row["case_name"],
        "chunk_type": row["chunk_type"],
        "stage": row["stage"],
        "scenario": row["scenario"],
        "metadata_json": metadata_json,
        "citations_json": citations_json,
    }
    for field_name, value in fields.items():
        if not isinstance(value, str):
            raise IngestionPipelineError(
                "chunk_failed", f"Milvus 字段类型无效: {field_name}"
            )
        try:
            encoded = value.encode("utf-8")
        except UnicodeEncodeError as exc:
            raise IngestionPipelineError(
                "chunk_failed", f"chunk 字段无法编码为 UTF-8: {field_name}"
            ) from exc
        if len(encoded) > _MILVUS_FIELD_MAX_BYTES[field_name]:
            raise IngestionPipelineError(
                "chunk_failed",
                f"{field_name} 超过 Milvus 字段上限: {chunk.chunk_id}",
            )


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"非标准 JSON 常量: {value}")


def _publish_staging(staging_dir: Path, chunks: list[RagChunk]) -> Path:
    lines = [
        json.dumps(
            chunk.model_dump(mode="json"),
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
        for chunk in chunks
    ]
    parent = staging_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    temporary_dir = parent / f".staging.tmp-{uuid.uuid4().hex}"
    temporary_dir.mkdir()
    temporary_chunks_path = temporary_dir / "chunks.jsonl"
    try:
        with temporary_chunks_path.open("x", encoding="utf-8") as output:
            output.write("\n".join(lines) + "\n")
            output.flush()
            os.fsync(output.fileno())
        _fsync_directory(temporary_dir)
        os.replace(temporary_dir, staging_dir)
        _fsync_directory(parent)
    except BaseException:
        _remove_path(temporary_dir)
        _remove_path(staging_dir)
        raise
    return staging_dir / "chunks.jsonl"


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _emit_item_event(
    callback: EventCallback | None,
    event_type: str,
    item: PreflightItem,
    *,
    stage: str,
) -> None:
    if callback is not None:
        callback(
            event_type,
            {
                "item_index": item.item_index,
                "display_name": item.display_name,
                "stage": stage,
            },
        )


def _emit_item_completed(
    callback: EventCallback | None,
    item: PreflightItem,
    chunk_count: int,
    warning_count: int,
) -> None:
    if callback is not None:
        callback(
            "item_completed",
            {
                "item_index": item.item_index,
                "chunk_count": chunk_count,
                "warning_count": warning_count,
            },
        )


def _emit_failure(
    callback: EventCallback | None,
    item: PreflightItem,
    error: IngestionPipelineError,
) -> None:
    if callback is not None:
        callback(
            "item_failed",
            {
                "item_index": item.item_index,
                "code": error.code,
                "detail": error.detail,
            },
        )


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink(missing_ok=True)
    elif path.exists():
        shutil.rmtree(path)


def _clear_staging_artifacts(staging_dir: Path) -> None:
    _remove_path(staging_dir)
    parent = staging_dir.parent
    if not parent.exists():
        return
    for temporary_dir in parent.glob(".staging.tmp-*"):
        _remove_path(temporary_dir)
