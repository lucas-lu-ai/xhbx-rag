import base64
import asyncio
import json
from io import BytesIO

from PIL import Image
from agentscope.message import DataBlock, TextBlock, ToolCallBlock
from agentscope.model import ChatResponse

from xhbx_rag.models import (
    CaseSalesInsightsSource,
    CaseSalesScript,
    CustomerSignal,
    EvidenceRef,
    ObjectionEvidence,
    SalesAction,
    SectionSalesEvidence,
    ScriptQuote,
    StrategyCandidate,
)
from xhbx_rag.sales_generation import (
    generate_case_sales_insights,
    generate_case_sales_insights_async,
)
from xhbx_rag.sales_generation import (
    SalesInsightAgentScopeAgent,
    VisionImageDescriptionAgent,
    _build_dashscope_chat_model,
    _build_structured_chat_model,
    _call_agent_scope_structured_output,
    _format_model_retry_error,
    _render_section_material,
    _RetryDiagnosticDashScopeChatModel,
    _RetryDiagnosticOpenAIChatModel,
)
from agentscope.credential import OpenAICredential
from xhbx_rag.observability import MemoryTraceSink
from xhbx_rag.source_loader import ParsedEmbeddedImage, ParsedSourceFile, SourceSection


class _FakeAgentScopeChatModel:
    def __init__(self, content: str) -> None:
        self.content = content
        self.calls: list[dict] = []

    async def __call__(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return ChatResponse(
            content=[TextBlock(text=self.content)],
            is_last=True,
        )


class _FakeStructuredToolChatModel:
    def __init__(self, payload: dict) -> None:
        self.payload = payload
        self.calls: list[dict] = []

    async def __call__(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        return ChatResponse(
            content=[
                ToolCallBlock(
                    type="tool_call",
                    id="call-1",
                    name="generate_structured_output",
                    input=json.dumps(self.payload, ensure_ascii=False),
                )
            ],
            is_last=True,
        )


class _SequenceStructuredToolChatModel:
    def __init__(self, payloads: list[dict]) -> None:
        self.payloads = payloads
        self.calls: list[dict] = []

    async def __call__(self, messages, **kwargs):
        self.calls.append({"messages": messages, "kwargs": kwargs})
        payload = self.payloads[min(len(self.calls) - 1, len(self.payloads) - 1)]
        return ChatResponse(
            content=[
                ToolCallBlock(
                    type="tool_call",
                    id=f"call-{len(self.calls)}",
                    name="generate_structured_output",
                    input=json.dumps(payload, ensure_ascii=False),
                )
            ],
            is_last=True,
        )


class _RetryableDiagnosticChatModel(_RetryDiagnosticOpenAIChatModel):
    @classmethod
    def _get_retryable_exceptions(cls):
        return (RuntimeError,)

    async def _call_api(self, model_name, messages, **kwargs):
        if not hasattr(self, "attempt_count"):
            self.attempt_count = 0
        self.attempt_count += 1
        if self.attempt_count == 1:
            cause = ValueError("tcp reset by peer")
            raise RuntimeError("Connection error.") from cause
        return ChatResponse(content=[TextBlock(text="ok")], is_last=True)


class _FakeHttpResponse:
    status_code = 502
    headers = {"x-request-id": "req_123", "content-type": "application/json"}
    text = '{"error":"upstream gateway closed"}'


class _FakeHttpStatusError(Exception):
    response = _FakeHttpResponse()


class _FakeSectionAgent:
    def extract(self, section):
        return SectionSalesEvidence(
            case_name=section.case_name,
            section_name=section.section_name,
            sales_actions=[
                SalesAction(
                    action="识别预算上限",
                    evidence="客户说每年不能超过80万",
                    source_refs=[
                        EvidenceRef(
                            section_name=section.section_name,
                            filename="第1节.track-0.txt",
                            quote="客户说每年不能超过80万",
                        )
                    ],
                )
            ],
        )


class _FakeCaseAgent:
    def __init__(self) -> None:
        self.received = None

    def extract(self, case_name, evidences):
        self.received = evidences
        assert evidences[0].sales_actions[0].source_refs[0].locator["line_start"] == 2
        return CaseSalesInsightsSource(
            case_name=case_name,
            case_summary="客户有预算上限，需要用预算释放方式处理。",
            scripts=[
                CaseSalesScript(
                    script_id="script_001",
                    stage="异议处理",
                    scenario="客户预算封顶",
                    customer_trigger="客户说每年不能超过80万",
                    goal="在预算不增加的前提下说明加保空间",
                    source_quote="客户说每年不能超过80万",
                    coach_wording="先确认预算红线，再检查已缴清保单释放出的预算。",
                    evidence_refs=[
                        EvidenceRef(
                            section_name="第1节",
                            filename="第1节.track-0.txt",
                            quote="客户说每年不能超过80万",
                        )
                    ],
                )
            ],
        )


def test_generate_case_sales_insights_writes_located_refs_and_playbook(tmp_path) -> None:
    case_dir = tmp_path / "案例A"
    section_dir = case_dir / "第1节"
    section_dir.mkdir(parents=True)
    (section_dir / "第1节.track-0.txt").write_text(
        "老师开场\n客户说每年不能超过80万\n销售回应：可以看缴费期满的保单\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "out"
    case_agent = _FakeCaseAgent()

    result = generate_case_sales_insights(
        case_dir=case_dir,
        output_dir=out_dir,
        section_agent=_FakeSectionAgent(),
        case_agent=case_agent,
    )

    assert result.status == "ok"
    data = json.loads(result.insights_path.read_text(encoding="utf-8"))
    ref = data["scripts"][0]["evidence_refs"][0]
    assert ref["source_type"] == "txt"
    assert ref["context"] == (
        "老师开场\n客户说每年不能超过80万\n销售回应：可以看缴费期满的保单"
    )
    assert ref["locator"]["line_start"] == 2
    assert ref["locator_confidence"] == "exact"
    evidence_data = json.loads(result.evidence_paths[0].read_text(encoding="utf-8"))
    source_ref = evidence_data["sales_actions"][0]["source_refs"][0]
    assert source_ref["context"] == ref["context"]
    assert result.playbook_path.exists()
    playbook = result.playbook_path.read_text(encoding="utf-8")
    assert "第1节.track-0.txt L2-L2" in playbook
    assert "客户说每年不能超过80万" in playbook


def test_sales_insight_agentscope_agent_uses_structured_tool_call() -> None:
    chat_model = _FakeStructuredToolChatModel(
        {"case_name": "案例A", "section_name": "第1节", "sales_actions": []}
    )
    agent = SalesInsightAgentScopeAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        chat_model=chat_model,
        retry_base_delay=0,
    )
    section = SourceSection(
        case_name="案例A",
        section_name="第1节",
        section_dir="案例A/第1节",
        sources=(
            ParsedSourceFile(
                source_id="txt:a.txt",
                source_type="txt",
                filename="a.txt",
                source_path="案例A/第1节/a.txt",
                text="客户关注预算",
            ),
        ),
    )

    agent.extract_section(section)

    call = chat_model.calls[0]
    assert "response_format" not in call["kwargs"]
    assert call["kwargs"]["tool_choice"].mode == "auto"
    assert call["kwargs"]["tools"][0]["function"]["name"] == "generate_structured_output"
    assert call["messages"][0].role == "system"
    assert call["messages"][1].role == "user"
    assert "客户关注预算" in call["messages"][1].get_text_content()
    assert "MUST" in call["messages"][1].get_text_content()


def test_retry_diagnostic_model_logs_exception_type_and_cause(monkeypatch) -> None:
    logs: list[str] = []

    def capture_warning(message, *args):
        logs.append(message % args)

    from xhbx_rag import sales_generation

    monkeypatch.setattr(sales_generation._agentscope_logger, "warning", capture_warning)
    model = _RetryableDiagnosticChatModel(
        credential=OpenAICredential(api_key="key", base_url="https://api.example.com/v1"),
        model="chat-model",
        max_retries=1,
        retry_delay=0,
    )

    response = asyncio.run(model([]))

    assert response.content[0].text == "ok"
    assert model.attempt_count == 2
    assert "retry diagnostic" in logs[0]
    assert "RuntimeError" in logs[0]
    assert "Connection error." in logs[0]
    assert "ValueError: tcp reset by peer" in logs[0]


def test_format_model_retry_error_includes_http_response_details() -> None:
    diagnostic = _format_model_retry_error(_FakeHttpStatusError("server failed"))

    assert "FakeHttpStatusError" in diagnostic
    assert "server failed" in diagnostic
    assert "status=502" in diagnostic
    assert "x-request-id=req_123" in diagnostic
    assert "body={\"error\":\"upstream gateway closed\"}" in diagnostic


def test_model_builders_use_retry_diagnostic_models() -> None:
    structured = _build_structured_chat_model(
        base_url="https://api.example.com/v1",
        api_key="key",
        model="chat-model",
        timeout=10,
        retry_attempts=2,
        retry_base_delay=0.25,
        enable_thinking=True,
        stream=True,
    )
    vision = _build_dashscope_chat_model(
        base_url="https://dashscope.example.com/compatible-mode/v1",
        api_key="key",
        model="vision-model",
        timeout=10,
        retry_attempts=2,
        retry_base_delay=0.25,
        enable_thinking=False,
        stream=True,
    )

    assert isinstance(structured, _RetryDiagnosticOpenAIChatModel)
    assert isinstance(vision, _RetryDiagnosticDashScopeChatModel)
    assert structured.max_retries == 1
    assert vision.max_retries == 1
    assert structured.stream is True
    assert vision.stream is True


def test_sales_insight_agent_prompt_includes_line_numbers_for_source_refs() -> None:
    chat_model = _FakeStructuredToolChatModel(
        {
            "case_name": "案例A",
            "section_name": "第1节",
            "sales_actions": [
                {
                    "action": "识别理赔价值",
                    "evidence": "客户一周内收到赔款",
                    "source_refs": [
                        {
                            "filename": "a.txt",
                            "quote": "他这90万的赔款在一周以内全部到账",
                            "locator": {"line_start": 2, "line_end": 4},
                        }
                    ],
                }
            ],
        }
    )
    agent = SalesInsightAgentScopeAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        chat_model=chat_model,
        retry_base_delay=0,
    )
    section = SourceSection(
        case_name="案例A",
        section_name="第1节",
        section_dir="案例A/第1节",
        sources=(
            ParsedSourceFile(
                source_id="txt:a.txt",
                source_type="txt",
                filename="a.txt",
                source_path="案例A/第1节/a.txt",
                text="开场白\n他这90万的赔款\n在一周以来\n全部到账\n",
            ),
        ),
    )

    evidence = agent.extract_section(section)

    user_text = chat_model.calls[0]["messages"][1].get_text_content()
    assert "L001: 开场白" in user_text
    assert "L002: 他这90万的赔款" in user_text
    assert "line_start/line_end" in user_text
    ref = evidence.sales_actions[0].source_refs[0]
    assert ref.locator["line_start"] == 2


def test_sales_insight_agent_can_compact_case_input() -> None:
    chat_model = _FakeStructuredToolChatModel(
        {"case_name": "案例A", "case_summary": "摘要"}
    )
    agent = SalesInsightAgentScopeAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        chat_model=chat_model,
        retry_base_delay=0,
        compact_case_input=True,
    )
    long_context = "上下文" * 300
    evidence = SectionSalesEvidence(
        case_name="案例A",
        section_name="第1节",
        customer_signals=[
            CustomerSignal(
                signal="客户担心企业资产传承",
                evidence="客户提到孩子接班和资产安全",
                source_refs=[
                    EvidenceRef(
                        section_name="第1节",
                        source_id="txt:a.txt",
                        source_type="txt",
                        filename="a.txt",
                        source_path="案例A/第1节/a.txt",
                        quote="孩子接班和资产安全",
                        context=long_context,
                        source_excerpt=long_context,
                        locator={"line_start": 7, "line_end": 8},
                        locator_confidence="exact",
                    )
                ],
            )
        ],
        sales_actions=[
            SalesAction(
                action="用财富观念唤醒传承需求",
                stage_hint="需求唤醒",
                evidence="销售引导客户区分企业资产和家庭资产",
            )
        ],
        script_quotes=[
            ScriptQuote(
                quote="先把企业资产和家庭资产分开看",
                speaker="销售",
                stage_hint="需求诊断",
            )
        ],
        objections=[
            ObjectionEvidence(
                objection="客户觉得保险收益不高",
                response_evidence="回应保险更看重确定性和传承安排",
            )
        ],
        strategy_candidates=[
            StrategyCandidate(
                name="财富观念唤醒",
                reason="先建立资产隔离与传承认知",
                confidence="high",
                inferred=False,
            )
        ],
    )

    agent.extract_case("案例A", [evidence])

    user_text = chat_model.calls[0]["messages"][1].get_text_content()
    assert "精简后的章节销售证据" in user_text
    assert "客户担心企业资产传承" in user_text
    assert "用财富观念唤醒传承需求" in user_text
    assert "先把企业资产和家庭资产分开看" in user_text
    assert "财富观念唤醒" in user_text
    assert '"line_start": 7' in user_text
    assert '"quote": "孩子接班和资产安全"' in user_text
    assert "context" not in user_text
    assert "source_excerpt" not in user_text
    assert long_context not in user_text


