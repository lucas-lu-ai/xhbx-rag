import json
import logging
import os
import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Callable
from zipfile import ZipFile

import pytest

from xhbx_rag.chunk_io import load_chunks_jsonl
from xhbx_rag.course_parser import CourseParseReport
from xhbx_rag.milvus_store import MilvusChunkRecord
from xhbx_rag.models import EvidenceRef, RagChunk
from xhbx_rag.sales_generation import CaseSalesGenerationResult
from xhbx_rag.web import ingestion_pipeline as pipeline_module
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


def two_course_job(tmp_path: Path) -> dict[str, object]:
    source = tmp_path / "课程.zip"
    with ZipFile(source, "w") as archive:
        archive.writestr("b.txt", "课程 B" * 20)
        archive.writestr("a.txt", "课程 A" * 20)
    return _job_from_source(source, "course")


def _write_course_outputs(out_dir: Path, chunks: list[RagChunk]) -> dict[str, str]:
    out_dir.mkdir(parents=True, exist_ok=True)
    chunks_path = out_dir / "chunks.jsonl"
    report_path = out_dir / "parse_report.json"
    chunks_path.write_text(
        "".join(
            json.dumps(chunk.model_dump(mode="python"), ensure_ascii=True) + "\n"
            for chunk in chunks
        ),
        encoding="utf-8",
    )
    report_path.write_text("{}\n", encoding="utf-8")
    return {"chunks": str(chunks_path), "report": str(report_path)}


def _write_case_insights(output_dir: Path, case_name: str) -> Path:
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
    return insights_path


def _two_course_pipeline(
    *,
    duplicate_ids: bool = False,
    fail_on: str | None = None,
) -> tuple[IngestionPipeline, list[str]]:
    calls: list[str] = []

    def course_parser(
        course_dir: Path,
        out_dir: Path,
        enrichment_agent: object = None,
        trace: object = None,
        *,
        fail_fast: bool = False,
        on_file: Callable[[str, str, int], None] | None = None,
    ) -> CourseParseReport:
        del enrichment_agent, trace
        assert fail_fast is True
        source = next(course_dir.rglob("*.txt"))
        calls.append(source.name)
        if source.name == fail_on:
            if on_file is not None:
                on_file(source.name, "failed", 0)
            raise RuntimeError("secret parse failure")
        chunk_id = "same" if duplicate_ids else f"course-{source.stem}"
        chunks = [course_chunk(chunk_id, source_file=source.name)]
        output_files = _write_course_outputs(out_dir, chunks)
        if on_file is not None:
            on_file(source.name, "parsed", len(chunks))
        return CourseParseReport(
            input_dir=str(course_dir),
            output_files=output_files,
            counts={
                "files_parsed": 1,
                "files_skipped": 0,
                "files_failed": 0,
                "chunks": 1,
                "duplicate_text_hashes": 0,
            },
        )

    return (
        IngestionPipeline(
            config_provider=object,
            case_generation_factory=lambda config: None,
            course_enrichment_factory=lambda config: None,
            course_parser=course_parser,
        ),
        calls,
    )


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
            insights_path = _write_case_insights(output_dir, case_name)
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
    config_provider: Callable[[], object] = object,
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
        output_files = _write_course_outputs(out_dir, chunks)
        if on_file is not None:
            on_file("促成课.txt", "parsed", len(chunks))
        return CourseParseReport(
            input_dir="course",
            output_files=output_files,
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
        config_provider=config_provider,
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


def test_course_config_failure_degrades_to_safe_warning(tmp_path: Path) -> None:
    def unavailable() -> object:
        raise RuntimeError("secret-token=/private/model/config")

    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("course__a__0001")],
        enrich_failures=[],
        config_provider=unavailable,
    )

    prepared = pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    assert prepared.chunk_count == 1
    assert prepared.warnings == ("课程增值服务不可用",)
    assert "secret" not in "".join(prepared.warnings)


def test_case_config_failure_remains_fail_fast(tmp_path: Path) -> None:
    pipeline = pipeline_with_case_result(status="ok")
    pipeline.config_provider = lambda: (_ for _ in ()).throw(
        RuntimeError("secret-token")
    )

    with pytest.raises(IngestionPipelineError, match="配置无法加载") as captured:
        pipeline.prepare(case_job(tmp_path), tmp_path / "attempt")

    assert "secret" not in captured.value.detail
    assert not (tmp_path / "attempt" / "staging").exists()


