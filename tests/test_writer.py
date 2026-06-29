import json

from xhbx_rag.models import ParseReport, RagChunk, StructuredCaseKnowledge
from xhbx_rag.writer import write_outputs


def test_write_outputs_creates_structured_json_chunks_and_report(tmp_path) -> None:
    knowledge = StructuredCaseKnowledge(
        case_id="case_a",
        case_name="案例A",
        case_summary="摘要",
        source_files=["case.sales_insights.json"],
        customer_journey=[],
        strategies=[],
        scripts=[],
        objection_handling=[],
    )
    chunk = RagChunk(
        chunk_id="case_a__script__script_001",
        chunk_type="script",
        text="话术文本",
        metadata={"case_name": "案例A"},
        citations=[],
        source_file="case.sales_insights.json",
    )
    report = ParseReport(
        input_files={"insights": "case.sales_insights.json", "playbook": None},
        output_files={},
        case_name="案例A",
        counts={"chunks": 1},
        warnings=[],
        errors=[],
    )

    outputs = write_outputs(tmp_path, knowledge, [chunk], report)

    assert outputs.structured_path.exists()
    assert outputs.chunks_path.exists()
    assert outputs.report_path.exists()
    assert (
        json.loads(outputs.structured_path.read_text(encoding="utf-8"))["case_name"]
        == "案例A"
    )
    assert (
        json.loads(outputs.chunks_path.read_text(encoding="utf-8").splitlines()[0])[
            "chunk_id"
        ]
        == chunk.chunk_id
    )
