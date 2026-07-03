import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import xhbx_rag.cli as cli


def test_cli_generate_insights_prints_result(monkeypatch, tmp_path, capsys) -> None:
    calls = {}
    case_dir = tmp_path / "案例A"
    case_dir.mkdir()
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        cli.RetrievalConfig,
        "from_env",
        lambda: SimpleNamespace(
            base_url="https://api.example.com/v1",
            api_key="chat-key",
            model_name="chat-model",
            vision_model_name="vision-model",
        ),
    )
    def fake_agent(**kwargs):
        calls["agent_kwargs"] = kwargs
        return "agent"

    monkeypatch.setattr(cli, "SalesInsightAgentScopeAgent", fake_agent)

    def fake_vision_agent(**kwargs):
        calls["vision_agent_kwargs"] = kwargs
        return "vision-agent"

    monkeypatch.setattr(cli, "VisionImageDescriptionAgent", fake_vision_agent)

    async def fake_generate_case_sales_insights_async(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(
            case_name="案例A",
            status="ok",
            evidence_paths=(Path("out/案例A/第1节.sales_evidence.json"),),
            failure_paths=(),
            insights_path=Path("out/案例A/case.sales_insights.json"),
            playbook_path=Path("out/案例A/case.sales_playbook.md"),
            error=None,
        )

    monkeypatch.setattr(
        cli,
        "generate_case_sales_insights_async",
        fake_generate_case_sales_insights_async,
    )

    exit_code = cli.main(
        [
            "generate-insights",
            "--case-dir",
            str(case_dir),
            "--out",
            str(out_dir),
            "--retry-attempts",
            "4",
            "--max-section-chars",
            "12345",
            "--section-concurrency",
            "3",
        ]
    )

    assert exit_code == 0
    assert calls["case_dir"] == case_dir
    assert calls["output_dir"] == out_dir
    assert calls["section_agent"] == "agent"
    assert calls["case_agent"] == "agent"
    assert calls["vision_agent"] == "vision-agent"
    assert calls["section_concurrency"] == 3
    assert calls["agent_kwargs"]["retry_attempts"] == 4
    assert calls["agent_kwargs"]["max_section_chars"] == 12345
    assert calls["agent_kwargs"]["enable_thinking"] is True
    assert calls["agent_kwargs"]["stream"] is False
    assert calls["agent_kwargs"]["compact_case_input"] is False
    assert calls["vision_agent_kwargs"]["model"] == "vision-model"
    assert calls["vision_agent_kwargs"]["stream"] is False
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "ok"
    assert output["insights_path"] == "out/案例A/case.sales_insights.json"
    assert output["failure_paths"] == []


def test_cli_generate_insights_can_disable_thinking(monkeypatch, tmp_path) -> None:
    calls = {}
    case_dir = tmp_path / "案例A"
    case_dir.mkdir()
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        cli.RetrievalConfig,
        "from_env",
        lambda: SimpleNamespace(
            base_url="https://api.example.com/v1",
            api_key="chat-key",
            model_name="chat-model",
            vision_model_name="",
        ),
    )

    def fake_agent(**kwargs):
        calls["agent_kwargs"] = kwargs
        return "agent"

    monkeypatch.setattr(cli, "SalesInsightAgentScopeAgent", fake_agent)

    async def fake_generate_case_sales_insights_async(**kwargs):
        return SimpleNamespace(
            case_name="案例A",
            status="ok",
            evidence_paths=(),
            failure_paths=(),
            insights_path=Path("out/案例A/case.sales_insights.json"),
            playbook_path=Path("out/案例A/case.sales_playbook.md"),
            error=None,
        )

    monkeypatch.setattr(
        cli,
        "generate_case_sales_insights_async",
        fake_generate_case_sales_insights_async,
    )

    exit_code = cli.main(
        [
            "generate-insights",
            "--case-dir",
            str(case_dir),
            "--out",
            str(out_dir),
            "--no-thinking",
        ]
    )

    assert exit_code == 0
    assert calls["agent_kwargs"]["enable_thinking"] is False