def test_duplicate_chunk_ids_fail_before_index(tmp_path: Path) -> None:
    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("duplicate"), course_chunk("duplicate")],
        enrich_failures=[],
    )

    with pytest.raises(IngestionPipelineError, match="chunk_id 重复"):
        pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    assert not (tmp_path / "attempt" / "staging" / "chunks.jsonl").exists()


def test_large_raw_citations_pass_when_real_milvus_row_is_compacted(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    citations = [
        EvidenceRef(
            section_name=f"第 {index} 节",
            quote="引用",
            context="上下文" * 20_000,
            source_excerpt="原文" * 2_000,
        )
        for index in range(120)
    ]
    chunk = course_chunk("large-citations", citations=citations)
    pipeline = pipeline_with_course_report(chunks=[chunk], enrich_failures=[])
    caplog.set_level(logging.WARNING, logger="xhbx_rag.milvus_store")

    prepared = pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    clipping_logs = [
        record
        for record in caplog.records
        if "citations 超过 Milvus 字段上限" in record.getMessage()
    ]
    assert len(clipping_logs) == 1
    staged = load_chunks_jsonl(prepared.chunks_path)
    assert len(staged[0].citations) == 120
    row = MilvusChunkRecord.from_chunk(staged[0], [0.0]).to_row()
    assert len(row["citations_json"].encode("utf-8")) <= 65_535
    stored_citations = json.loads(row["citations_json"])
    assert stored_citations
    assert all("context" not in citation for citation in stored_citations)
    assert all(len(citation["source_excerpt"]) <= 600 for citation in stored_citations)


def test_each_chunk_builds_real_milvus_row_once_per_prepare(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []
    original = MilvusChunkRecord.to_row

    def recording_to_row(record: MilvusChunkRecord) -> dict[str, object]:
        calls.append(record.chunk.chunk_id)
        return original(record)

    monkeypatch.setattr(MilvusChunkRecord, "to_row", recording_to_row)
    chunks = [course_chunk("one"), course_chunk("two")]
    pipeline = pipeline_with_course_report(chunks=chunks, enrich_failures=[])

    prepared = pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    assert prepared.chunk_count == 2
    assert calls == ["one", "two"]


def test_two_course_items_run_once_in_item_index_order(tmp_path: Path) -> None:
    pipeline, calls = _two_course_pipeline()
    events: list[tuple[str, dict[str, object]]] = []

    prepared = pipeline.prepare(
        two_course_job(tmp_path),
        tmp_path / "attempt",
        on_event=lambda event, payload: events.append((event, payload)),
    )

    assert calls == ["a.txt", "b.txt"]
    assert prepared.chunk_count == 2
    assert [
        payload["item_index"]
        for event, payload in events
        if event == "item_completed"
    ] == [1, 2]


def test_second_course_failure_removes_old_and_final_staging(tmp_path: Path) -> None:
    pipeline, calls = _two_course_pipeline(fail_on="b.txt")
    attempt_dir = tmp_path / "attempt"
    old_staging = attempt_dir / "staging"
    old_staging.mkdir(parents=True)
    (old_staging / "chunks.jsonl").write_text("old\n", encoding="utf-8")

    with pytest.raises(IngestionPipelineError) as captured:
        pipeline.prepare(two_course_job(tmp_path), attempt_dir)

    assert calls == ["a.txt", "b.txt"]
    assert captured.value.item_index == 2
    assert not old_staging.exists()
    assert not list(attempt_dir.glob(".staging.tmp-*"))


def test_cross_item_duplicate_fails_second_item_before_completed_event(
    tmp_path: Path,
) -> None:
    pipeline, calls = _two_course_pipeline(duplicate_ids=True)
    events: list[tuple[str, dict[str, object]]] = []

    with pytest.raises(IngestionPipelineError, match="chunk_id 重复") as captured:
        pipeline.prepare(
            two_course_job(tmp_path),
            tmp_path / "attempt",
            on_event=lambda event, payload: events.append((event, payload)),
        )

    assert calls == ["a.txt", "b.txt"]
    assert captured.value.item_index == 2
    assert [
        payload["item_index"]
        for event, payload in events
        if event == "item_completed"
    ] == [1]
    assert [
        payload["item_index"]
        for event, payload in events
        if event == "item_failed"
    ] == [2]


@pytest.mark.parametrize("mode", ["skipped", "reversed"])
def test_job_item_indexes_must_be_contiguous_and_ordered(
    tmp_path: Path, mode: str
) -> None:
    job = two_course_job(tmp_path)
    items = job["items"]
    assert isinstance(items, list)
    if mode == "skipped":
        items[1]["item_index"] = 3
    else:
        items.reverse()
    pipeline, calls = _two_course_pipeline()

    with pytest.raises(IngestionPipelineError, match="输入项序号无效"):
        pipeline.prepare(job, tmp_path / "attempt")

    assert calls == []


@pytest.mark.parametrize(
    "mode", ["outside", "missing", "directory", "symlink", "non_path"]
)
def test_case_insights_path_must_be_confined_regular_file(
    tmp_path: Path, mode: str
) -> None:
    outside = _write_case_insights(tmp_path / "outside", "王女士")

    def factory(config: object) -> Callable[..., CaseSalesGenerationResult]:
        del config

        def generate(**kwargs: object) -> CaseSalesGenerationResult:
            output_dir = Path(kwargs["output_dir"])  # type: ignore[arg-type]
            output_dir.mkdir(parents=True, exist_ok=True)
            if mode == "outside":
                candidate = outside
            elif mode == "missing":
                candidate = output_dir / "missing.json"
            elif mode == "directory":
                candidate = output_dir
            elif mode == "symlink":
                candidate = output_dir / "linked.json"
                candidate.symlink_to(outside)
            else:
                candidate = object()
            return CaseSalesGenerationResult(
                case_name="王女士",
                status="ok",
                insights_path=candidate,  # type: ignore[arg-type]
            )

        return generate

    pipeline = IngestionPipeline(
        config_provider=object,
        case_generation_factory=factory,
        course_enrichment_factory=lambda config: None,
    )

    with pytest.raises(
        IngestionPipelineError, match="案例洞察产物无效"
    ) as captured:
        pipeline.prepare(case_job(tmp_path), tmp_path / "attempt")

    assert str(tmp_path) not in captured.value.detail
    assert not (tmp_path / "attempt" / "staging").exists()


def test_case_playbook_path_must_be_confined(tmp_path: Path) -> None:
    outside_playbook = tmp_path / "secret-playbook.md"
    outside_playbook.write_text("# 王女士", encoding="utf-8")

    def factory(config: object) -> Callable[..., CaseSalesGenerationResult]:
        del config

        def generate(**kwargs: object) -> CaseSalesGenerationResult:
            output_dir = Path(kwargs["output_dir"])  # type: ignore[arg-type]
            insights = _write_case_insights(output_dir, "王女士")
            return CaseSalesGenerationResult(
                case_name="王女士",
                status="ok",
                insights_path=insights,
                playbook_path=outside_playbook,
            )

        return generate

    pipeline = IngestionPipeline(
        config_provider=object,
        case_generation_factory=factory,
        course_enrichment_factory=lambda config: None,
    )

    with pytest.raises(IngestionPipelineError, match="playbook 产物无效"):
        pipeline.prepare(case_job(tmp_path), tmp_path / "attempt")

    assert not (tmp_path / "attempt" / "staging").exists()


def test_case_revalidates_output_parent_chain_after_generator(
    tmp_path: Path,
) -> None:
    outside_parent = tmp_path / "outside-generated"
    outside_parent.mkdir()
    external_files: list[tuple[Path, str]] = []

    def factory(config: object) -> Callable[..., CaseSalesGenerationResult]:
        del config

        def generate(**kwargs: object) -> CaseSalesGenerationResult:
            output_dir = Path(kwargs["output_dir"])  # type: ignore[arg-type]
            outside_item = outside_parent / output_dir.name
            outside_insights = _write_case_insights(outside_item, "王女士")
            original_content = outside_insights.read_text(encoding="utf-8")
            external_files.append((outside_insights, original_content))
            generated_parent = output_dir.parent
            shutil.rmtree(generated_parent)
            generated_parent.symlink_to(outside_parent, target_is_directory=True)
            return CaseSalesGenerationResult(
                case_name="王女士",
                status="ok",
                insights_path=output_dir / "case.sales_insights.json",
            )

        return generate

    pipeline = IngestionPipeline(
        config_provider=object,
        case_generation_factory=factory,
        course_enrichment_factory=lambda config: None,
    )

    with pytest.raises(IngestionPipelineError, match="案例洞察产物无效"):
        pipeline.prepare(case_job(tmp_path), tmp_path / "attempt")

    assert external_files
    assert external_files[0][0].read_text(encoding="utf-8") == external_files[0][1]
    assert not (tmp_path / "attempt" / "staging").exists()


@pytest.mark.parametrize(
    "field", ["chunks", "report", "chunks_symlink", "chunks_non_path"]
)
def test_course_output_paths_must_be_confined(
    tmp_path: Path, field: str
) -> None:
    outside_chunks = tmp_path / "outside-chunks.jsonl"
    outside_chunks.write_text(course_chunk("outside").model_dump_json() + "\n")
    outside_report = tmp_path / "outside-report.json"
    outside_report.write_text("{}\n", encoding="utf-8")

    def course_parser(
        course_dir: Path,
        out_dir: Path,
        enrichment_agent: object = None,
        trace: object = None,
        *,
        fail_fast: bool = False,
        on_file: Callable[[str, str, int], None] | None = None,
    ) -> CourseParseReport:
        del course_dir, enrichment_agent, trace, fail_fast, on_file
        output_files = _write_course_outputs(out_dir, [course_chunk("inside")])
        if field == "chunks":
            output_files["chunks"] = str(outside_chunks)
        elif field == "report":
            output_files["report"] = str(outside_report)
        elif field == "chunks_symlink":
            linked = out_dir / "linked-chunks.jsonl"
            linked.symlink_to(outside_chunks)
            output_files["chunks"] = str(linked)
        else:
            output_files["chunks"] = object()  # type: ignore[assignment]
        return CourseParseReport(
            input_dir="course",
            output_files=output_files,
            counts={"files_parsed": 1, "files_failed": 0},
        )

    pipeline = IngestionPipeline(
        config_provider=object,
        case_generation_factory=lambda config: None,
        course_enrichment_factory=lambda config: None,
        course_parser=course_parser,
    )

    with pytest.raises(IngestionPipelineError, match="课程解析产物无效"):
        pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    assert not (tmp_path / "attempt" / "staging").exists()


def test_course_revalidates_output_parent_chain_after_parser(
    tmp_path: Path,
) -> None:
    outside_parent = tmp_path / "outside-parsed"
    outside_parent.mkdir()
    external_files: list[tuple[Path, str]] = []

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
        outside_item = outside_parent / out_dir.name
        output_files = _write_course_outputs(
            outside_item, [course_chunk("outside-course")]
        )
        outside_chunks = Path(output_files["chunks"])
        external_files.append(
            (outside_chunks, outside_chunks.read_text(encoding="utf-8"))
        )
        parsed_parent = out_dir.parent
        shutil.rmtree(parsed_parent)
        parsed_parent.symlink_to(outside_parent, target_is_directory=True)
        if on_file is not None:
            on_file("促成课.txt", "parsed", 1)
        return CourseParseReport(
            input_dir="course",
            output_files={
                "chunks": str(out_dir / "chunks.jsonl"),
                "report": str(out_dir / "parse_report.json"),
            },
            counts={"files_parsed": 1, "files_failed": 0, "chunks": 1},
        )

    pipeline = IngestionPipeline(
        config_provider=object,
        case_generation_factory=lambda config: None,
        course_enrichment_factory=lambda config: None,
        course_parser=course_parser,
    )

    with pytest.raises(IngestionPipelineError, match="课程解析产物无效"):
        pipeline.prepare(course_job(tmp_path), tmp_path / "attempt")

    assert external_files
    assert external_files[0][0].read_text(encoding="utf-8") == external_files[0][1]
    assert not (tmp_path / "attempt" / "staging").exists()


def test_item_output_workspace_failure_is_safely_mapped(tmp_path: Path) -> None:
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    (attempt_dir / "generated").write_text("blocked", encoding="utf-8")
    pipeline = pipeline_with_case_result(status="ok")
    events: list[tuple[str, dict[str, object]]] = []

    with pytest.raises(
        IngestionPipelineError, match="案例工作区初始化失败"
    ) as captured:
        pipeline.prepare(
            case_job(tmp_path),
            attempt_dir,
            on_event=lambda event, payload: events.append((event, payload)),
        )

    assert captured.value.item_index == 1
    assert [event for event, _ in events].count("item_failed") == 1
    assert not (attempt_dir / "staging").exists()


def test_item_output_workspace_rejects_symlinked_parent(tmp_path: Path) -> None:
    attempt_dir = tmp_path / "attempt"
    attempt_dir.mkdir()
    outside = tmp_path / "outside-generated"
    outside.mkdir()
    (attempt_dir / "generated").symlink_to(outside, target_is_directory=True)
    pipeline = pipeline_with_case_result(status="ok")

    with pytest.raises(IngestionPipelineError, match="案例工作区初始化失败"):
        pipeline.prepare(case_job(tmp_path), attempt_dir)

    assert list(outside.iterdir()) == []
    assert not (attempt_dir / "staging").exists()


def test_real_course_parser_bad_supported_file_fails_whole_prepare(
    tmp_path: Path,
) -> None:
    source = tmp_path / "坏课件.pptx"
    source.write_bytes(b"broken")
    pipeline = IngestionPipeline(
        config_provider=object,
        case_generation_factory=lambda config: None,
        course_enrichment_factory=lambda config: None,
    )

    with pytest.raises(
        IngestionPipelineError, match="课程文件解析或切分失败"
    ):
        pipeline.prepare(_job_from_source(source, "course"), tmp_path / "attempt")

    assert not (tmp_path / "attempt" / "staging").exists()


def test_staging_publish_replaces_complete_sibling_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    replacements: list[tuple[Path, Path]] = []
    real_replace = os.replace

    def recording_replace(source: object, destination: object) -> None:
        replacements.append((Path(source), Path(destination)))  # type: ignore[arg-type]
        real_replace(source, destination)  # type: ignore[arg-type]

    monkeypatch.setattr(pipeline_module.os, "replace", recording_replace)
    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("atomic")], enrich_failures=[]
    )
    attempt_dir = tmp_path / "attempt"

    prepared = pipeline.prepare(course_job(tmp_path), attempt_dir)

    assert prepared.chunks_path == attempt_dir / "staging" / "chunks.jsonl"
    assert any(
        source.parent == attempt_dir
        and source.name.startswith(".staging.tmp-")
        and destination == attempt_dir / "staging"
        for source, destination in replacements
    )
    assert not list(attempt_dir.glob(".staging.tmp-*"))