def test_generate_case_sales_insights_traces_final_case_model_input(tmp_path) -> None:
    case_dir = tmp_path / "案例A"
    section_dir = case_dir / "第1节"
    section_dir.mkdir(parents=True)
    (section_dir / "a.txt").write_text("客户关注预算", encoding="utf-8")
    chat_model = _FakeStructuredToolChatModel(
        {"case_name": "案例A", "case_summary": "预算相关摘要"}
    )
    case_agent = SalesInsightAgentScopeAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        chat_model=chat_model,
        retry_base_delay=0,
        compact_case_input=True,
    )
    trace = MemoryTraceSink()

    result = generate_case_sales_insights(
        case_dir=case_dir,
        output_dir=tmp_path / "out",
        section_agent=_FakeSectionAgent(),
        case_agent=case_agent,
        trace=trace,
    )

    assert result.status == "ok"
    input_events = [
        event for event in trace.events if event.step == "generate.case_model_input"
    ]
    assert len(input_events) == 1
    payload = input_events[0].payload
    assert payload["case_name"] == "案例A"
    assert payload["evidence_count"] == 1
    assert payload["compact"] is True
    assert payload["model"] == "chat-model"
    assert payload["stream"] is False
    assert "案例级销售洞察专家" in payload["system_prompt"]
    assert "精简后的章节销售证据" in payload["user_content"]
    assert "识别预算上限" in payload["user_content"]
    assert payload["user_content_chars"] == len(payload["user_content"])
    model_user_text = chat_model.calls[0]["messages"][1].get_text_content()
    assert payload["user_content"] in model_user_text
    assert payload["structured_reminder"] in model_user_text


