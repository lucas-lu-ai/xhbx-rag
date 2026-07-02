import {
  AlertCircle,
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  Database,
  ExternalLink,
  FileText,
  Flag,
  LoaderCircle,
  Plus,
  RefreshCcw,
  Save,
  Search,
  Send,
  Trash2
} from "lucide-react";
import {
  type ChangeEvent,
  type FormEvent,
  type KeyboardEvent,
  useEffect,
  useMemo,
  useRef,
  useState
} from "react";
import { readSheet } from "read-excel-file/universal";
import writeExcelFile from "write-excel-file/universal";

import {
  answerQuestionStream,
  getStatus,
  revealSource,
  submitBadCase
} from "./api";
import {
  backfilledDownloadName,
  badCaseJsonlDownloadName,
  buildBackfilledTable,
  buildBackfilledDelimitedText,
  buildBadCaseJsonl,
  parseBatchDelimitedInput,
  parseBatchTableInput
} from "./batch";
import type {
  AnswerResponse,
  AnswerProcessStep,
  BadCaseRequest,
  BadCaseFeedbackResult,
  BadCaseIssueType,
  BadCaseProblemTag,
  BatchBadCaseJsonlRecord,
  BatchQuestion,
  BatchRunState,
  BatchSourceFormat,
  ChatSession,
  ChatTurn,
  Citation,
  EvidenceFeedback,
  EvidenceFeedbackJudgement,
  RetrievalEvidence,
  StoredChatSessions,
  StatusResponse
} from "./types";

const CHAT_SESSIONS_STORAGE_KEY = "xhbx-rag.chat-sessions.v1";
const DEFAULT_SESSION_TITLE = "新会话";

type WorkMode = "single" | "batch";

function downloadTextFile(filename: string, text: string) {
  const blob = new Blob([text], { type: "text/plain;charset=utf-8" });
  downloadBlobFile(filename, blob);
}

function downloadBlobFile(filename: string, blob: Blob) {
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  try {
    link.click();
  } finally {
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 0);
  }
}

const emptyStatus: StatusResponse = {
  ok: false,
  data_dir: "data",
  milvus_mode: "",
  milvus_target: "",
  milvus_lite_path: "",
  milvus_collection: "",
  config: {},
  errors: []
};

