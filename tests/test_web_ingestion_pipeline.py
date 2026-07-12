import json
from pathlib import Path
from typing import Callable

import pytest

from xhbx_rag.chunk_io import load_chunks_jsonl
from xhbx_rag.course_parser import CourseParseReport
from xhbx_rag.models import RagChunk
from xhbx_rag.sales_generation import CaseSalesGenerationResult
from xhbx_rag.web.ingestion_pipeline import IngestionPipeline, IngestionPipelineError
from xhbx_rag.web.ingestion_uploads import IngestionLimits, preflight_upload


def course_chunk(chunk_id: str, **changes: object) -> RagChunk:
    values: dict[str, object] = {
        "chunk_id": chunk_id,
        "chunk_type": "training_course",
        "text": "课程内容：促成与异议处理",
        "metadata": {
            "course_name": "促成课",
            "course_series": "新人培训",
            "audience": "新人",
        },
        "citations": [],
        "source_file": "新人培训/促成课.txt",
    }
    values.update(changes)
    return RagChunk.model_validate(values)


def _job_from_source(source: Path, target: str) -> dict[str, object]:
    preflight = preflight_upload(
        source,
        target=target,  # type: ignore[arg-type]
        limits=IngestionLimits(),
    )
    return {
        "job_id": "job-1",
        "source_name": preflight.source_name,
        "source_kind": preflight.source_kind,
        "source_path": source,
        "target": preflight.target,
        "items": [
            {
                "item_index": item.item_index,
                "unit_key": item.unit_key,
                "display_name": item.display_name,
                "relative_paths": list(item.relative_paths),
                "document_count": item.document_count,
            }
            for item in preflight.items
        ],
        "ignored_entries": list(preflight.ignored_entries),
    }


def case_job(tmp_path: Path) -> dict[str, object]:
    source = tmp_path / "王女士.txt"
    source.write_text("客户经营与需求分析", encoding="utf-8")
    return _job_from_source(source, "case")


def course_job(tmp_path: Path) -> dict[str, object]:
    source = tmp_path / "促成课.txt"
    source.write_text("促成动作与异议处理" * 20, encoding="utf-8")
    return _job_from_source(source, "course")


def pipeline_with_case_result(status: str) -> IngestionPipeline:
    def case_generation_factory(
        config: object,
    ) -> Callable[..., CaseSalesGenerationResult]:
        del config

        def generate(**kwargs: object) -> CaseSalesGenerationResult:
            case_name = str(kwargs["case_name"])
            output_dir = Path(kwargs["output_dir"])  # type: ignore[arg-type]
            if status != "ok":
                return CaseSalesGenerationResult(case_name=case_name, status=status)
            output_dir.mkdir(parents=True, exist_ok=True)
            insights_path = output_dir / "case.sales_insights.json"
            insights_path.write_text(
                json.dumps(
                    {
                        "case_name": case_name,
                        "scripts": [
                            {
                                "script_id": "script-1",
                                "stage": "促成",
                                "scenario": "客户犹豫",
                                "coach_wording": "我们先看您最关心的部分。",
                            }
                        ],
                    },
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            return CaseSalesGenerationResult(
                case_name=case_name,
                status="ok",
                insights_path=insights_path,
            )

        return generate

    return IngestionPipeline(
        config_provider=object,
        case_generation_factory=case_generation_factory,
        course_enrichment_factory=lambda config: None,
    )


def pipeline_with_course_report(
    chunks: list[RagChunk],
    enrich_failures: list[str],
    *,
    course_enrichment_factory: Callable[[object], object] | None = None,
) -> IngestionPipeline:
    def course_parser(
        course_dir: Path,
        out_dir: Path,
        enrichment_agent: object = None,
        trace: object = None,
        *,
        fail_fast: bool = False,
        on_file: Callable[[str, str, int], None] | None = None,
    ) -> CourseParseReport:
        del course_dir, enrichment_agent, trace
        assert fail_fast is True
        out_dir.mkdir(parents=True, exist_ok=True)
        chunks_path = out_dir / "chunks.jsonl"
        chunks_path.write_text(
            "".join(
                json.dumps(chunk.model_dump(mode="python"), ensure_ascii=True) + "\n"
                for chunk in chunks
            ),
            encoding="utf-8",
        )
        if on_file is not None:
            on_file("促成课.txt", "parsed", len(chunks))
        return CourseParseReport(
            input_dir="course",
            output_files={
                "chunks": str(chunks_path),
                "report": str(out_dir / "parse_report.json"),
            },
            counts={
                "files_parsed": 1,
                "files_skipped": 0,
                "files_failed": 0,
                "chunks": len(chunks),
                "duplicate_text_hashes": 0,
            },
            enrich_failures=enrich_failures,
        )

    return IngestionPipeline(
        config_provider=object,
        case_generation_factory=lambda config: None,
        course_enrichment_factory=course_enrichment_factory or (lambda config: None),
        course_parser=course_parser,
    )


def test_case_partial_generation_fails_entire_prepare(tmp_path: Path) -> None:
    pipeline = pipeline_with_case_result(status="partial")

    with pytest.raises(IngestionPipelineError, match="案例洞察生成不完整"):
        pipeline.prepare(case_job(tmp_path), tmp_path / "attempt")

    assert not (tmp_path / "attempt" / "staging" / "chunks.jsonl").exists()


def test_case_success_runs_real_parse_normalize_and_chunk(tmp_path: Path) -> None:
    pipeline = pipeline_with_case_result(status="ok")

    prepared = pipeline.prepare(case_job(tmp_path), tmp_path / "attempt")

    chunks = load_chunks_jsonl(prepared.chunks_path)
    assert prepared.chunk_count == 1
    assert chunks[0].chunk_type == "script"
    assert chunks[0].metadata["case_name"] == "王女士"


def test_course_enrichment_warning_keeps_prepare_successful(tmp_path: Path) -> None:
    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("course__a__0001")],
        enrich_failures=["促成课: 模型不可用"],
    )

    prepared = pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    assert prepared.chunk_count == 1
    assert prepared.warning_count == 1
    assert prepared.warnings == ("促成课: 模型不可用",)


