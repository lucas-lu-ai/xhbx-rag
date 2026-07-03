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
