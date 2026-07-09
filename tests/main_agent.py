import asyncio
import os, json, uuid, httpx

from agentscope.agent import Agent
from agentscope.credential import DashScopeCredential
from agentscope.event import EventType
from agentscope.message import UserMsg
from agentscope.model import DashScopeChatModel
from agentscope.tool import Toolkit, Bash, Read, Write, Edit

_SYSTEM_PROMPT = """你是保险销售知识问答系统的 Query Rewrite 节点。

你的任务是把用户原始问题改写成一个适合发送给下游 RAG 知识问答智能体的检索问题。

要求：
1. 只改写问题，不回答问题。
2. 改写后的问题必须能脱离上下文独立理解。
3. 保留用户明确表达的业务约束，例如客户类型、销售阶段、产品/课程、异议点、场景、案例名称。
4. 不要添加用户没有表达的事实、案例、客户背景、产品名称或金额。
5. 如果用户问题过短或口语化，补全为保险销售知识库能检索的表达。
6. 如果用户明确是在问话术、异议处理、销售策略、客户旅程、培训课程内容，要在改写问题中自然体现这一意图。
7. 不要强行指定知识类型；除非用户明确说“培训课程/标准流程/制式话术/绩优案例/实战经验”等。
8. 如果问题明显不属于保险销售知识范围，返回原问题并标记为 out_of_scope。

输出 JSON，不要输出多余解释：

{
  "needs_rag": true,
  "rewritten_query": "...",
  "reason": "一句话说明改写依据"
}
"""

RAG_A2A_URL = os.getenv(
    "RAG_A2A_URL",
    "http://127.0.0.1:8848/a2a/xhbx-rag-answer",
)

def _assistant_text(msg) -> str:
    return "".join(
        block.text
        for block in msg.content
        if getattr(block, "type", None) == "text"
    ).strip()

async def ask_rag_agent(query: str, session_id: str = "local-test-session") -> tuple[str, dict]:
    rpc_id = str(uuid.uuid4())

    payload = {
        "jsonrpc": "2.0",
        "method": "tasks/send",
        "id": rpc_id,
        "params": {
            "id": str(uuid.uuid4()),
            "sessionId": session_id,
            "message": {
                "role": "user",
                "parts": [
                    {"type": "text", "text": query},
                ],
            },
            "metadata": {
                "user_id": "local-test-user",
                "tenant_no": "local",
                "parent_session_code": session_id,
            },
        },
    }

    async with httpx.AsyncClient(timeout=600) as client:
        response = await client.post(RAG_A2A_URL, json=payload)
        response.raise_for_status()
        data = response.json()

    if "error" in data:
        raise RuntimeError(f"A2A 调用失败: {data['error']}")

    task = data["result"]
    parts = task["status"]["message"]["parts"]
    answer = "\n".join(
        part["text"]
        for part in parts
        if part.get("type") == "text"
    )

    return answer, task.get("metadata", {})

async def main() -> None:
    agent = Agent(
        name="问答主控智能体",
        system_prompt=_SYSTEM_PROMPT,
        model=DashScopeChatModel(
            credential=DashScopeCredential(
                api_key="sk-305aed53d23a42cf8114939904cfd388",
            ),
            model="qwen3.7-max",
        ),
        toolkit=Toolkit(tools=[]),
    )

    original_query = "用户说每年不超过80万怎么办？"
    user_msg = UserMsg(name="user", content=original_query)

    rewrite_msg = await agent.reply(user_msg)
    rewrite_payload = json.loads(_assistant_text(rewrite_msg))

    if not rewrite_payload.get("needs_rag", True):
        print(rewrite_payload)
        return

    rewritten_query = rewrite_payload["rewritten_query"]
    print("改写后 query:", rewritten_query)

    answer, metadata = await ask_rag_agent(rewritten_query)

    print("RAG 回答:")
    print(answer)
    print("引用 metadata:")
    print(json.dumps(metadata, ensure_ascii=False, indent=2))


asyncio.run(main())

"""
# 检查AgentCard
curl https://bba1-152-42-161-78.ngrok-free.app/a2a/xhbx-rag-answer/.well-known/agent.json
# 测试 A2A 问答
curl -s http://127.0.0.1:8000/a2a/xhbx-rag-answer \
  -H 'Content-Type: application/json' \
  -d '{
    "jsonrpc": "2.0",
    "method": "tasks/send",
    "id": "rpc-001",
    "params": {
      "id": "task-001",
      "sessionId": "session-001",
      "message": {
        "role": "user",
        "parts": [
          {
            "type": "text",
            "text": "客户说每年不超过80万怎么办？"
          }
        ]
      },
      "metadata": {
        "user_id": "test-user",
        "tenant_no": "test",
        "parent_session_code": "main-agent-session-001"
      }
    }
  }' | python -m json.tool
"""