def test_cli_generate_insights_can_enable_streaming(monkeypatch, tmp_path) -> None:
    calls = {}
    case_dir = tmp_path / "案例A"
    case_dir.mkdir()
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        cli.RetrievalConfig,
        "from_env",
        lambda: SimpleNamespace(
            base_url="https://api.example.com/v1",
            api_key="chat-key",
            model_name="chat-model",
            vision_model_name="vision-model",
        ),
    )

    def fake_agent(**kwargs):
        calls["agent_kwargs"] = kwargs
        return "agent"

    monkeypatch.setattr(cli, "SalesInsightAgentScopeAgent", fake_agent)

    def fake_vision_agent(**kwargs):
        calls["vision_agent_kwargs"] = kwargs
        return "vision-agent"

    monkeypatch.setattr(cli, "VisionImageDescriptionAgent", fake_vision_agent)

    async def fake_generate_case_sales_insights_async(**kwargs):
        return SimpleNamespace(
            case_name="案例A",
            status="ok",
            evidence_paths=(),
            failure_paths=(),
            insights_path=Path("out/案例A/case.sales_insights.json"),
            playbook_path=Path("out/案例A/case.sales_playbook.md"),
            error=None,
        )

    monkeypatch.setattr(
        cli,
        "generate_case_sales_insights_async",
        fake_generate_case_sales_insights_async,
    )

    exit_code = cli.main(
        [
            "generate-insights",
            "--case-dir",
            str(case_dir),
            "--out",
            str(out_dir),
            "--stream",
        ]
    )

    assert exit_code == 0
    assert calls["agent_kwargs"]["stream"] is True
    assert calls["vision_agent_kwargs"]["stream"] is True


def test_cli_generate_insights_can_enable_compact_case_input(
    monkeypatch,
    tmp_path,
) -> None:
    calls = {}
    case_dir = tmp_path / "案例A"
    case_dir.mkdir()
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        cli.RetrievalConfig,
        "from_env",
        lambda: SimpleNamespace(
            base_url="https://api.example.com/v1",
            api_key="chat-key",
            model_name="chat-model",
            vision_model_name="",
        ),
    )

    def fake_agent(**kwargs):
        calls["agent_kwargs"] = kwargs
        return "agent"

    monkeypatch.setattr(cli, "SalesInsightAgentScopeAgent", fake_agent)

    async def fake_generate_case_sales_insights_async(**kwargs):
        return SimpleNamespace(
            case_name="案例A",
            status="ok",
            evidence_paths=(),
            failure_paths=(),
            insights_path=Path("out/案例A/case.sales_insights.json"),
            playbook_path=Path("out/案例A/case.sales_playbook.md"),
            error=None,
        )

    monkeypatch.setattr(
        cli,
        "generate_case_sales_insights_async",
        fake_generate_case_sales_insights_async,
    )

    exit_code = cli.main(
        [
            "generate-insights",
            "--case-dir",
            str(case_dir),
            "--out",
            str(out_dir),
            "--compact-case-input",
        ]
    )

    assert exit_code == 0
    assert calls["agent_kwargs"]["compact_case_input"] is True


def test_cli_generate_insights_can_reuse_section_evidence(
    monkeypatch,
    tmp_path,
) -> None:
    calls = {}
    case_dir = tmp_path / "案例A"
    case_dir.mkdir()
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        cli.RetrievalConfig,
        "from_env",
        lambda: SimpleNamespace(
            base_url="https://api.example.com/v1",
            api_key="chat-key",
            model_name="chat-model",
            vision_model_name="",
        ),
    )
    monkeypatch.setattr(cli, "SalesInsightAgentScopeAgent", lambda **kwargs: "agent")

    async def fake_generate_case_sales_insights_async(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(
            case_name="案例A",
            status="ok",
            evidence_paths=(),
            failure_paths=(),
            insights_path=Path("out/案例A/case.sales_insights.json"),
            playbook_path=Path("out/案例A/case.sales_playbook.md"),
            error=None,
        )

    monkeypatch.setattr(
        cli,
        "generate_case_sales_insights_async",
        fake_generate_case_sales_insights_async,
    )

    exit_code = cli.main(
        [
            "generate-insights",
            "--case-dir",
            str(case_dir),
            "--out",
            str(out_dir),
            "--reuse-section-evidence",
        ]
    )

    assert exit_code == 0
    assert calls["reuse_section_evidence"] is True


def test_cli_generate_insights_passes_case_call_mode(monkeypatch, tmp_path) -> None:
    calls = {}
    case_dir = tmp_path / "案例A"
    case_dir.mkdir()
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        cli.RetrievalConfig,
        "from_env",
        lambda: SimpleNamespace(
            base_url="https://api.example.com/v1",
            api_key="chat-key",
            model_name="chat-model",
            vision_model_name="",
        ),
    )
    monkeypatch.setattr(cli, "SalesInsightAgentScopeAgent", lambda **kwargs: "agent")

    async def fake_generate_case_sales_insights_async(**kwargs):
        calls.update(kwargs)
        return SimpleNamespace(
            case_name="案例A",
            status="ok",
            evidence_paths=(),
            failure_paths=(),
            insights_path=Path("out/案例A/case.sales_insights.json"),
            playbook_path=Path("out/案例A/case.sales_playbook.md"),
            error=None,
            case_part_errors=(),
        )

    monkeypatch.setattr(
        cli,
        "generate_case_sales_insights_async",
        fake_generate_case_sales_insights_async,
    )

    exit_code = cli.main(
        [
            "generate-insights",
            "--case-dir",
            str(case_dir),
            "--out",
            str(out_dir),
            "--case-call-mode",
            "single",
        ]
    )

    assert exit_code == 0
    assert calls["case_call_mode"] == "single"