export function App() {
  const [workMode, setWorkMode] = useState<WorkMode>("single");
  const [batchRunning, setBatchRunning] = useState(false);
  const [status, setStatus] = useState<StatusResponse>(emptyStatus);
  const [statusError, setStatusError] = useState("");
  const [query, setQuery] = useState("");
  const [topN, setTopN] = useState(20);
  const [topK, setTopK] = useState(5);
  const [sessionStore, setSessionStore] =
    useState<StoredChatSessions>(loadChatSessions);
  const [selectedCitation, setSelectedCitation] = useState<Citation | null>(null);
  const [loading, setLoading] = useState(false);
  const [formError, setFormError] = useState("");
  const [revealMessage, setRevealMessage] = useState("");
  const activeSession = findActiveSession(sessionStore);
  const turns = activeSession?.turns ?? [];

  useEffect(() => {
    persistChatSessions(sessionStore);
  }, [sessionStore]);

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
    () => latestResponseFromTurns(turns),
    [turns]
  );
  const latestEvidences = latestResponse?.retrieval_evidences ?? [];

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
    const submittedSessionId = activeSession.id;
    updateSessionTurns(
      submittedSessionId,
      (items) => [...items, makeStreamingTurn(id, trimmed, topN, topK)],
      sessionTitleForQuestion(activeSession, trimmed)
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
          onEvent: (event) => {
            if (event.type === "step") {
              updateSessionTurns(submittedSessionId, (items) =>
                appendProcessStep(items, id, {
                  step: event.step,
                  message: event.message,
                  payload: event.payload
                })
              );
            }
            if (event.type === "answer_delta") {
              updateSessionTurns(submittedSessionId, (items) =>
                appendAnswerDelta(items, id, event.text)
              );
            }
            if (event.type === "final") {
              updateSessionTurns(submittedSessionId, (items) =>
                completeTurn(items, id, event.response)
              );
              setSelectedCitation(event.response.citations[0] ?? null);
            }
          }
        }
      );
      updateSessionTurns(submittedSessionId, (items) =>
        completeTurn(items, id, response)
      );
      setSelectedCitation(response.citations[0] ?? null);
    } catch (error) {
      const message = error instanceof Error ? error.message : "问答失败";
      updateSessionTurns(submittedSessionId, (items) =>
        failTurn(items, id, message)
      );
    } finally {
      setLoading(false);
    }
  }

  function handleQueryKeyDown(event: KeyboardEvent<HTMLTextAreaElement>) {
    if (event.key !== "Enter" || event.shiftKey || event.nativeEvent.isComposing) {
      return;
    }

    event.preventDefault();
    if (loading) {
      return;
    }

    event.currentTarget.form?.requestSubmit();
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
    updateSessionTurns(activeSession.id, () => []);
    setSelectedCitation(null);
    setRevealMessage("");
  }

  function createSession() {
    const session = createEmptySession();
    setSessionStore((current) => ({
      ...current,
      active_session_id: session.id,
      sessions: [session, ...current.sessions]
    }));
    setQuery("");
    setFormError("");
    setSelectedCitation(null);
    setRevealMessage("");
  }

  function selectSession(sessionId: string) {
    const session = sessionStore.sessions.find((item) => item.id === sessionId);
    if (!session) {
      return;
    }

    setSessionStore((current) => ({
      ...current,
      active_session_id: sessionId
    }));
    setSelectedCitation(
      latestResponseFromTurns(session.turns)?.citations[0] ?? null
    );
    setRevealMessage("");
  }

  function deleteSession(sessionId: string) {
    const nextStore = deleteSessionFromStore(sessionStore, sessionId);
    const nextActiveSession = findActiveSession(nextStore);

    setSessionStore(nextStore);
    setSelectedCitation(
      latestResponseFromTurns(nextActiveSession.turns)?.citations[0] ?? null
    );
    setRevealMessage("");
  }

  function updateSessionTurns(
    sessionId: string,
    updater: (items: ChatTurn[]) => ChatTurn[],
    title?: string
  ) {
    setSessionStore((current) =>
      updateSession(current, sessionId, (session) => ({
        ...session,
        title: title ?? session.title,
        turns: updater(session.turns),
        updated_at: new Date().toISOString()
      }))
    );
  }

  return (
    <div className="app-shell">
      <aside className="session-panel" aria-label="会话列表">
        <header className="session-header">
          <div>
            <p className="eyebrow">会话</p>
            <h2>问答会话</h2>
          </div>
          <button
            className="ghost-button session-new-button"
            type="button"
            onClick={createSession}
          >
            <Plus size={16} aria-hidden="true" />
            新会话
          </button>
        </header>
        <nav className="session-list" aria-label="历史会话">
          {sessionStore.sessions.map((session) => (
            <div
              className={
                session.id === activeSession.id
                  ? "session-row selected"
                  : "session-row"
              }
              key={session.id}
            >
              <button
                className="session-item"
                type="button"
                aria-pressed={session.id === activeSession.id}
                onClick={() => selectSession(session.id)}
              >
                <span>{session.title}</span>
                <small>
                  {session.turns.length} 轮 · {formatSessionTime(session.updated_at)}
                </small>
              </button>
              <button
                className="session-delete-button"
                type="button"
                aria-label={`删除会话 ${session.title}`}
                onClick={() => deleteSession(session.id)}
              >
                <Trash2 size={15} aria-hidden="true" />
              </button>
            </div>
          ))}
        </nav>
      </aside>

      <main className="qa-panel" aria-label="RAG 问答">
        <header className="panel-header">
          <div>
            <p className="eyebrow">xhbx-rag Web</p>
            <h1>销售知识库问答</h1>
          </div>
          <div className="panel-header-actions">
            <div className="mode-switch" role="group" aria-label="工作模式">
              <button
                className={
                  workMode === "single" ? "mode-button active" : "mode-button"
                }
                type="button"
                aria-pressed={workMode === "single"}
                disabled={batchRunning}
                onClick={() => setWorkMode("single")}
              >
                单问
              </button>
              <button
                className={
                  workMode === "batch" ? "mode-button active" : "mode-button"
                }
                type="button"
                aria-pressed={workMode === "batch"}
                disabled={batchRunning}
                onClick={() => setWorkMode("batch")}
              >
                批量
              </button>
            </div>
            {workMode === "single" && (
              <button
                className="ghost-button"
                type="button"
                onClick={clearTurns}
                disabled={loading}
              >
                <Trash2 size={18} aria-hidden="true" />
                清空
              </button>
            )}
          </div>
        </header>

        {(statusError || status.errors.length > 0) && (
          <div className="status-banner error" role="status">
            <AlertCircle size={18} aria-hidden="true" />
            <span>{statusError || status.errors.join("；")}</span>
          </div>
        )}

        {workMode === "single" ? (
          <>
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
                      <p>
                        {turn.response?.answer ||
                          turn.streaming_answer ||
                          "正在生成回答..."}
                      </p>
                      {turn.response?.rewritten_query && (
                        <p className="meta-text">
                          改写问题：{turn.response.rewritten_query}
                        </p>
                      )}
                      {turn.response && (
                        <>
                          <CitationList
                            citations={turn.response.citations}
                            selectedCitation={selectedCitation}
                            onSelect={(citation) => {
                              setSelectedCitation(citation);
                              setRevealMessage("");
                            }}
                          />
                          <BadCasePanel turn={turn} response={turn.response} />
                        </>
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
          </>
        ) : (
          <BatchPanel
            selectedCitation={selectedCitation}
            onRunningChange={setBatchRunning}
            onCitationSelect={(citation) => {
              setSelectedCitation(citation);
              setRevealMessage("");
            }}
          />
        )}
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
          <EvidenceList response={latestResponse} evidences={latestEvidences} />
        </section>
      </aside>
    </div>
  );
}

function BatchPanel({
  selectedCitation,
  onRunningChange,
  onCitationSelect
}: {
  selectedCitation: Citation | null;
  onRunningChange: (running: boolean) => void;
  onCitationSelect: (citation: Citation) => void;
}) {
  const [batchText, setBatchText] = useState("");
  const [batchState, setBatchState] = useState<BatchRunState | null>(null);
  const [parseError, setParseError] = useState("");
  const [sourceLabel, setSourceLabel] = useState("pasted");
  const [sourceFormat, setSourceFormat] = useState<BatchSourceFormat>("pasted");
  const running = batchState?.running ?? false;
  const runningRef = useRef(running);

  useEffect(() => {
    runningRef.current = running;
  }, [running]);

  function parseBatchText(
    text: string,
    nextSourceLabel: string,
    nextSourceFormat: BatchSourceFormat
  ) {
    try {
      setBatchState(
        parseBatchDelimitedInput({
          text,
          sourceLabel: nextSourceLabel,
          sourceFormat: nextSourceFormat
        })
      );
      setParseError("");
    } catch (error) {
      setBatchState(null);
      setParseError(error instanceof Error ? error.message : "解析失败");
    }
  }

  function handleTextChange(event: ChangeEvent<HTMLTextAreaElement>) {
    if (runningRef.current) {
      return;
    }

    setBatchText(event.target.value);
    setSourceLabel("pasted");
    setSourceFormat("pasted");
    setBatchState(null);
    setParseError("");
  }

  function handleParseClick() {
    if (runningRef.current) {
      return;
    }

    parseBatchText(batchText, sourceLabel, sourceFormat);
  }

  function updateBatchQuestion(
    questionId: string,
    updater: (question: BatchQuestion) => BatchQuestion,
    patch: Partial<Omit<BatchRunState, "questions">> = {}
  ) {
    setBatchState((current) => {
      if (!current) {
        return current;
      }

      return {
        ...current,
        ...patch,
        questions: current.questions.map((question) =>
          question.id === questionId ? updater(question) : question
        )
      };
    });
  }

  function setBatchRunning(nextRunning: boolean, activeQuestionId?: string) {
    runningRef.current = nextRunning;
    onRunningChange(nextRunning);
    setBatchState((current) =>
      current
        ? {
            ...current,
            running: nextRunning,
            active_question_id: nextRunning ? activeQuestionId : undefined
          }
        : current
    );
  }

  async function runBatchQuestion(question: BatchQuestion) {
    const topN = 20;
    const topK = 5;

    updateBatchQuestion(
      question.id,
      (item) => ({
        ...item,
        top_n: topN,
        top_k: topK,
        status: "running",
        process_steps: [],
        streaming_answer: "",
        response: undefined,
        error: undefined,
        bad_case_payload: undefined
      }),
      { active_question_id: question.id }
    );

    try {
      const response = await answerQuestionStream(
        {
          query: question.query,
          top_n: topN,
          top_k: topK
        },
        {
          onEvent: (event) => {
            if (event.type === "step") {
              updateBatchQuestion(question.id, (item) => ({
                ...item,
                process_steps: [
                  ...item.process_steps,
                  {
                    step: event.step,
                    message: event.message,
                    payload: event.payload
                  }
                ]
              }));
            }
            if (event.type === "answer_delta") {
              updateBatchQuestion(question.id, (item) => ({
                ...item,
                streaming_answer: `${item.streaming_answer}${event.text}`
              }));
            }
            if (event.type === "final") {
              updateBatchQuestion(question.id, (item) => ({
                ...item,
                status: "succeeded",
                response: event.response,
                streaming_answer: event.response.answer,
                error: undefined
              }));
            }
          }
        }
      );
      updateBatchQuestion(question.id, (item) => ({
        ...item,
        status: "succeeded",
        response,
        streaming_answer: response.answer,
        error: undefined
      }));
    } catch (error) {
      updateBatchQuestion(question.id, (item) => ({
        ...item,
        status: "failed",
        error: error instanceof Error ? error.message : "批量问题执行失败"
      }));
    }
  }

  async function retryBatchQuestion(question: BatchQuestion) {
    if (runningRef.current || question.status !== "failed") {
      return;
    }

    setBatchRunning(true, question.id);
    try {
      await runBatchQuestion(question);
    } finally {
      setBatchRunning(false);
    }
  }

  async function runBatch() {
    if (!batchState || batchState.running || runningRef.current) {
      return;
    }

    setBatchRunning(true);
    try {
      for (const question of batchState.questions) {
        await runBatchQuestion(question);
      }
    } finally {
      setBatchRunning(false);
    }
  }

  async function downloadBackfilledFile() {
    if (!batchState) {
      return;
    }

    const backfilledTable = buildBackfilledTable({
      headers: batchState.headers,
      rows: batchState.rows,
      questions: batchState.questions
    });
    const fileName = backfilledDownloadName(batchState.source_label);
    if (batchState.source_format === "xlsx") {
      const blob = await writeExcelFile(backfilledTable).toBlob();
      downloadBlobFile(fileName, blob);
      return;
    }

    downloadTextFile(fileName, buildBackfilledDelimitedText({
      headers: batchState.headers,
      rows: batchState.rows,
      questions: batchState.questions
    }));
  }

  function batchBadCaseSourceLabel(
    state: Pick<BatchRunState, "source_label" | "source_format">
  ): string {
    const label = state.source_label;
    const format = state.source_format;
    return format === "pasted" && label === "pasted" ? "pasted.csv" : label;
  }

  function saveBatchBadCasePayload(
    question: BatchQuestion,
    payload: BadCaseRequest
  ) {
    const expectedSourceLabel = batchState?.source_label;
    const expectedSourceFormat = batchState?.source_format;
    setBatchState((current) => {
      if (
        !current ||
        current.source_label !== expectedSourceLabel ||
        current.source_format !== expectedSourceFormat
      ) {
        return current;
      }

      let saved = false;
      const questions = current.questions.map((item) => {
        if (
          item.id !== question.id ||
          item.query !== question.query ||
          item.response !== question.response
        ) {
          return item;
        }

        saved = true;
        return {
          ...item,
          bad_case_payload: {
            ...payload,
            batch_source_label: batchBadCaseSourceLabel(current),
            row_index: item.row_index,
            input_answer: item.input_answer
          }
        };
      });

      return saved ? { ...current, questions } : current;
    });
  }

  function batchBadCaseRecords(): BatchBadCaseJsonlRecord[] {
    return (
      batchState?.questions
        .map((question) => question.bad_case_payload)
        .filter((payload): payload is BatchBadCaseJsonlRecord => Boolean(payload)) ??
      []
    );
  }

  function downloadBadCaseJsonl() {
    if (!batchState) {
      return;
    }

    downloadTextFile(
      badCaseJsonlDownloadName(batchState.source_label),
      buildBadCaseJsonl(batchBadCaseRecords())
    );
  }

  async function handleFileChange(event: ChangeEvent<HTMLInputElement>) {
    if (runningRef.current) {
      return;
    }

    const file = event.target.files?.[0];
    if (!file) {
      return;
    }

    const nextSourceFormat = batchSourceFormatForFile(file.name);
    if (!nextSourceFormat) {
      setBatchState(null);
      setParseError("仅支持 txt、csv 或 xlsx 文件");
      return;
    }

    try {
      if (nextSourceFormat === "xlsx") {
        const tableRows = await readSheet(file);
        if (runningRef.current) {
          return;
        }

        const parsed = parseBatchTableInput({
          rows: tableRows,
          sourceLabel: file.name,
          sourceFormat: nextSourceFormat
        });
        setBatchText(buildBackfilledDelimitedText(parsed));
        setSourceLabel(file.name);
        setSourceFormat(nextSourceFormat);
        setBatchState(parsed);
        setParseError("");
        return;
      }

      const text = await file.text();
      if (runningRef.current) {
        return;
      }

      setBatchText(text);
      setSourceLabel(file.name);
      setSourceFormat(nextSourceFormat);
      parseBatchText(text, file.name, nextSourceFormat);
    } catch {
      if (runningRef.current) {
        return;
      }

      setBatchState(null);
      setParseError("无法读取文件");
    }
  }

  return (
    <section className="batch-panel" aria-label="批量问题">
      <div className="batch-inputs">
        <label className="file-field">
          <span>上传批量文件</span>
          <input
            type="file"
            accept=".txt,.csv,.xlsx"
            disabled={running}
            onChange={(event) => void handleFileChange(event)}
          />
        </label>
        <label className="text-field batch-text-field" htmlFor="batch-content">
          <span>批量问题内容</span>
          <textarea
            id="batch-content"
            rows={8}
            value={batchText}
            disabled={running}
            onChange={handleTextChange}
            placeholder="问题,答案&#10;客户说每年不能超过80万怎么办？,人工答案"
          />
        </label>
      </div>

      <div className="batch-actions">
        <button
          className="secondary-button compact-button"
          type="button"
          disabled={running}
          onClick={handleParseClick}
        >
          解析内容
        </button>
        {batchState && (
          <span className="status-chip">已解析 {batchState.questions.length} 个问题</span>
        )}
      </div>

      {parseError && (
        <p className="form-error" role="alert">
          {parseError}
        </p>
      )}

      <div className="batch-results" aria-live="polite">
        {!batchState ? (
          <div className="batch-empty-state">
            <h2>等待解析</h2>
            <p>粘贴带表头的逗号分隔内容，或上传 txt/csv/xlsx 文件。</p>
          </div>
        ) : (
          <ol className="batch-result-list">
            {batchState.questions.map((question, index) => (
              <li key={question.id}>
                <article className="batch-result-item">
                  <div className="batch-result-heading">
                    <strong>问题 {index + 1}</strong>
                    <div className="batch-actions">
                      <span className="status-chip muted">
                        第 {question.row_index} 行
                      </span>
                      <span
                        className={
                          question.status === "succeeded"
                            ? "status-chip"
                            : "status-chip muted"
                        }
                      >
                        {batchQuestionStatusLabel(question.status)}
                      </span>
                      {question.status === "failed" && (
                        <button
                          className="secondary-button compact-button"
                          type="button"
                          disabled={running}
                          onClick={() => void retryBatchQuestion(question)}
                        >
                          <RefreshCcw size={16} aria-hidden="true" />
                          重试
                        </button>
                      )}
                    </div>
                  </div>
                  <p>{question.query}</p>
                  <div className="batch-original-answer">
                    <span>原答案</span>
                    <p>{question.input_answer.trim() || "未提供"}</p>
                  </div>
                  {(question.status !== "pending" ||
                    question.process_steps.length > 0 ||
                    question.streaming_answer ||
                    question.response ||
                    question.error) && (
                    <div className="batch-original-answer">
                      <span>模型答案</span>
                      <ProcessTimeline
                        active={
                          question.status === "running" && !question.response
                        }
                        steps={question.process_steps}
                      />
                      {question.error ? (
                        <p className="form-error">{question.error}</p>
                      ) : (
                        <p>
                          {question.response?.answer ||
                            question.streaming_answer ||
                            "正在生成回答..."}
                        </p>
                      )}
                      {question.response?.rewritten_query && (
                        <p className="meta-text">
                          改写问题：{question.response.rewritten_query}
                        </p>
                      )}
                      {question.response && (
                        <>
                          <CitationList
                            citations={question.response.citations}
                            selectedCitation={selectedCitation}
                            onSelect={onCitationSelect}
                          />
                          <BadCasePanel
                            turn={batchQuestionToChatTurn(question)}
                            response={question.response}
                            onSavedBadCase={(payload) =>
                              saveBatchBadCasePayload(question, payload)
                            }
                          />
                        </>
                      )}
                    </div>
                  )}
                </article>
              </li>
            ))}
          </ol>
        )}
      </div>

      <div className="batch-footer">
        <button
          className="primary-button"
          type="button"
          disabled={!batchState || running}
          onClick={() => void runBatch()}
        >
          {running && <LoaderCircle className="spin" size={18} aria-hidden="true" />}
          开始批量运行
        </button>
        <button
          className="secondary-button compact-button"
          type="button"
          disabled={
            running ||
            !batchState ||
            !batchState.questions.some((question) => question.status === "succeeded")
          }
          onClick={() => void downloadBackfilledFile()}
        >
          下载回填文件
        </button>
        <button
          className="secondary-button compact-button"
          type="button"
          disabled={running || batchBadCaseRecords().length === 0}
          onClick={downloadBadCaseJsonl}
        >
          下载 bad case JSONL
        </button>
      </div>
    </section>
  );
}

function batchQuestionStatusLabel(status: BatchQuestion["status"]): string {
  if (status === "running") {
    return "运行中";
  }
  if (status === "succeeded") {
    return "已完成";
  }
  if (status === "failed") {
    return "失败";
  }
  return "待运行";
}

function batchQuestionToChatTurn(question: BatchQuestion): ChatTurn {
  return {
    id: question.id,
    query: question.query,
    top_n: question.top_n,
    top_k: question.top_k,
    process_steps: question.process_steps,
    streaming_answer: question.streaming_answer,
    response: question.response,
    error: question.error,
    is_streaming: question.status === "running"
  };
}

function batchSourceFormatForFile(fileName: string): BatchSourceFormat | null {
  const normalized = fileName.toLowerCase();
  if (normalized.endsWith(".csv")) {
    return "csv";
  }
  if (normalized.endsWith(".xlsx")) {
    return "xlsx";
  }
  if (normalized.endsWith(".txt")) {
    return "txt";
  }
  return null;
}

function ProcessTimeline({
  active,
  steps
}: {
  active: boolean;
  steps: AnswerProcessStep[];
}) {
  if (steps.length === 0 && !active) {
    return null;
  }

  return (
    <section className="process-panel" aria-label="处理过程">
      <div className="process-heading">
        <Activity size={16} aria-hidden="true" />
        <strong>处理过程</strong>
        {active && <span>运行中</span>}
      </div>
      {steps.length === 0 ? (
        <p className="meta-text">正在连接问答服务...</p>
      ) : (
        <ol className="process-list">
          {steps.map((step, index) => (
            <li key={`${step.step}-${index}`}>
              <CheckCircle2 size={15} aria-hidden="true" />
              <div>
                <span>{step.message}</span>
                {formatProcessPayload(step.payload) && (
                  <small>{formatProcessPayload(step.payload)}</small>
                )}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function CitationList({
  citations,
  selectedCitation,
  onSelect
}: {
  citations: Citation[];
  selectedCitation: Citation | null;
  onSelect: (citation: Citation) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const maxCollapsedCitations = 3;
  const canToggle = citations.length > maxCollapsedCitations;
  const visibleCitations =
    expanded || !canToggle
      ? citations
      : citations.slice(0, maxCollapsedCitations);

  useEffect(() => {
    setExpanded(false);
  }, [citations]);

  return (
    <div className="citation-list" aria-label="引用列表">
      {citations.length === 0 ? (
        <span className="meta-text">没有可展示引用。</span>
      ) : (
        <>
          {visibleCitations.map((citation, index) => (
            <button
              className={
                citation === selectedCitation
                  ? "citation-chip selected"
                  : "citation-chip"
              }
              key={`${citation.source_path ?? citation.filename ?? "citation"}-${index}`}
              type="button"
              aria-pressed={citation === selectedCitation}
              onClick={() => onSelect(citation)}
            >
              引用 {index + 1} · {citation.filename || "未知文件"} ·{" "}
              {citation.display_location || "未提供精确位置"}
            </button>
          ))}
          {canToggle && (
            <button
              className="inline-button citation-toggle"
              type="button"
              aria-expanded={expanded}
              onClick={() => setExpanded((value) => !value)}
            >
              {expanded ? (
                <ChevronUp size={16} aria-hidden="true" />
              ) : (
                <ChevronDown size={16} aria-hidden="true" />
              )}
              {expanded ? "收起" : "显示更多"}
            </button>
          )}
        </>
      )}
    </div>
  );
}

const feedbackResultOptions: Array<{
  value: BadCaseFeedbackResult;
  label: string;
  tone: "positive" | "negative";
}> = [
  { value: "usable", label: "可用", tone: "positive" },
  { value: "inaccurate", label: "不准确", tone: "negative" },
  { value: "incomplete", label: "不完整", tone: "negative" },
  { value: "citation_issue", label: "引用有问题", tone: "negative" },
  { value: "customer_mismatch", label: "不适合当前客户", tone: "negative" }
];

const problemTagOptions: Array<{ value: BadCaseProblemTag; label: string }> = [
  { value: "off_topic", label: "答非所问" },
  { value: "missing_talk_track", label: "缺关键话术" },
  { value: "case_mismatch", label: "案例不匹配" },
  { value: "citation_mismatch", label: "引用/原文对不上" },
  { value: "not_customer_ready", label: "表达不能直接给客户用" },
  { value: "compliance_risk", label: "可能有合规风险" },
  { value: "other", label: "其他" }
];

function BadCasePanel({
  turn,
  response,
  onSavedBadCase
}: {
  turn: ChatTurn;
  response: AnswerResponse;
  onSavedBadCase?: (payload: BadCaseRequest) => void;
}) {
  const [selectedResult, setSelectedResult] =
    useState<BadCaseFeedbackResult | null>(null);
  const [problemTags, setProblemTags] = useState<BadCaseProblemTag[]>([]);
  const [problemDetail, setProblemDetail] = useState("");
  const [expectedAnswer, setExpectedAnswer] = useState("");
  const [referenceNote, setReferenceNote] = useState("");
  const [evidenceFeedback, setEvidenceFeedback] = useState<
    Record<string, EvidenceFeedback>
  >({});
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState("");
  const [error, setError] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const evidences = response.retrieval_evidences ?? [];
  const showForm = selectedResult !== null && selectedResult !== "usable";

  function toggleProblemTag(value: BadCaseProblemTag) {
    setProblemTags((items) =>
      items.includes(value)
        ? items.filter((item) => item !== value)
        : [...items, value]
    );
    setError("");
    setMessage("");
  }

  function toggleEvidenceFeedback(
    index: number,
    evidence: RetrievalEvidence,
    judgement: EvidenceFeedbackJudgement
  ) {
    const key = evidenceFeedbackKey(index, evidence);
    setEvidenceFeedback((items) => {
      if (items[key]?.judgement === judgement) {
        const { [key]: _removed, ...rest } = items;
        return rest;
      }
      return {
        ...items,
        [key]: {
          chunk_id: evidence.chunk_id,
          judgement,
          label: evidenceFeedbackLabel(index, evidence),
          text_preview: evidenceFeedbackPreview(evidence)
        }
      };
    });
    setError("");
    setMessage("");
  }

  async function saveFeedback(
    feedbackResult: BadCaseFeedbackResult,
    draft = {
      problemTags,
      problemDetail: problemDetail.trim(),
      expectedAnswer: expectedAnswer.trim(),
      referenceNote: referenceNote.trim(),
      evidenceFeedback: Object.values(evidenceFeedback)
    }
  ) {
    setSaving(true);
    setError("");
    setMessage("");
    try {
      const issueTypes = Array.from(
        new Set<BadCaseIssueType>([feedbackResult, ...draft.problemTags])
      );
      const payload: BadCaseRequest = {
        query: turn.query,
        rewritten_query: response.rewritten_query ?? "",
        answer: response.answer,
        top_n: turn.top_n,
        top_k: turn.top_k,
        feedback_result: feedbackResult,
        problem_tags: draft.problemTags,
        problem_detail: draft.problemDetail,
        expected_answer: draft.expectedAnswer,
        reference_note: draft.referenceNote,
        evidence_feedback: draft.evidenceFeedback,
        issue_types: issueTypes,
        expected_knowledge: draft.expectedAnswer,
        expected_source: draft.referenceNote,
        note: draft.problemDetail,
        citations: response.citations,
        retrieval_evidences: evidences
      };
      await submitBadCase(payload);
      if (feedbackResult !== "usable") {
        onSavedBadCase?.(payload);
      }
      setMessage(
        feedbackResult === "usable" ? "已记录可用反馈。" : "反馈已保存。"
      );
      setSubmitted(true);
    } catch (submitError) {
      setError(
        submitError instanceof Error ? submitError.message : "无法保存反馈。"
      );
    } finally {
      setSaving(false);
    }
  }

  async function handleFeedbackResultClick(result: BadCaseFeedbackResult) {
    setSelectedResult(result);
    setError("");
    setMessage("");
    if (result === "usable") {
      setProblemTags([]);
      setProblemDetail("");
      setExpectedAnswer("");
      setReferenceNote("");
      setEvidenceFeedback({});
      await saveFeedback("usable", {
        problemTags: [],
        problemDetail: "",
        expectedAnswer: "",
        referenceNote: "",
        evidenceFeedback: []
      });
    }
  }

  async function handleBadCaseSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!selectedResult || selectedResult === "usable") {
      return;
    }
    await saveFeedback(selectedResult);
  }

  if (submitted) {
    return (
      <section className="bad-case-panel" aria-label="回答反馈">
        {message && <p className="success-text">{message}</p>}
      </section>
    );
  }

  return (
    <section className="bad-case-panel" aria-label="回答反馈">
      <div className="answer-feedback">
        <span>这个回答可用吗？</span>
        <div className="answer-feedback-actions">
          {feedbackResultOptions.map((option) => (
            <button
              className={
                selectedResult === option.value
                  ? `feedback-option ${option.tone} selected`
                  : `feedback-option ${option.tone}`
              }
              key={option.value}
              type="button"
              aria-pressed={selectedResult === option.value}
              disabled={saving}
              onClick={() => void handleFeedbackResultClick(option.value)}
            >
              {option.value === "usable" ? (
                <CheckCircle2 size={15} aria-hidden="true" />
              ) : (
                <Flag size={15} aria-hidden="true" />
              )}
              {option.label}
            </button>
          ))}
        </div>
      </div>

      {showForm && (
        <form className="bad-case-form" onSubmit={handleBadCaseSubmit}>
          <div className="bad-case-form-heading">
            <strong>反馈这次回答</strong>
            <span>问题、答案、引用和检索证据会自动随反馈保存。</span>
          </div>

          <fieldset className="bad-case-fieldset">
            <legend>问题点</legend>
            <div className="bad-case-option-list">
              {problemTagOptions.map((option) => (
                <label className="bad-case-option" key={option.value}>
                  <input
                    type="checkbox"
                    checked={problemTags.includes(option.value)}
                    onChange={() => toggleProblemTag(option.value)}
                  />
                  <span>{option.label}</span>
                </label>
              ))}
            </div>
          </fieldset>

          <div className="bad-case-grid">
            <label className="text-field">
              <span>哪里不对</span>
              <textarea
                rows={3}
                value={problemDetail}
                onChange={(event) => {
                  setProblemDetail(event.target.value);
                  setMessage("");
                }}
                placeholder="例如：回答没有讲清客户为什么要先看保障缺口。"
              />
            </label>
            <label className="text-field">
              <span>正确回答应包含什么</span>
              <textarea
                rows={3}
                value={expectedAnswer}
                onChange={(event) => {
                  setExpectedAnswer(event.target.value);
                  setMessage("");
                }}
                placeholder="例如：应该包含保障缺口分析、预算承接和缴费期调整话术。"
              />
            </label>
          </div>

          <label className="text-field">
            <span>相关案例/章节/文件名</span>
            <input
              type="text"
              value={referenceNote}
              onChange={(event) => {
                setReferenceNote(event.target.value);
                setMessage("");
              }}
              placeholder="例如：案例A 第3节，或客户预算异议处理案例。"
            />
          </label>

          {evidences.length > 0 && (
            <fieldset className="bad-case-fieldset">
              <legend>本次检索证据</legend>
              <div className="bad-case-evidence-list">
                {evidences.map((evidence, index) => {
                  const key = evidenceFeedbackKey(index, evidence);
                  const selectedJudgement = evidenceFeedback[key]?.judgement;
                  return (
                    <article className="bad-case-evidence-item" key={key}>
                      <div>
                        <strong>{evidenceFeedbackLabel(index, evidence)}</strong>
                        <p>{evidenceFeedbackPreview(evidence)}</p>
                      </div>
                      <div className="bad-case-evidence-actions">
                        <label>
                          <input
                            type="checkbox"
                            aria-label={`证据 ${index + 1} 应该用`}
                            checked={selectedJudgement === "should_use"}
                            onChange={() =>
                              toggleEvidenceFeedback(index, evidence, "should_use")
                            }
                          />
                          <span>应该用</span>
                        </label>
                        <label>
                          <input
                            type="checkbox"
                            aria-label={`证据 ${index + 1} 不该用`}
                            checked={selectedJudgement === "should_not_use"}
                            onChange={() =>
                              toggleEvidenceFeedback(
                                index,
                                evidence,
                                "should_not_use"
                              )
                            }
                          />
                          <span>不该用</span>
                        </label>
                      </div>
                    </article>
                  );
                })}
              </div>
            </fieldset>
          )}

          <div className="bad-case-actions">
            <button className="secondary-button compact-button" type="submit" disabled={saving}>
              {saving ? (
                <LoaderCircle className="spin" size={16} aria-hidden="true" />
              ) : (
                <Save size={16} aria-hidden="true" />
              )}
              保存反馈
            </button>
            <span className="bad-case-context">
              已自动包含 {evidences.length} 条检索证据
            </span>
          </div>
          {error && <p className="form-error">{error}</p>}
          {message && <p className="success-text">{message}</p>}
        </form>
      )}
      {!showForm && error && <p className="form-error">{error}</p>}
      {!showForm && message && <p className="success-text">{message}</p>}
    </section>
  );
}

function evidenceFeedbackKey(index: number, evidence: RetrievalEvidence): string {
  return evidence.chunk_id || `evidence-${index}`;
}

function evidenceFeedbackLabel(index: number, evidence: RetrievalEvidence): string {
  return formatEvidenceMeta(evidence.metadata) || `证据 ${index + 1}`;
}

function evidenceFeedbackPreview(evidence: RetrievalEvidence): string {
  const text = evidence.text_preview || evidence.text || "没有正文内容。";
  return text.length > 80 ? `${text.slice(0, 80)}...` : text;
}

function EvidenceList({
  response,
  evidences
}: {
  response?: AnswerResponse;
  evidences: RetrievalEvidence[];
}) {
  return (
    <div className="evidence-section" aria-label="检索证据">
      <div className="pane-heading compact-heading">
        <Search size={20} aria-hidden="true" />
        <h2>检索证据</h2>
        {response && (
          <span className="evidence-count">
            {evidences.length}/{response.evidence_count}
          </span>
        )}
      </div>
      {!response ? (
        <p className="empty-source">暂无检索证据。</p>
      ) : evidences.length === 0 ? (
        <p className="empty-source">本次回答没有可展示检索证据。</p>
      ) : (
        <div
          className="evidence-scroll"
          role="region"
          aria-label="检索证据列表"
          tabIndex={0}
        >
          <div className="evidence-list">
            {evidences.map((evidence, index) => {
              const meta = formatEvidenceMeta(evidence.metadata);
              const score = formatScore(evidence.rerank_score);
              const text =
                evidence.text || evidence.text_preview || "没有正文内容。";
              const citations = evidence.citations ?? [];
              return (
                <article
                  className="evidence-item"
                  key={`${evidence.chunk_id ?? "evidence"}-${index}`}
                >
                  <div className="evidence-header">
                    <strong>
                      证据 {index + 1} · {evidence.chunk_type || "未知类型"}
                    </strong>
                    {score && <span>重排 {score}</span>}
                  </div>
                  {meta && <p className="meta-text">{meta}</p>}
                  <p className="evidence-text">{text}</p>
                  {citations.length > 0 && (
                    <div className="evidence-source-list" aria-label="证据来源">
                      {citations.map((citation, sourceIndex) => (
                        <span
                          className="evidence-source"
                          key={`${
                            citation.source_path ?? citation.filename ?? "source"
                          }-${sourceIndex}`}
                        >
                          {formatEvidenceSource(citation)}
                        </span>
                      ))}
                    </div>
                  )}
                </article>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

function loadChatSessions(): StoredChatSessions {
  const fallback = createDefaultSessionStore();
  const storage = getStorage();
  if (!storage) {
    return fallback;
  }

  try {
    const raw = storage.getItem(CHAT_SESSIONS_STORAGE_KEY);
    if (!raw) {
      return fallback;
    }
    return normalizeStoredChatSessions(JSON.parse(raw)) ?? fallback;
  } catch {
    return fallback;
  }
}

function persistChatSessions(store: StoredChatSessions) {
  const storage = getStorage();
  if (!storage) {
    return;
  }

  try {
    storage.setItem(CHAT_SESSIONS_STORAGE_KEY, JSON.stringify(store));
  } catch {
    // Persistence is best-effort; the active chat should keep working in memory.
  }
}

function normalizeStoredChatSessions(value: unknown): StoredChatSessions | null {
  if (
    !isObject(value) ||
    value.version !== 1 ||
    typeof value.active_session_id !== "string" ||
    !Array.isArray(value.sessions)
  ) {
    return null;
  }

  const sessions = value.sessions.filter(isChatSession);
  if (sessions.length === 0) {
    return null;
  }

  const activeSessionId = sessions.some(
    (session) => session.id === value.active_session_id
  )
    ? value.active_session_id
    : sessions[0].id;

  return {
    version: 1,
    active_session_id: activeSessionId,
    sessions
  };
}

function isChatSession(value: unknown): value is ChatSession {
  return (
    isObject(value) &&
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string" &&
    Array.isArray(value.turns)
  );
}

function createDefaultSessionStore(): StoredChatSessions {
  const session = createEmptySession();
  return {
    version: 1,
    active_session_id: session.id,
    sessions: [session]
  };
}

function createEmptySession(): ChatSession {
  const now = new Date().toISOString();
  return {
    id: makeTurnId(),
    title: DEFAULT_SESSION_TITLE,
    created_at: now,
    updated_at: now,
    turns: []
  };
}

function findActiveSession(store: StoredChatSessions): ChatSession {
  return (
    store.sessions.find((session) => session.id === store.active_session_id) ??
    store.sessions[0] ??
    createEmptySession()
  );
}

function updateSession(
  store: StoredChatSessions,
  sessionId: string,
  updater: (session: ChatSession) => ChatSession
): StoredChatSessions {
  let found = false;
  const sessions = store.sessions.map((session) => {
    if (session.id !== sessionId) {
      return session;
    }
    found = true;
    return updater(session);
  });

  if (!found) {
    return store;
  }

  return {
    ...store,
    sessions
  };
}

function deleteSessionFromStore(
  store: StoredChatSessions,
  sessionId: string
): StoredChatSessions {
  const remaining = store.sessions.filter((session) => session.id !== sessionId);
  if (remaining.length === 0) {
    return createDefaultSessionStore();
  }

  const activeSessionId =
    store.active_session_id === sessionId
      ? mostRecentlyUpdatedSession(remaining).id
      : store.active_session_id;

  return {
    ...store,
    active_session_id: activeSessionId,
    sessions: remaining
  };
}

function mostRecentlyUpdatedSession(sessions: ChatSession[]): ChatSession {
  return [...sessions].sort(
    (left, right) =>
      new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime()
  )[0];
}

function sessionTitleForQuestion(
  session: ChatSession,
  query: string
): string | undefined {
  if (session.title !== DEFAULT_SESSION_TITLE || session.turns.length > 0) {
    return undefined;
  }
  return makeSessionTitle(query);
}

function makeSessionTitle(query: string): string {
  const normalized = query.replace(/\s+/g, " ").trim();
  if (normalized.length <= 32) {
    return normalized || DEFAULT_SESSION_TITLE;
  }
  return `${normalized.slice(0, 32)}...`;
}

function makeStreamingTurn(
  id: string,
  query: string,
  topN: number,
  topK: number
): ChatTurn {
  return {
    id,
    query,
    top_n: topN,
    top_k: topK,
    process_steps: [],
    streaming_answer: "",
    is_streaming: true
  };
}

function appendProcessStep(
  turns: ChatTurn[],
  turnId: string,
  step: AnswerProcessStep
): ChatTurn[] {
  return turns.map((turn) =>
    turn.id === turnId
      ? {
          ...turn,
          process_steps: [...(turn.process_steps ?? []), step]
        }
      : turn
  );
}

function appendAnswerDelta(
  turns: ChatTurn[],
  turnId: string,
  text: string
): ChatTurn[] {
  return turns.map((turn) =>
    turn.id === turnId
      ? {
          ...turn,
          streaming_answer: `${turn.streaming_answer ?? ""}${text}`
        }
      : turn
  );
}

function completeTurn(
  turns: ChatTurn[],
  turnId: string,
  response: AnswerResponse
): ChatTurn[] {
  return turns.map((turn) =>
    turn.id === turnId
      ? {
          ...turn,
          response,
          streaming_answer: response.answer,
          is_streaming: false
        }
      : turn
  );
}

function failTurn(turns: ChatTurn[], turnId: string, message: string): ChatTurn[] {
  return turns.map((turn) =>
    turn.id === turnId ? { ...turn, error: message, is_streaming: false } : turn
  );
}

function latestResponseFromTurns(turns: ChatTurn[]): AnswerResponse | undefined {
  return [...turns].reverse().find((turn) => turn.response)?.response;
}

function getStorage(): Storage | null {
  if (
    typeof localStorage === "undefined" ||
    typeof localStorage.getItem !== "function"
  ) {
    return null;
  }
  return localStorage;
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

function formatEvidenceMeta(metadata?: Record<string, unknown>): string {
  if (!metadata) {
    return "";
  }
  return [stringValue(metadata.case_name), stringValue(metadata.stage)]
    .filter(Boolean)
    .join(" · ");
}

function formatEvidenceSource(citation: Citation): string {
  const file = citation.filename || citation.source_path || "未知文件";
  const location =
    citation.display_location && citation.display_location !== "未提供精确位置"
      ? citation.display_location
      : "";
  return [file, location].filter(Boolean).join(" · ");
}

function formatScore(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value)
    ? value.toFixed(2)
    : "";
}

function formatSessionTime(value: string): string {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return "刚刚";
  }
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit"
  }).format(date);
}

function stringValue(value: unknown): string {
  return typeof value === "string" ? value : "";
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function formatProcessPayload(payload?: Record<string, unknown>): string {
  if (!payload) {
    return "";
  }
  const rewrittenQuery = stringValue(payload.rewritten_query);
  if (rewrittenQuery) {
    return `改写为：${rewrittenQuery}`;
  }
  const candidateCount = numberValue(payload.candidate_count);
  if (candidateCount !== "") {
    return `候选 ${candidateCount} 条`;
  }
  const resultCount = numberValue(payload.result_count);
  if (resultCount !== "") {
    return `结果 ${resultCount} 条`;
  }
  const evidenceCount = numberValue(payload.evidence_count);
  const citationCount = numberValue(payload.citation_count);
  if (evidenceCount !== "" || citationCount !== "") {
    return [`证据 ${evidenceCount || 0} 条`, `引用 ${citationCount || 0} 条`].join(
      " · "
    );
  }
  return "";
}

function numberValue(value: unknown): string {
  return typeof value === "number" && Number.isFinite(value) ? String(value) : "";
}
