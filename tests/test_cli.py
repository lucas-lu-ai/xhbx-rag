import json
from pathlib import Path
from types import SimpleNamespace

import xhbx_rag.cli as cli
from xhbx_rag.cli import main


def test_cli_parse_writes_outputs(tmp_path) -> None:
    insights = tmp_path / "case.sales_insights.json"
    playbook = tmp_path / "case.sales_playbook.md"
    out_dir = tmp_path / "parsed"
    insights.write_text(
        json.dumps(
            {
                "case_name": "案例A",
                "case_summary": "摘要",
                "scripts": [
                    {
                        "script_id": "script_001",
                        "stage": "售前",
                        "scenario": "客户抗拒保险",
                        "coach_wording": "先聊家庭责任。",
                    }
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    playbook.write_text("# 案例A - 销售洞察手册\n", encoding="utf-8")

    exit_code = main(
        [
            "parse",
            "--insights",
            str(insights),
            "--playbook",
            str(playbook),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    case_dirs = list(out_dir.iterdir())
    assert len(case_dirs) == 1
    assert (case_dirs[0] / "case.structured.json").exists()
    assert (case_dirs[0] / "chunks.jsonl").exists()
    assert (case_dirs[0] / "parse_report.json").exists()


def test_cli_parse_returns_nonzero_for_missing_case_name(tmp_path) -> None:
    insights = tmp_path / "case.sales_insights.json"
    out_dir = tmp_path / "parsed"
    insights.write_text(
        json.dumps({"case_summary": "摘要"}, ensure_ascii=False),
        encoding="utf-8",
    )

    exit_code = main(["parse", "--insights", str(insights), "--out", str(out_dir)])

    assert exit_code == 1
    report = json.loads((out_dir / "parse_report.json").read_text(encoding="utf-8"))
    assert report["errors"] == ["case.sales_insights.json 缺少必需字段 case_name"]


def test_cli_normalize_knowledge_prints_summary_and_returns_success(
    monkeypatch, capsys
) -> None:
    calls: list[tuple[Path, Path]] = []

    def fake_normalize(input_dir: Path, out_dir: Path):
        calls.append((input_dir, out_dir))
        return SimpleNamespace(
            success=True,
            report_path=out_dir / "classification_report.json",
            input_files=1078,
            chunks=16299,
        )

    monkeypatch.setattr(cli, "normalize_knowledge", fake_normalize)

    exit_code = main(
        [
            "normalize-knowledge",
            "--input-dir",
            "parsed",
            "--out",
            "parsed_normalized",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert calls == [(Path("parsed"), Path("parsed_normalized"))]
    assert payload["input_files"] == 1078
    assert payload["chunks"] == 16299


def test_cli_normalize_knowledge_returns_nonzero_for_failed_report(
    monkeypatch, capsys
) -> None:
    monkeypatch.setattr(
        cli,
        "normalize_knowledge",
        lambda input_dir, out_dir: SimpleNamespace(
            success=False,
            report_path=Path("failed.classification_report.json"),
            input_files=2,
            chunks=1,
        ),
    )

    exit_code = main(
        [
            "normalize-knowledge",
            "--input-dir",
            "parsed",
            "--out",
            "parsed_normalized",
        ]
    )

    assert exit_code == 1
    assert json.loads(capsys.readouterr().out)["success"] is False


def test_cli_index_dir_passes_batch_size_and_explicit_collection(
    monkeypatch, capsys
) -> None:
    calls: dict[str, object] = {}
    config = SimpleNamespace()
    monkeypatch.setattr(
        cli.RetrievalConfig,
        "from_env",
        lambda require_chat=False: config,
    )
    monkeypatch.setattr(cli, "_embedding_client", lambda selected: "embedding")
    monkeypatch.setattr(
        cli,
        "create_milvus_store",
        lambda selected, collection_name=None: (selected, collection_name),
    )

    def fake_index_directory(
        chunks_dir,
        embedding_client,
        store_factory,
        collection_name,
        *,
        batch_size,
        mode,
    ):
        calls.update(
            {
                "chunks_dir": chunks_dir,
                "embedding_client": embedding_client,
                "store": store_factory(collection_name),
                "collection_name": collection_name,
                "batch_size": batch_size,
                "mode": mode,
            }
        )
        return SimpleNamespace(
            collection=collection_name,
            files=1076,
            indexed=16299,
            vector_dim=1024,
            primary_domain_counts={"产品知识": 100},
        )

    monkeypatch.setattr(cli, "index_directory", fake_index_directory)

    exit_code = main(
        [
            "index-dir",
            "--chunks-dir",
            "parsed_normalized",
            "--collection-name",
            "xhbx_knowledge_chunks",
            "--mode",
            "rebuild",
            "--batch-size",
            "64",
        ]
    )

    payload = json.loads(capsys.readouterr().out)
    assert exit_code == 0
    assert calls["chunks_dir"] == Path("parsed_normalized")
    assert calls["embedding_client"] == "embedding"
    assert calls["store"] == (config, "xhbx_knowledge_chunks")
    assert calls["batch_size"] == 64
    assert calls["mode"] == "rebuild"
    assert payload["indexed"] == 16299