def test_cli_generate_insights_partial_result_reports_part_errors(
    monkeypatch,
    tmp_path,
    capsys,
) -> None:
    case_dir = tmp_path / "案例A"
    case_dir.mkdir()
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        cli.RetrievalConfig,
        "from_env",
        lambda: SimpleNamespace(
            base_url="https://api.example.com/v1",
            api_key="chat-key",
            model_name="chat-model",
            vision_model_name="",
        ),
    )
    monkeypatch.setattr(cli, "SalesInsightAgentScopeAgent", lambda **kwargs: "agent")

    async def fake_generate_case_sales_insights_async(**kwargs):
        return SimpleNamespace(
            case_name="案例A",
            status="partial",
            evidence_paths=(),
            failure_paths=(),
            insights_path=Path("out/案例A/case.sales_insights.json"),
            playbook_path=Path("out/案例A/case.sales_playbook.md"),
            error=None,
            case_part_errors=(("strategies", "RemoteProtocolError('断流')"),),
        )

    monkeypatch.setattr(
        cli,
        "generate_case_sales_insights_async",
        fake_generate_case_sales_insights_async,
    )

    exit_code = cli.main(
        ["generate-insights", "--case-dir", str(case_dir), "--out", str(out_dir)]
    )

    assert exit_code == 0
    output = json.loads(capsys.readouterr().out)
    assert output["status"] == "partial"
    assert output["case_part_errors"] == {"strategies": "RemoteProtocolError('断流')"}


def _minimal_insights_payload() -> dict:
    return {
        "case_name": "案例A",
        "case_summary": "预算封顶客户的加保案例",
        "customer_journey": [],
        "strategies": [],
        "scripts": [
            {
                "script_id": "script_001",
                "stage": "异议处理",
                "scenario": "客户预算封顶",
                "source_quote": "客户说每年不能超过80万",
                "coach_wording": "先确认红线，再看缴清保单释放的预算。",
                "evidence_refs": [
                    {
                        "section_name": "第1节",
                        "filename": "第1节.track-0.txt",
                        "quote": "客户说每年不能超过80万",
                    }
                ],
            }
        ],
        "objection_handling": [],
    }


def _setup_ingest_env(monkeypatch, tmp_path, generate_result_factory):
    calls = {}
    case_dir = tmp_path / "案例A"
    case_dir.mkdir()

    monkeypatch.setattr(
        cli.RetrievalConfig,
        "from_env",
        lambda: SimpleNamespace(
            base_url="https://api.example.com/v1",
            api_key="chat-key",
            model_name="chat-model",
            vision_model_name="",
        ),
    )
    monkeypatch.setattr(cli, "SalesInsightAgentScopeAgent", lambda **kwargs: "agent")

    async def fake_generate(**kwargs):
        calls["generate"] = kwargs
        return generate_result_factory()

    monkeypatch.setattr(cli, "generate_case_sales_insights_async", fake_generate)

    def fake_index_chunks(chunks_path, embedding, store, *, trace=None, mode):
        calls["index"] = {"chunks_path": chunks_path, "mode": mode}
        return 1

    monkeypatch.setattr(cli, "index_chunks", fake_index_chunks)
    monkeypatch.setattr(cli, "_embedding_client", lambda config: "embedding")
    monkeypatch.setattr(cli, "_milvus_store", lambda config: "store")
    return case_dir, calls


