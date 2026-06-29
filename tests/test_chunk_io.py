import json

import pytest

from xhbx_rag.chunk_io import ChunkLoadError, chunk_text_hash, load_chunks_jsonl


def test_load_chunks_jsonl_reads_valid_chunks(tmp_path) -> None:
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text(
        json.dumps(
            {
                "chunk_id": "chunk-1",
                "chunk_type": "script",
                "text": "话术文本",
                "metadata": {"case_name": "案例A"},
                "citations": [],
                "source_file": "case.sales_insights.json",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    chunks = load_chunks_jsonl(chunks_path)

    assert len(chunks) == 1
    assert chunks[0].chunk_id == "chunk-1"
    assert chunks[0].metadata["case_name"] == "案例A"


def test_load_chunks_jsonl_reports_line_number_for_invalid_json(tmp_path) -> None:
    chunks_path = tmp_path / "chunks.jsonl"
    chunks_path.write_text("{bad-json}\n", encoding="utf-8")

    with pytest.raises(ChunkLoadError, match="第 1 行"):
        load_chunks_jsonl(chunks_path)


def test_chunk_text_hash_is_stable() -> None:
    assert chunk_text_hash("  话术文本\n") == chunk_text_hash("话术文本")
