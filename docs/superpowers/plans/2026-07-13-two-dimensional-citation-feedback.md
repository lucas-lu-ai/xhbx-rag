# 引用反馈双维度 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 将每条引用的“应该用/不该用”改为“召回准确性 + 回答参考准确性”两级反馈，并把两个维度结构化写入聊天与批量 bad case。

**Architecture:** `EvidenceDetail` 管理逐级单选和负向原因表单，向 `BadCasePanel` 提交一个终态 decision；`BadCasePanel` 统一构建新 `EvidenceFeedback` 和 bad-case 分类。后端在 `bad_cases.py` 集中校验双维度组合并补中文标签，单问和批量路由复用同一校验函数，同时保留旧 `judgement` 兼容。

**Tech Stack:** React 19、TypeScript、Vitest、Testing Library、FastAPI、Pydantic、pytest

---

### Task 1: 后端双维度契约与中文标签

**Files:**
- Modify: `tests/test_web_bad_cases.py`
- Modify: `tests/test_web_app.py`
- Modify: `tests/test_web_batch_routes.py`
- Modify: `src/xhbx_rag/web/bad_cases.py`
- Modify: `src/xhbx_rag/web/app.py`
- Modify: `src/xhbx_rag/web/batch_routes.py`

- [ ] **Step 1: 写入双维度中文标签失败测试**

在 `tests/test_web_bad_cases.py` 增加：

```python
def test_save_bad_case_adds_two_dimension_feedback_labels(tmp_path: Path) -> None:
    bad_cases.save_bad_case(
        {
            **_bad_case_payload(),
            "evidence_feedback": [
                {
                    "chunk_id": "case-a-1",
                    "retrieval_judgement": "accurate",
                    "answer_usage_judgement": "incorrect",
                    "reason": "回答遗漏了关键限制。",
                }
            ],
        },
        project_root=tmp_path,
    )

    path = tmp_path / ".local" / "bad_cases" / "bad_cases.jsonl"
    feedback = json.loads(path.read_text(encoding="utf-8"))["evidence_feedback"][0]
    assert feedback["retrieval_judgement_label"] == "召回准确"
    assert feedback["answer_usage_judgement_label"] == "参考不正确"
```

- [ ] **Step 2: 运行标签测试并确认失败**

Run: `uv run pytest tests/test_web_bad_cases.py::test_save_bad_case_adds_two_dimension_feedback_labels -q`

Expected: FAIL，缺少两个 `*_label` 字段。

- [ ] **Step 3: 实现标签映射**

在 `bad_cases.py` 增加：

```python
RETRIEVAL_JUDGEMENT_LABELS = {
    "accurate": "召回准确",
    "inaccurate": "召回不准确",
}
ANSWER_USAGE_JUDGEMENT_LABELS = {
    "correct": "参考正确",
    "incorrect": "参考不正确",
    "not_applicable": "不适用",
}
```

并在 `_with_evidence_feedback_label` 中保留旧 `judgement_label` 逻辑后追加：

```python
retrieval_judgement = item.get("retrieval_judgement")
if isinstance(retrieval_judgement, str) and retrieval_judgement:
    item["retrieval_judgement_label"] = _label_for(
        retrieval_judgement, RETRIEVAL_JUDGEMENT_LABELS
    )
answer_usage_judgement = item.get("answer_usage_judgement")
if isinstance(answer_usage_judgement, str) and answer_usage_judgement:
    item["answer_usage_judgement_label"] = _label_for(
        answer_usage_judgement, ANSWER_USAGE_JUDGEMENT_LABELS
    )
```

- [ ] **Step 4: 运行标签测试并确认通过**

Run: `uv run pytest tests/test_web_bad_cases.py::test_save_bad_case_adds_two_dimension_feedback_labels -q`

Expected: PASS。

- [ ] **Step 5: 写入组合校验失败测试**

在 `tests/test_web_app.py` 增加一个合法新 payload 测试，并参数化非法组合：