def test_cli_ingest_runs_generate_parse_index(monkeypatch, tmp_path, capsys) -> None:
    generated_out = tmp_path / "generated"
    insights_path = generated_out / "案例A" / "case.sales_insights.json"
    insights_path.parent.mkdir(parents=True)
    insights_path.write_text(
        json.dumps(_minimal_insights_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    def make_result():
        return SimpleNamespace(
            case_name="案例A",
            status="ok",
            evidence_paths=(),
            failure_paths=(),
            insights_path=insights_path,
            playbook_path=None,
            error=None,
            case_part_errors=(),
        )

    case_dir, calls = _setup_ingest_env(monkeypatch, tmp_path, make_result)

    exit_code = cli.main(
        [
            "ingest",
            "--case-dir",
            str(case_dir),
            "--generated-out",
            str(generated_out),
            "--parsed-out",
            str(tmp_path / "parsed"),
        ]
    )

    assert exit_code == 0
    assert calls["generate"]["case_dir"] == case_dir
    chunks_path = calls["index"]["chunks_path"]
    assert chunks_path.name == "chunks.jsonl"
    assert chunks_path.exists()
    assert calls["index"]["mode"] == "incremental"
    output = json.loads(capsys.readouterr().out)
    assert output["generate"]["status"] == "ok"
    assert output["parse"]["counts"]["chunks"] == 1
    assert output["index"]["indexed"] == 1


def test_cli_ingest_stops_when_generation_fails(monkeypatch, tmp_path, capsys) -> None:
    def make_result():
        return SimpleNamespace(
            case_name="案例A",
            status="failed",
            evidence_paths=(),
            failure_paths=(),
            insights_path=None,
            playbook_path=None,
            error="案例级分型抽取全部失败",
            case_part_errors=(("strategies", "RemoteProtocolError"),),
        )

    case_dir, calls = _setup_ingest_env(monkeypatch, tmp_path, make_result)

    exit_code = cli.main(["ingest", "--case-dir", str(case_dir)])

    assert exit_code == 1
    assert "index" not in calls
    output = json.loads(capsys.readouterr().out)
    assert output["generate"]["status"] == "failed"
    assert output["parse"] is None
    assert output["index"] is None


def test_cli_ingest_continues_on_partial_generation(
    monkeypatch, tmp_path, capsys
) -> None:
    generated_out = tmp_path / "generated"
    insights_path = generated_out / "案例A" / "case.sales_insights.json"
    insights_path.parent.mkdir(parents=True)
    insights_path.write_text(
        json.dumps(_minimal_insights_payload(), ensure_ascii=False),
        encoding="utf-8",
    )

    def make_result():
        return SimpleNamespace(
            case_name="案例A",
            status="partial",
            evidence_paths=(),
            failure_paths=(),
            insights_path=insights_path,
            playbook_path=None,
            error=None,
            case_part_errors=(("strategies", "RemoteProtocolError"),),
        )

    case_dir, calls = _setup_ingest_env(monkeypatch, tmp_path, make_result)

    exit_code = cli.main(
        [
            "ingest",
            "--case-dir",
            str(case_dir),
            "--generated-out",
            str(generated_out),
            "--parsed-out",
            str(tmp_path / "parsed"),
            "--index-mode",
            "rebuild",
        ]
    )

    assert exit_code == 0
    assert calls["index"]["mode"] == "rebuild"
    output = json.loads(capsys.readouterr().out)
    assert output["generate"]["status"] == "partial"
    assert output["generate"]["case_part_errors"] == {
        "strategies": "RemoteProtocolError"
    }
    assert output["index"]["indexed"] == 1


def test_cli_generate_insights_uses_single_async_entrypoint(
    monkeypatch,
    tmp_path,
) -> None:
    calls = {}
    case_dir = tmp_path / "案例A"
    case_dir.mkdir()
    out_dir = tmp_path / "out"

    monkeypatch.setattr(
        cli.RetrievalConfig,
        "from_env",
        lambda: SimpleNamespace(
            base_url="https://api.example.com/v1",
            api_key="chat-key",
            model_name="chat-model",
            vision_model_name="",
        ),
    )
    monkeypatch.setattr(cli, "SalesInsightAgentScopeAgent", lambda **kwargs: "agent")

    async def fake_generate_case_sales_insights_async(**kwargs):
        calls["loop_id"] = id(asyncio.get_running_loop())
        calls.update(kwargs)
        return SimpleNamespace(
            case_name="案例A",
            status="ok",
            evidence_paths=(),
            failure_paths=(),
            insights_path=Path("out/案例A/case.sales_insights.json"),
            playbook_path=Path("out/案例A/case.sales_playbook.md"),
            error=None,
        )

    monkeypatch.setattr(
        cli,
        "generate_case_sales_insights_async",
        fake_generate_case_sales_insights_async,
    )

    exit_code = cli.main(
        [
            "generate-insights",
            "--case-dir",
            str(case_dir),
            "--out",
            str(out_dir),
        ]
    )

    assert exit_code == 0
    assert isinstance(calls["loop_id"], int)