def test_sales_insight_agentscope_agent_unwraps_nested_structured_output() -> None:
    chat_model = _FakeStructuredToolChatModel(
        {
            "output": {
                "case_name": "案例A",
                "section_name": "第1节",
                "sales_actions": [
                    {
                        "action": "识别预算异议",
                        "evidence": "客户说每年不能超过80万",
                    }
                ],
            }
        }
    )
    agent = SalesInsightAgentScopeAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        chat_model=chat_model,
        retry_base_delay=0,
    )
    section = SourceSection(
        case_name="案例A",
        section_name="第1节",
        section_dir="案例A/第1节",
        sources=(
            ParsedSourceFile(
                source_id="txt:a.txt",
                source_type="txt",
                filename="a.txt",
                source_path="案例A/第1节/a.txt",
                text="客户说每年不能超过80万",
            ),
        ),
    )

    evidence = agent.extract_section(section)

    assert evidence.sales_actions[0].action == "识别预算异议"


def test_structured_output_retry_adds_validation_error_context() -> None:
    chat_model = _SequenceStructuredToolChatModel(
        [
            {"case_summary": "缺少必填 case_name"},
            {"case_name": "案例A", "case_summary": "摘要"},
        ]
    )

    data = _call_agent_scope_structured_output(
        chat_model,
        [
            TextBlock(text="system"),
            TextBlock(text="请生成案例洞察"),
        ],
        structured_model=CaseSalesInsightsSource,
    )

    assert data["case_name"] == "案例A"
    assert len(chat_model.calls) == 2
    second_user_text = chat_model.calls[1]["messages"][-1].get_text_content()
    assert "上一次结构化输出校验失败" in second_user_text
    assert "case_name" in second_user_text
    assert "缺少必填 case_name" in second_user_text


