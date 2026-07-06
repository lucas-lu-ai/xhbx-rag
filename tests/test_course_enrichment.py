import json

from agentscope.message import ToolCallBlock
from agentscope.model import ChatResponse

from xhbx_rag.course_enrichment import (
    CourseEnrichment,
    CourseEnrichmentAgentScopeAgent,
    _CourseEnrichmentDraft,
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


def test_course_enrichment_agent_parses_structured_payload() -> None:
    chat_model = _FakeStructuredToolChatModel(
        {
            "summary": "本课讲解促成动作与异议处理方法",
            "audience": "新人",
            "sales_stages": ["促成", "异议处理"],
        }
    )
    agent = CourseEnrichmentAgentScopeAgent(
        base_url="https://api.example.com/v1",
        api_key="secret",
        model="chat-model",
        chat_model=chat_model,
    )

    enrichment = agent.enrich(
        course_name="06促成及异议处理",
        course_series="新人专属会课程集锦1028",
        sample_text="促成：帮助及鼓励客户做出购买决定",
    )

    assert enrichment == CourseEnrichment(
        summary="本课讲解促成动作与异议处理方法",
        audience="新人",
        sales_stages=("促成", "异议处理"),
    )
    prompt_text = "\n".join(
        str(getattr(message, "content", "")) for message in chat_model.calls[0]["messages"]
    )
    assert "06促成及异议处理" in prompt_text
    assert "新人专属会课程集锦1028" in prompt_text
    assert "促成：帮助及鼓励客户做出购买决定" in prompt_text


def test_course_enrichment_draft_coerces_loose_output() -> None:
    draft = _CourseEnrichmentDraft.model_validate(
        {
            "summary": "摘要",
            "audience": "适合新人营销员学习",
            "sales_stages": "促成",
            "unexpected": "ignored",
        }
    )

    assert draft.audience == "新人"
    assert draft.sales_stages == ["促成"]


def test_course_enrichment_draft_unknown_audience_falls_back_to_empty() -> None:
    draft = _CourseEnrichmentDraft.model_validate({"audience": "外星人"})

    assert draft.audience == ""
