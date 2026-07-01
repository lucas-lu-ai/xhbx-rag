import {
  AlertCircle,
  Database,
  ExternalLink,
  FileText,
  LoaderCircle,
  RefreshCcw,
  Send,
  Trash2
} from "lucide-react";
import { type FormEvent, useEffect, useMemo, useState } from "react";

import { answerQuestion, getStatus, revealSource } from "./api";
import type { ChatTurn, Citation, StatusResponse } from "./types";

const emptyStatus: StatusResponse = {
  ok: false,
  data_dir: "data",
  milvus_lite_path: "",
  milvus_collection: "",
  config: {},
  errors: []
};

export function App() {
  const [status, setStatus] = useState<StatusResponse>(emptyStatus);
  const [statusError, setStatusError] = useState("");
  const [query, setQuery] = useState("");
  const [topN, setTopN] = useState(20);
  const [topK, setTopK] = useState(5);
  const [turns, setTurns] = useState<ChatTurn[]>([]);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
  const [loading, setLoading] = useState(false);
  const [formError, setFormError] = useState("");
  const [revealMessage, setRevealMessage] = useState("");

  useEffect(() => {
    let active = true;
    getStatus()
      .then((payload) => {
        if (active) {
          setStatus(payload);
        }
      })
      .catch((error: Error) => {
        if (active) {
          setStatusError(error.message);
        }
      });

    return () => {
      active = false;
    };
  }, []);

  const latestResponse = useMemo(
    () => [...turns].reverse().find((turn) => turn.response)?.response,
    [turns]
  );

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    const trimmed = query.trim();
    if (!trimmed) {
      setFormError("请输入问题后再发送。");
      return;
    }
    const limitError = validateLimits(topN, topK);
    if (limitError) {
      setFormError(limitError);
      return;
    }

    setFormError("");
    setRevealMessage("");
    setLoading(true);
    const id = makeTurnId();
    setTurns((items) => [...items, { id, query: trimmed }]);
    setQuery("");

    try {
      const response = await answerQuestion({
        query: trimmed,
        top_n: topN,
        top_k: topK
      });
      setTurns((items) =>
        items.map((item) => (item.id === id ? { ...item, response } : item))
      );
      setSelectedCitation(response.citations[0] ?? null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "问答失败";
      setTurns((items) =>
        items.map((item) => (item.id === id ? { ...item, error: message } : item))
      );
    } finally {
      setLoading(false);
    }
  }

  async function handleReveal() {
    if (!selectedCitation?.source_path) {
      return;
    }

    try {
      await revealSource({ source_path: selectedCitation.source_path });
      setRevealMessage("已在 Finder 中显示文件。");
    } catch (error) {
      setRevealMessage(error instanceof Error ? error.message : "无法显示文件。");
    }
  }

  function clearTurns() {
    setTurns([]);
    setSelectedCitation(null);
    setRevealMessage("");
  }

  return (
    <div className="app-shell">
      <main className="qa-panel" aria-label="RAG 问答">
        <header className="panel-header">
          <div>
            <p className="eyebrow">xhbx-rag Web</p>
            <h1>销售知识库问答</h1>
          </div>
          <button
            className="ghost-button"
            type="button"
            onClick={clearTurns}
            disabled={loading}
          >
            <Trash2 size={18} aria-hidden="true" />
            清空
          </button>
        </header>

        {(statusError || status.errors.length > 0) && (
          <div className="status-banner error" role="status">
            <AlertCircle size={18} aria-hidden="true" />
            <span>{statusError || status.errors.join("；")}</span>
          </div>
        )}

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

              {turn.response && (
                <div className="message answer-message">
                  <p>{turn.response.answer}</p>
                  {turn.response.rewritten_query && (
                    <p className="meta-text">
                      改写问题：{turn.response.rewritten_query}
                    </p>
                  )}
                  <div className="citation-list" aria-label="引用列表">
                    {turn.response.citations.length === 0 ? (
                      <span className="meta-text">没有可展示引用。</span>
                    ) : (
                      turn.response.citations.map((citation, index) => (
                        <button
                          className={
                            citation === selectedCitation
                              ? "citation-chip selected"
                              : "citation-chip"
                          }
                          key={`${citation.source_path ?? citation.filename ?? "citation"}-${index}`}
                          type="button"
                          aria-pressed={citation === selectedCitation}
                          onClick={() => {
                            setSelectedCitation(citation);
                            setRevealMessage("");
                          }}
                        >
                          引用 {index + 1} · {citation.filename || "未知文件"} ·{" "}
                          {citation.display_location || "未提供精确位置"}
                        </button>
                      ))
                    )}
                  </div>
                </div>
              )}
            </article>
          ))}

          {loading && (
            <div className="message answer-message loading-message">
              <LoaderCircle className="spin" size={18} aria-hidden="true" />
              正在检索并生成回答...
            </div>
          )}
        </section>

        <form className="question-form" onSubmit={handleSubmit}>
          <label htmlFor="query">输入问题</label>
          <textarea
            id="query"
            rows={3}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder="客户说每年不能超过80万怎么办？"
          />
          <div className="form-actions">
            <label className="number-field">
              <span>召回</span>
              <input
                aria-label="召回数量"
                min={1}
                max={100}
                type="number"
                value={topN}
                onChange={(event) => {
                  setTopN(Number(event.target.value));
                  setFormError("");
                }}
              />
            </label>
            <label className="number-field">
              <span>引用</span>
              <input
                aria-label="引用数量"
                min={1}
                max={20}
                type="number"
                value={topK}
                onChange={(event) => {
                  setTopK(Number(event.target.value));
                  setFormError("");
                }}
              />
            </label>
            <button className="primary-button" type="submit" disabled={loading}>
              {loading ? (
                <LoaderCircle className="spin" size={18} aria-hidden="true" />
              ) : (
                <Send size={18} aria-hidden="true" />
              )}
              发送
            </button>
          </div>
          {formError && <p className="form-error">{formError}</p>}
        </form>
      </main>

      <aside className="source-panel" aria-label="索引和溯源">
        <section className="status-card">
          <div className="pane-heading">
            <Database size={20} aria-hidden="true" />
            <h2>索引状态</h2>
          </div>
          <dl>
            <div>
              <dt>状态</dt>
              <dd className={status.ok ? "ok-text" : "error-text"}>
                {status.ok ? "ready" : "needs config"}
              </dd>
            </div>
            <div>
              <dt>数据目录</dt>
              <dd>{status.data_dir}</dd>
            </div>
            <div>
              <dt>Collection</dt>
              <dd>{status.milvus_collection || "未配置"}</dd>
            </div>
          </dl>
        </section>

        <section className="source-detail">
          <div className="pane-heading">
            <FileText size={20} aria-hidden="true" />
            <h2>溯源详情</h2>
          </div>
          {!selectedCitation ? (
            <p className="empty-source">
              {latestResponse ? "请选择一个引用。" : "暂无引用。"}
            </p>
          ) : (
            <div className="source-stack">
              <div className="detail-block">
                <span>文件</span>
                <strong>
                  {selectedCitation.source_path ||
                    selectedCitation.filename ||
                    "未知文件"}
                </strong>
              </div>
              <div className="detail-grid">
                <div className="detail-block">
                  <span>类型</span>
                  <strong>{selectedCitation.source_type || "未知"}</strong>
                </div>
                <div className="detail-block">
                  <span>位置</span>
                  <strong>
                    {selectedCitation.display_location || "未提供精确位置"}
                  </strong>
                </div>
              </div>
              <div className="detail-block">
                <span>定位</span>
                <strong>{selectedCitation.locator_confidence || "未提供"}</strong>
              </div>
              <div className="excerpt-box">
                <span>原文摘录</span>
                <p>
                  {selectedCitation.display_excerpt ||
                    selectedCitation.quote ||
                    "没有摘录内容。"}
                </p>
              </div>
              <button
                className="secondary-button"
                type="button"
                disabled={!selectedCitation.can_reveal}
                onClick={handleReveal}
              >
                <ExternalLink size={18} aria-hidden="true" />
                在 Finder 中显示文件
              </button>
              {revealMessage && <p className="meta-text">{revealMessage}</p>}
            </div>
          )}
        </section>
      </aside>
    </div>
  );
}

function makeTurnId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random()}`;
}

function validateLimits(topN: number, topK: number): string {
  if (!Number.isInteger(topN) || topN < 1 || topN > 100) {
    return "召回数量必须在 1 到 100 之间。";
  }
  if (!Number.isInteger(topK) || topK < 1 || topK > 20) {
    return "引用数量必须在 1 到 20 之间。";
  }
  if (topK > topN) {
    return "引用数量不能大于召回数量。";
  }
  return "";
}