```python
def _minimal_bad_case_payload(**overrides: object) -> dict:
    payload = {
        "query": "保单整理对客户有什么作用？",
        "answer": "保单整理能帮助客户看清保障缺口。",
        "top_n": 20,
        "top_k": 5,
        "issue_types": ["usable"],
        "evidence_feedback": [],
        "citations": [],
        "retrieval_evidences": [],
    }
    payload.update(overrides)
    return payload


def _two_dimension_feedback(**overrides: object) -> dict:
    return {
        "chunk_id": "case-a-1",
        "retrieval_judgement": "accurate",
        "answer_usage_judgement": "correct",
        "label": "案例A · 需求分析",
        "text_preview": "先做保单整理。",
        **overrides,
    }


def test_bad_case_route_accepts_two_dimension_feedback(monkeypatch) -> None:
    calls = {}

    def fake_save_bad_case(payload: dict) -> dict:
        calls["payload"] = payload
        return {"ok": True, "bad_case_id": "bad-1", "path": ".local/x.jsonl"}

    monkeypatch.setattr(web_app, "save_bad_case", fake_save_bad_case)
    client = TestClient(web_app.create_app())
    payload = _minimal_bad_case_payload(
        evidence_feedback=[_two_dimension_feedback()]
    )
    response = client.post("/api/bad-cases", json=payload)
    assert response.status_code == 200


@pytest.mark.parametrize(
    "feedback",
    [
        _two_dimension_feedback(retrieval_judgement="unknown"),
        _two_dimension_feedback(answer_usage_judgement="not_applicable"),
        _two_dimension_feedback(
            retrieval_judgement="inaccurate",
            answer_usage_judgement="correct",
            reason="不相关",
        ),
        _two_dimension_feedback(
            retrieval_judgement="inaccurate",
            answer_usage_judgement="not_applicable",
            reason="",
        ),
        _two_dimension_feedback(
            answer_usage_judgement="incorrect",
            reason="",
        ),
    ],
)
def test_bad_case_route_rejects_invalid_two_dimension_feedback(
    feedback: dict,
) -> None:
    client = TestClient(web_app.create_app())
    response = client.post(
        "/api/bad-cases",
        json=_minimal_bad_case_payload(evidence_feedback=[feedback]),
    )
    assert response.status_code == 422
```

在 `tests/test_web_batch_routes.py` 的 `test_bad_case_route_rejects_invalid_payload` 参数中追加：

```python
{
    "evidence_feedback": [
        {
            "chunk_id": "case-a-1",
            "retrieval_judgement": "inaccurate",
            "answer_usage_judgement": "not_applicable",
            "reason": "",
        }
    ]
}
```

并新增合法双维度批量行保存测试，使用该文件现有 `_bad_case_payload` 与 run/store setup，断言状态码为 200，缓存记录中的 `retrieval_judgement` 为 `accurate`、`answer_usage_judgement` 为 `correct`。

- [ ] **Step 6: 运行新路由测试并确认失败**

Run: `uv run pytest tests/test_web_app.py -k "two_dimension" tests/test_web_batch_routes.py -k "two_dimension or invalid_payload" -q`

Expected: 新双维度 payload 被旧 `judgement` 校验拒绝，合法测试 FAIL。

- [ ] **Step 7: 集中实现兼容校验**

在 `bad_cases.py` 增加：

```python
LEGACY_EVIDENCE_JUDGEMENTS = frozenset(
    EVIDENCE_FEEDBACK_JUDGEMENT_LABELS
)


def validate_evidence_feedback_items(
    values: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    for item in values:
        retrieval = item.get("retrieval_judgement")
        answer_usage = item.get("answer_usage_judgement")
        if retrieval is None and answer_usage is None:
            if item.get("judgement") not in LEGACY_EVIDENCE_JUDGEMENTS:
                raise ValueError("证据反馈类型不支持")
            continue
        if retrieval not in RETRIEVAL_JUDGEMENT_LABELS:
            raise ValueError("召回反馈类型不支持")
        if answer_usage not in ANSWER_USAGE_JUDGEMENT_LABELS:
            raise ValueError("回答参考反馈类型不支持")
        valid_pair = (
            retrieval == "accurate"
            and answer_usage in {"correct", "incorrect"}
        ) or (
            retrieval == "inaccurate"
            and answer_usage == "not_applicable"
        )
        if not valid_pair:
            raise ValueError("证据反馈组合不支持")
        is_negative = retrieval == "inaccurate" or answer_usage == "incorrect"
        reason = item.get("reason")
        if is_negative and (not isinstance(reason, str) or not reason.strip()):
            raise ValueError("负向证据反馈必须填写原因")
    return values
```

