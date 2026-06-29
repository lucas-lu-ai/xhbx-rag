import json

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