def test_vision_image_description_agent_sends_image_data_block() -> None:
    chat_model = _FakeAgentScopeChatModel("图片显示客户的保单整理表。")
    agent = VisionImageDescriptionAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="qwen3.7-plus",
        chat_model=chat_model,
        retry_base_delay=0,
    )
    image = ParsedEmbeddedImage(
        image_id="docx:讲义.docx:image-1",
        filename="image1.png",
        source_path="案例A/第1节/讲义.docx::word/media/image1.png",
        media_type="image/png",
        data=b"png-bytes",
        locator={"container": "word/media/image1.png"},
    )
    source = ParsedSourceFile(
        source_id="docx:讲义.docx",
        source_type="docx",
        filename="讲义.docx",
        source_path="案例A/第1节/讲义.docx",
        text="讲义文字",
        images=(image,),
    )

    description = agent.describe(
        image,
        case_name="案例A",
        section_name="第1节",
        source=source,
    )

    assert description == "图片显示客户的保单整理表。"
    blocks = chat_model.calls[0]["messages"][1].get_content_blocks()
    data_blocks = [block for block in blocks if isinstance(block, DataBlock)]
    assert data_blocks
    assert data_blocks[0].source.media_type == "image/png"
    assert data_blocks[0].source.data == base64.b64encode(b"png-bytes").decode(
        "ascii"
    )