def test_staging_fsync_failure_is_safe_and_leaves_no_artifacts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def failing_fsync(file_descriptor: int) -> None:
        del file_descriptor
        raise OSError("secret fsync path")

    monkeypatch.setattr(pipeline_module.os, "fsync", failing_fsync)
    pipeline = pipeline_with_course_report(
        chunks=[course_chunk("fsync")], enrich_failures=[]
    )
    attempt_dir = tmp_path / "attempt"

    with pytest.raises(
        IngestionPipelineError, match="staging 产物发布失败"
    ) as captured:
        pipeline.prepare(course_job(tmp_path), attempt_dir)

    assert "secret" not in captured.value.detail
    assert not (attempt_dir / "staging").exists()
    assert not list(attempt_dir.glob(".staging.tmp-*"))


def test_default_case_factory_minimal_wiring_smoke(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    created_agents: list[dict[str, object]] = []
    generation_calls: list[dict[str, object]] = []

    class FakeSalesAgent:
        def __init__(self, **kwargs: object) -> None:
            created_agents.append(kwargs)

    def fake_generate(**kwargs: object) -> CaseSalesGenerationResult:
        generation_calls.append(kwargs)
        output_dir = Path(kwargs["output_dir"])  # type: ignore[arg-type]
        case_name = str(kwargs["case_name"])
        insights = _write_case_insights(output_dir, case_name)
        return CaseSalesGenerationResult(
            case_name=case_name,
            status="ok",
            insights_path=insights,
        )

    monkeypatch.setattr(
        pipeline_module, "SalesInsightAgentScopeAgent", FakeSalesAgent
    )
    monkeypatch.setattr(
        pipeline_module, "generate_case_sales_insights", fake_generate
    )
    config = SimpleNamespace(
        base_url="https://model.invalid",
        api_key="secret",
        model_name="model",
        vision_model_name="",
    )
    pipeline = IngestionPipeline(config_provider=lambda: config)

    prepared = pipeline.prepare(case_job(tmp_path), tmp_path / "attempt")

    assert prepared.chunk_count == 1
    assert created_agents == [
        {
            "base_url": "https://model.invalid",
            "api_key": "secret",
            "model": "model",
        }
    ]
    assert len(generation_calls) == 1
    assert generation_calls[0]["section_agent"] is generation_calls[0]["case_agent"]


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