def test_course_enrichment_factory_failure_degrades_to_warning(tmp_path: Path) -> None:
    def unavailable(config: object) -> object:
        del config
        raise RuntimeError("模型客户端不可用")

    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("course__a__0001")],
        enrich_failures=[],
        course_enrichment_factory=unavailable,
    )

    prepared = pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    assert prepared.chunk_count == 1
    assert prepared.warnings == ("课程增值服务不可用",)


def test_duplicate_chunk_ids_fail_before_index(tmp_path: Path) -> None:
    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("duplicate"), course_chunk("duplicate")],
        enrich_failures=[],
    )

    with pytest.raises(IngestionPipelineError, match="chunk_id 重复"):
        pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    assert not (tmp_path / "attempt" / "staging" / "chunks.jsonl").exists()


def test_invalid_item_chunks_fail_item_before_completed_event(tmp_path: Path) -> None:
    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("empty", text="   ")],
        enrich_failures=[],
    )
    events: list[tuple[str, dict[str, object]]] = []

    with pytest.raises(IngestionPipelineError) as captured:
        pipeline.prepare(
            course_job(tmp_path),
            tmp_path / "attempt",
            on_event=lambda event, payload: events.append((event, payload)),
        )

    assert captured.value.item_index == 1
    assert [event for event, _ in events].count("item_failed") == 1
    assert "item_completed" not in [event for event, _ in events]


@pytest.mark.parametrize(
    ("chunk", "message"),
    [
        (course_chunk("empty", text="   "), "chunk 文本为空"),
        (
            course_chunk("wrong-type", chunk_type="script"),
            "chunk_type 与目标知识库不匹配",
        ),
        (course_chunk("bad-utf8", text="\ud800"), "UTF-8"),
        (course_chunk("bad-source", source_file="\ud800"), "UTF-8"),
        (
            course_chunk("too-wide", metadata={"case_name": "课" * 513}),
            "case_name 超过 Milvus 字段上限",
        ),
        (
            course_chunk("metadata-wide", metadata={"payload": "课" * 22_000}),
            "metadata_json 超过 Milvus 字段上限",
        ),
    ],
)
def test_full_batch_validation_rejects_invalid_chunk(
    tmp_path: Path, chunk: RagChunk, message: str
) -> None:
    pipeline = pipeline_with_course_report(chunks=[chunk], enrich_failures=[])

    with pytest.raises(IngestionPipelineError, match=message):
        pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    assert not (tmp_path / "attempt" / "staging" / "chunks.jsonl").exists()


def test_full_batch_validation_rejects_non_standard_json_numbers(
    tmp_path: Path,
) -> None:
    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("nan", metadata={"score": float("nan")})],
        enrich_failures=[],
    )

    with pytest.raises(IngestionPipelineError, match="metadata 无法 JSON 序列化"):
        pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    assert not (tmp_path / "attempt" / "staging" / "chunks.jsonl").exists()