def test_generate_case_sales_insights_appends_image_descriptions(tmp_path) -> None:
    from docx import Document

    image_buffer = BytesIO()
    Image.new("RGB", (8, 8), "red").save(image_buffer, format="PNG")
    image_path = tmp_path / "chart.png"
    image_path.write_bytes(image_buffer.getvalue())
    case_dir = tmp_path / "案例A"
    section_dir = case_dir / "第1节"
    section_dir.mkdir(parents=True)
    docx_path = section_dir / "讲义.docx"
    document = Document()
    document.add_paragraph("这是一页保单整理讲义")
    document.add_picture(str(image_path))
    document.save(str(docx_path))

    class _VisionAgent:
        def describe(self, image, *, case_name, section_name, source):
            return "图片显示一张保单整理表，包含年缴保费和保障缺口。"

    class _SectionAgent:
        def __init__(self) -> None:
            self.source_text = ""

        def extract(self, section):
            self.source_text = section.sources[0].text
            return SectionSalesEvidence(
                case_name=section.case_name,
                section_name=section.section_name,
                sales_actions=[
                    SalesAction(
                        action="读取图片表格",
                        evidence="图片显示一张保单整理表",
                        source_refs=[
                            EvidenceRef(
                                filename="讲义.docx",
                                quote="图片显示一张保单整理表",
                            )
                        ],
                    )
                ],
            )

    class _CaseAgent:
        def extract(self, case_name, evidences):
            return CaseSalesInsightsSource(
                case_name=case_name,
                case_summary="摘要",
            )

    section_agent = _SectionAgent()

    result = generate_case_sales_insights(
        case_dir=case_dir,
        output_dir=tmp_path / "out",
        section_agent=section_agent,
        case_agent=_CaseAgent(),
        vision_agent=_VisionAgent(),
    )

    assert result.status == "ok"
    assert "图片补充信息" in section_agent.source_text
    assert "年缴保费和保障缺口" in section_agent.source_text


def test_generate_case_sales_insights_extracts_each_source_separately(tmp_path) -> None:
    case_dir = tmp_path / "案例A"
    section_dir = case_dir / "第1节"
    section_dir.mkdir(parents=True)
    (section_dir / "a.txt").write_text("客户关注预算", encoding="utf-8")
    (section_dir / "b.txt").write_text("销售建议预算释放", encoding="utf-8")

    class _SingleSourceAgent:
        def __init__(self) -> None:
            self.calls: list[list[str]] = []

        def extract(self, section):
            filenames = [source.filename for source in section.sources]
            self.calls.append(filenames)
            assert len(filenames) == 1
            return SectionSalesEvidence(
                case_name=section.case_name,
                section_name=section.section_name,
                sales_actions=[
                    SalesAction(
                        action=f"读取{filenames[0]}",
                        evidence=section.sources[0].text,
                        source_refs=[
                            EvidenceRef(
                                filename=filenames[0],
                                quote=section.sources[0].text,
                            )
                        ],
                    )
                ],
            )

    class _CaseAgent:
        def extract(self, case_name, evidences):
            assert len(evidences) == 1
            assert [item.action for item in evidences[0].sales_actions] == [
                "读取a.txt",
                "读取b.txt",
            ]
            return CaseSalesInsightsSource(
                case_name=case_name,
                case_summary="摘要",
            )

    section_agent = _SingleSourceAgent()

    result = generate_case_sales_insights(
        case_dir=case_dir,
        output_dir=tmp_path / "out",
        section_agent=section_agent,
        case_agent=_CaseAgent(),
    )

    assert result.status == "ok"
    assert section_agent.calls == [["a.txt"], ["b.txt"]]