让 `app.py` 和 `batch_routes.py` 的 `evidence_feedback` validator 直接返回 `validate_evidence_feedback_items(values)`；删除路由文件中重复的 judgement 集合，但保留旧数据兼容测试。

- [ ] **Step 8: 运行后端相关测试**

Run: `uv run pytest tests/test_web_bad_cases.py tests/test_web_app.py tests/test_web_batch_routes.py -q`

Expected: 全部 PASS。

- [ ] **Step 9: 提交后端契约**

```bash
git add src/xhbx_rag/web/bad_cases.py src/xhbx_rag/web/app.py src/xhbx_rag/web/batch_routes.py tests/test_web_bad_cases.py tests/test_web_app.py tests/test_web_batch_routes.py
git commit -m "feat: validate two-dimensional citation feedback"
```

### Task 2: 前端类型与两级反馈交互

**Files:**
- Modify: `web/src/types.ts`
- Modify: `web/src/components/EvidenceDetail.tsx`
- Modify: `web/src/components/EvidenceDetail.test.tsx`
- Modify: `web/src/styles.css`

- [ ] **Step 1: 用新交互测试替换旧二选一测试**

在 `EvidenceDetail.test.tsx` 中删除旧 `EvidenceFeedbackJudgement` harness，创建提交终态的 harness：

```tsx
function FeedbackHarness({
  onSubmit = vi.fn().mockResolvedValue(undefined)
}: {
  onSubmit?: (decision: EvidenceFeedbackDecision) => Promise<void>;
}) {
  const [feedback, setFeedback] = useState<EvidenceFeedback>();
  return (
    <EvidenceDetail
      evidence={evidence}
      index={0}
      feedback={feedback}
      onSubmitFeedback={async (decision) => {
        await onSubmit(decision);
        setFeedback({
          ...decision,
          chunk_id: evidence.chunk_id,
          label: "案例A · 需求分析",
          text_preview: evidence.text ?? ""
        });
      }}
    />
  );
}
```

添加以下行为测试，每个测试只验证一个状态转换：

```tsx
test("初始只显示召回准确性维度", () => {
  render(<FeedbackHarness />);
  expect(screen.getByRole("group", { name: "召回是否准确？" })).toBeInTheDocument();
  expect(screen.queryByRole("group", { name: "回答是否正确参考该引用？" })).not.toBeInTheDocument();
});

test("召回准确后显示回答参考维度", async () => {
  const user = userEvent.setup();
  render(<FeedbackHarness />);
  await user.click(screen.getByLabelText("引用1召回准确"));
  expect(screen.getByRole("group", { name: "回答是否正确参考该引用？" })).toBeInTheDocument();
});
```

再分别增加：召回不准确 placeholder 与必填、参考不正确 placeholder 与必填、取消两条负向路径、第一维度切换清理第二维度、正向自动提交、保存失败可重试、保存后全部锁定测试。

- [ ] **Step 2: 运行组件测试并确认失败**

Run: `cd web && npm test -- src/components/EvidenceDetail.test.tsx`

Expected: FAIL，旧组件仍只提供“应该用/不该用”。

- [ ] **Step 3: 定义新前端类型**

在 `types.ts` 用以下定义替换仅供旧 UI 使用的 `EvidenceFeedbackJudgement`：

