import { LoaderCircle, RefreshCcw, Send, Trash2 } from "lucide-react";
import {
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useRef,
  useState
} from "react";

import { answerQuestionStream } from "../api";
import {
  appendAnswerDelta,
  appendProcessStep,
  appendThinkingDelta,
  completeTurn,
  failTurn,
  makeStreamingTurn,
  makeTurnId,
  sessionTitleForQuestion
} from "../chatSessions";
import type { AnswerResponse, ChatSession, ChatTurn } from "../types";
import { BadCasePanel } from "./BadCasePanel";
import { firstEvidenceKey, useEvidenceDetail } from "./EvidenceDetailContext";
import { MarkdownMessage } from "./MarkdownMessage";
import { ProcessTimeline } from "./ProcessTimeline";
import { ThinkingProcess } from "./ThinkingProcess";

type ChatViewProps = {
  session: ChatSession;
  onStartTurn: (
    sessionId: string,
    turn: ChatTurn,
    title?: string
  ) => void;
  onUpdateSession: (
    sessionId: string,
    updater: (turns: ChatTurn[]) => ChatTurn[],
    title?: string
  ) => void;
  topN: number;
  topK: number;
};

export function ChatView({
  session,
  onStartTurn,
  onUpdateSession,
  topN,
  topK
}: ChatViewProps) {
  const { onSelectEvidence } = useEvidenceDetail();
  const [query, setQuery] = useState("");
  const [formError, setFormError] = useState("");
  const turns = session.turns;
  // 流式状态从会话 turns 派生，切走再切回同一会话时仍能保持发送/清空守卫。
  const streaming = turns.some((turn) => turn.is_streaming);
  // 组件卸载（切换会话）后不再改动共享的溯源面板，避免跨会话证据串扰。
  const mountedRef = useRef(true);
  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
    };
  }, []);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      setFormError("请输入问题后再发送。");
      return;
    }
    if (streaming) {
      return;
    }
    setFormError("");
    const id = makeTurnId();
    const submittedSessionId = session.id;
    onStartTurn(
      submittedSessionId,
      makeStreamingTurn(id, trimmed, topN, topK),
      sessionTitleForQuestion(session, trimmed)
    );
    setQuery("");

    try {
      const response = await answerQuestionStream(
        {
          query: trimmed,
          top_n: topN,
          top_k: topK
        },
        {
          onEvent: (streamEvent) => {
            if (streamEvent.type === "step") {
              onUpdateSession(submittedSessionId, (items) =>
                appendProcessStep(items, id, {
                  step: streamEvent.step,
                  message: streamEvent.message,
                  payload: streamEvent.payload
                })
              );
            }
            if (streamEvent.type === "thinking_delta") {
              onUpdateSession(submittedSessionId, (items) =>
                appendThinkingDelta(items, id, streamEvent.text)
              );
            }
            if (streamEvent.type === "answer_delta") {
              onUpdateSession(submittedSessionId, (items) =>
                appendAnswerDelta(items, id, streamEvent.text)
              );
            }
            if (streamEvent.type === "final") {
              onUpdateSession(submittedSessionId, (items) =>
                completeTurn(items, id, streamEvent.response)
              );
              selectFirstEvidence(id, streamEvent.response);
            }
          }
        }
      );
      onUpdateSession(submittedSessionId, (items) =>
        completeTurn(items, id, response)
      );
      selectFirstEvidence(id, response);
    } catch (error) {
      const message = error instanceof Error ? error.message : "问答失败";
      onUpdateSession(submittedSessionId, (items) => failTurn(items, id, message));
    }
  }

  // 回答完成后只自动选中第一条模型实际引用；没有实际引用保持未选中。
  function selectFirstEvidence(turnId: string, response: AnswerResponse) {
    if (!mountedRef.current) {
      return;
    }
    onSelectEvidence(
      firstEvidenceKey(
        turnId,
        response.citations,
        response.retrieval_evidences ?? []
      )
    );
  }

  function handleQueryKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }

    event.preventDefault();
    if (streaming) {
      return;
    }

    event.currentTarget.form?.requestSubmit();
  }

  function clearTurns() {
    onUpdateSession(session.id, () => []);
    onSelectEvidence(null);
  }

  return (
    <>
      <div className="view-toolbar">
        <button
          className="ghost-button"
          type="button"
          onClick={clearTurns}
          disabled={streaming}
        >
          <Trash2 size={18} aria-hidden="true" />
          清空
        </button>
      </div>

      <section className="turn-list" aria-live="polite">
        {turns.length === 0 && (
          <div className="empty-state">
            <h2>暂无问答</h2>
            <p>可以询问客户异议、销售策略或案例复盘问题。</p>
          </div>
        )}

        {turns.map((turn) => (
          <article className="turn" key={turn.id}>
            <div className="message user-message">{turn.query}</div>

            {turn.error && (
              <div className="message answer-message error-message">
                <p>{turn.error}</p>
                <button
                  className="inline-button"
                  type="button"
                  onClick={() => setQuery(turn.query)}
                >
                  <RefreshCcw size={16} aria-hidden="true" />
                  重新编辑
                </button>
              </div>
            )}

            {!turn.error &&
              (turn.response ||
                turn.is_streaming ||
                turn.streaming_answer ||
                (turn.process_steps?.length ?? 0) > 0) && (
                <div className="message answer-message">
                  <ProcessTimeline
                    active={Boolean(turn.is_streaming && !turn.response)}
                    steps={turn.process_steps ?? []}
                  />
                  <ThinkingProcess
                    reasoning={
                      turn.response?.reasoning || turn.streaming_reasoning || ""
                    }
                    live={Boolean(
                      turn.is_streaming && !turn.response && !turn.streaming_answer
                    )}
                  />
                  {turn.response?.answer || turn.streaming_answer ? (
                    <MarkdownMessage
                      content={turn.response?.answer || turn.streaming_answer || ""}
                    />
                  ) : (
                    <p>正在生成回答...</p>
                  )}
                  {turn.response?.rewritten_query && (
                    <p className="meta-text">
                      改写问题：{turn.response.rewritten_query}
                    </p>
                  )}
                  {turn.response && (
                    <BadCasePanel turn={turn} response={turn.response} />
                  )}
                </div>
              )}
          </article>
        ))}
      </section>

      <form className="question-form" onSubmit={handleSubmit}>
        <label htmlFor="query">输入问题</label>
        <textarea
          id="query"
          rows={3}
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          onKeyDown={handleQueryKeyDown}
          placeholder="客户说每年不能超过80万怎么办？"
        />
        <div className="form-actions">
          <button className="primary-button" type="submit" disabled={streaming}>
            {streaming ? (
              <LoaderCircle className="spin" size={18} aria-hidden="true" />
            ) : (
              <Send size={18} aria-hidden="true" />
            )}
            发送
          </button>
        </div>
        {formError && <p className="form-error">{formError}</p>}
      </form>
    </>
  );
}