def test_generate_case_sales_insights_async_reuses_one_loop_and_runs_sections_concurrently(
    tmp_path,
) -> None:
    case_dir = tmp_path / "案例A"
    for name in ["第1节", "第2节"]:
        section_dir = case_dir / name
        section_dir.mkdir(parents=True)
        (section_dir / f"{name}.txt").write_text(
            f"{name} 客户关注预算",
            encoding="utf-8",
        )

    class _AsyncSectionAgent:
        def __init__(self) -> None:
            self.loop_ids: list[int] = []
            self.active = 0
            self.max_active = 0

        async def extract_async(self, section):
            self.loop_ids.append(id(asyncio.get_running_loop()))
            self.active += 1
            self.max_active = max(self.max_active, self.active)
            await asyncio.sleep(0.01)
            self.active -= 1
            return SectionSalesEvidence(
                case_name=section.case_name,
                section_name=section.section_name,
                sales_actions=[
                    SalesAction(
                        action=f"读取{section.section_name}",
                        evidence=section.primary_text,
                        source_refs=[
                            EvidenceRef(
                                filename=section.sources[0].filename,
                                quote=section.primary_text.strip(),
                            )
                        ],
                    )
                ],
            )

    class _AsyncCaseAgent:
        def __init__(self) -> None:
            self.loop_ids: list[int] = []
            self.section_names: list[str] = []

        async def extract_async(self, case_name, evidences):
            self.loop_ids.append(id(asyncio.get_running_loop()))
            self.section_names = [evidence.section_name for evidence in evidences]
            return CaseSalesInsightsSource(case_name=case_name, case_summary="摘要")

    section_agent = _AsyncSectionAgent()
    case_agent = _AsyncCaseAgent()

    result = asyncio.run(
        generate_case_sales_insights_async(
            case_dir=case_dir,
            output_dir=tmp_path / "out",
            section_agent=section_agent,
            case_agent=case_agent,
            section_concurrency=2,
        )
    )

    assert result.status == "ok"
    assert section_agent.max_active == 2
    assert len(set(section_agent.loop_ids + case_agent.loop_ids)) == 1
    assert case_agent.section_names == ["第1节", "第2节"]


def test_generate_case_sales_insights_async_writes_failed_section_and_keeps_output(
    tmp_path,
) -> None:
    case_dir = tmp_path / "案例A"
    for name in ["第1节", "第2节"]:
        section_dir = case_dir / name
        section_dir.mkdir(parents=True)
        (section_dir / f"{name}.txt").write_text(
            f"{name} 客户关注预算",
            encoding="utf-8",
        )

    class _PartiallyFailingSectionAgent:
        async def extract_async(self, section):
            if section.section_name == "第2节":
                raise RuntimeError("模型返回内容无法修复")
            return SectionSalesEvidence(
                case_name=section.case_name,
                section_name=section.section_name,
                sales_actions=[
                    SalesAction(
                        action="识别预算",
                        evidence=section.primary_text,
                    )
                ],
            )

    class _CaseAgent:
        async def extract_async(self, case_name, evidences):
            assert [evidence.section_name for evidence in evidences] == ["第1节"]
            return CaseSalesInsightsSource(case_name=case_name, case_summary="摘要")

    result = asyncio.run(
        generate_case_sales_insights_async(
            case_dir=case_dir,
            output_dir=tmp_path / "out",
            section_agent=_PartiallyFailingSectionAgent(),
            case_agent=_CaseAgent(),
            section_concurrency=2,
        )
    )

    assert result.status == "ok"
    assert len(result.evidence_paths) == 1
    assert len(result.failure_paths) == 1
    failure = json.loads(result.failure_paths[0].read_text(encoding="utf-8"))
    assert failure["status"] == "failed"
    assert failure["section_name"] == "第2节"
    assert failure["error_type"] == "RuntimeError"
    assert "模型返回内容无法修复" in failure["error"]


def test_render_section_material_limits_prompt_size() -> None:
    section = SourceSection(
        case_name="案例A",
        section_name="第1节",
        section_dir="案例A/第1节",
        sources=(
            ParsedSourceFile(
                source_id="txt:a.txt",
                source_type="txt",
                filename="a.txt",
                source_path="案例A/第1节/a.txt",
                text="A" * 200,
            ),
        ),
    )

    material = _render_section_material(section, max_chars=120)

    assert len(material) <= 220
    assert "内容已截断" in material