```ts
export type RetrievalFeedbackJudgement = "accurate" | "inaccurate";
export type AnswerUsageFeedbackJudgement =
  | "correct"
  | "incorrect"
  | "not_applicable";

export type EvidenceFeedbackDecision = {
  retrieval_judgement: RetrievalFeedbackJudgement;
  answer_usage_judgement: AnswerUsageFeedbackJudgement;
  reason?: string;
};

export type EvidenceFeedback = EvidenceFeedbackDecision & {
  chunk_id?: string;
  label: string;
  text_preview: string;
};
```

- [ ] **Step 4: 实现 EvidenceDetail 两级状态机**

把旧 props 替换为：

```ts
feedback?: EvidenceFeedback;
onSubmitFeedback?: (decision: EvidenceFeedbackDecision) => Promise<void>;
```

组件维护 `retrievalJudgement`、`answerUsageJudgement`、`reasonKind`、`reason`、`saving` 和消息状态。核心转换严格如下：

```ts
function chooseRetrieval(next: RetrievalFeedbackJudgement) {
  setRetrievalJudgement(next);
  setAnswerUsageJudgement(
    next === "inaccurate" ? "not_applicable" : undefined
  );
  setReason("");
  setReasonKind(next === "inaccurate" ? "retrieval" : null);
}

function chooseAnswerUsage(next: "correct" | "incorrect") {
  setAnswerUsageJudgement(next);
  setReason("");
  if (next === "incorrect") {
    setReasonKind("answer");
    return;
  }
  setReasonKind(null);
  void submitDecision({
    retrieval_judgement: "accurate",
    answer_usage_judgement: "correct"
  });
}
```

渲染两个带 legend 的 fieldset/radio group；第二组只在 `retrievalJudgement === "accurate"` 时出现。负向表单动态使用：

```ts
const reasonLabel =
  reasonKind === "retrieval" ? "召回不准确原因" : "参考不正确原因";
const reasonPlaceholder =
  reasonKind === "retrieval"
    ? "例如：该引用与客户问题无关、客户或案例不匹配，未能回答当前异议。"
    : "例如：回答曲解了引用原意、超出证据范围，或遗漏了关键限制。";
```

取消 retrieval 原因时清空两维；取消 answer 原因时保留 retrieval=`accurate`、清空 answer。`feedback` 存在或保存中时禁用全部 radio。成功提示统一为“已记录引用反馈。”。

- [ ] **Step 5: 调整样式**

在 `styles.css` 复用 `.evidence-feedback-actions` 的胶囊选项样式，并增加无边框 fieldset、legend 和维度间距；保持现有移动端布局与 focus-visible 样式，不改变来源/正文区域。

- [ ] **Step 6: 运行组件测试并确认通过**

Run: `cd web && npm test -- src/components/EvidenceDetail.test.tsx`

Expected: 全部 PASS。

- [ ] **Step 7: 提交前端交互**

```bash
git add web/src/types.ts web/src/components/EvidenceDetail.tsx web/src/components/EvidenceDetail.test.tsx web/src/styles.css
git commit -m "feat: add staged citation feedback controls"
```

### Task 3: 统一聊天与批量 payload 映射

**Files:**
- Modify: `web/src/components/BadCasePanel.tsx`
- Modify: `web/src/App.chat.test.tsx`
- Modify: `web/src/App.batch.test.tsx`

- [ ] **Step 1: 改写聊天提交失败测试**

把原有“useful/not useful”集成测试改为三个终态：

```ts
// 正向
evidence_feedback: [{
  chunk_id: "case-a-2",
  retrieval_judgement: "accurate",
  answer_usage_judgement: "correct",
  label: "案例A · 需求分析",
  text_preview: "客户担心预算，可以先承接预算，再对齐保障缺口。"
}]
// 召回负向：feedback_result=citation_issue, issue_types=["citation_wrong"]
// 回答负向：feedback_result=inaccurate, issue_types=["answer_unsupported"]
```

测试交互必须通过新 radio 标签完成，并断言两类负向 payload 的 `problem_detail` 和 `note` 都等于所填原因。

- [ ] **Step 2: 运行聊天集成测试并确认失败**

Run: `cd web && npm test -- src/App.chat.test.tsx -t "citation feedback|引用反馈"`

Expected: FAIL，`BadCasePanel` 仍生成旧 `judgement` payload。

- [ ] **Step 3: 实现统一终态提交函数**

在 `BadCasePanel.tsx` 删除 toggle、`submitEvidenceUseful` 和 `submitEvidenceNotUseful`，改为：

同时从 `../types` 导入 `BadCaseIssueType` 与 `EvidenceFeedbackDecision`。

```ts
async function submitEvidenceFeedback(
  index: number,
  evidence: RetrievalEvidence,
  decision: EvidenceFeedbackDecision
) {
  const entry: EvidenceFeedback = {
    chunk_id: evidence.chunk_id,
    ...decision,
    label: evidenceFeedbackLabel(index, evidence),
    text_preview: evidenceFeedbackPreview(evidence)
  };
  const retrievalWrong = decision.retrieval_judgement === "inaccurate";
  const answerWrong = decision.answer_usage_judgement === "incorrect";
  const feedbackResult = retrievalWrong
    ? "citation_issue"
    : answerWrong
      ? "inaccurate"
      : "usable";
  const issueTypes: BadCaseIssueType[] = retrievalWrong
    ? ["citation_wrong" as const]
    : answerWrong
      ? ["answer_unsupported" as const]
      : ["usable" as const];
  const reason = decision.reason?.trim() ?? "";
  const payload: BadCaseRequest = {
    query: turn.query,
    rewritten_query: response.rewritten_query ?? "",
    answer: response.answer,
    top_n: turn.top_n,
    top_k: turn.top_k,
    feedback_result: feedbackResult,
    problem_tags: [],
    problem_detail: reason,
    expected_answer: "",
    reference_note: "",
    evidence_feedback: [entry],
    issue_types: issueTypes,
    expected_knowledge: "",
    expected_source: "",
    note: reason,
    citations: response.citations,
    retrieval_evidences: evidences
  };
  await submitFeedback(payload);
  onSavedBadCase?.(payload);
  setEvidenceFeedback((items) => ({
    ...items,
    [evidenceFeedbackKey(turn.id, index)]: entry
  }));
}
```

给 `EvidenceDetail` 传 `feedback` 和单一 `onSubmitFeedback`。

- [ ] **Step 4: 运行聊天集成测试并确认通过**

Run: `cd web && npm test -- src/App.chat.test.tsx`

Expected: 全部 PASS。

- [ ] **Step 5: 改写并运行批量集成测试**

把 `App.batch.test.tsx` 中旧标签操作改为新两级 radio，至少覆盖正向与“召回不准确”路径，并断言仍只调用 `/api/batch-runs/{run}/rows/{row}/bad-case`、不调用 `/api/bad-cases`。同时更新“按行隔离”测试，使返回上一行后两个已保存 radio 仍为选中且禁用。

Run: `cd web && npm test -- src/App.batch.test.tsx`

Expected: 全部 PASS。

- [ ] **Step 6: 提交 payload 映射**

```bash
git add web/src/components/BadCasePanel.tsx web/src/App.chat.test.tsx web/src/App.batch.test.tsx
git commit -m "feat: classify citation feedback by retrieval and answer usage"
```

### Task 4: 全量回归与构建

**Files:**
- Verify all files changed in Tasks 1-3

- [ ] **Step 1: 运行后端全量测试**

Run: `uv run pytest`

Expected: 全部 pytest 测试 PASS。

- [ ] **Step 2: 运行前端全量测试**

Run: `cd web && npm test`

Expected: 全部 Vitest 测试 PASS。

- [ ] **Step 3: 运行前端生产构建**

Run: `cd web && npm run build`

Expected: TypeScript 与 Vite 构建成功，退出码 0。

- [ ] **Step 4: 检查契约与差异**

Run: `if rg -n "引用[0-9]+应该用|引用[0-9]+不该用|feedbackJudgement|onSubmitUseful|onSubmitNotUseful" web/src; then exit 1; fi; git diff --check; git status --short`

Expected: `rg` 无旧交互命中；diff 无空白错误；工作区干净。
