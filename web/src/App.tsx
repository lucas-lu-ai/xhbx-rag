import { AlertCircle, ChevronDown, Database, FileText } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { ApiError, deleteBatchRun, getStatus, listBatchRuns } from "./api";
import {
  latestSessionSelection,
  mergeSessionEntries,
  isBatchRunActive
} from "./batchRuns";
import {
  createEmptySession,
  deleteSessionFromStore,
  findActiveSession,
  latestResponseFromTurns,
  loadChatSessions,
  mostRecentlyUpdatedSession,
  persistChatSessions,
  updateSession
} from "./chatSessions";
import { BatchCreateView } from "./components/BatchCreateView";
import { BatchRunView } from "./components/BatchRunView";
import { ChatView } from "./components/ChatView";
import {
  EvidenceDetailContext,
  type EvidenceDetailContextValue
} from "./components/EvidenceDetailContext";
import { SessionSidebar } from "./components/SessionSidebar";
import {
  loadSessionSelection,
  persistSessionSelection
} from "./sessionSelection";
import type {
  BatchRunSummary,
  ChatTurn,
  SessionSelection,
  StatusResponse
} from "./types";

const DEFAULT_BATCH_POLL_INTERVAL_MS = 2000;
const DEFAULT_LIST_POLL_INTERVAL_MS = 5000;
const BATCH_RUNS_LOAD_ERROR = "批量会话列表加载失败，仅显示聊天会话。";
const CASE_COLLECTION_LABEL = "案例知识库";
const COURSE_COLLECTION_LABEL = "课程知识库";
const COLLECTION_LABELS: Record<string, string> = {
  xhbx_sales_chunks: CASE_COLLECTION_LABEL,
  xhbx_course_chunks: COURSE_COLLECTION_LABEL
};

const emptyStatus: StatusResponse = {
  ok: false,
  data_dir: "data",
  milvus_mode: "",
  milvus_target: "",
  milvus_lite_path: "",
  milvus_collection: "",
  milvus_course_collection: "",
  milvus_collections: [],
  batch_concurrency: 1,
  config: {},
  errors: []
};

type AppProps = {
  batchPollIntervalMs?: number;
  listPollIntervalMs?: number;
};

export function App({
  batchPollIntervalMs = DEFAULT_BATCH_POLL_INTERVAL_MS,
  listPollIntervalMs = DEFAULT_LIST_POLL_INTERVAL_MS
}: AppProps = {}) {
  const [status, setStatus] = useState<StatusResponse>(emptyStatus);
  const [statusError, setStatusError] = useState("");
  const [collectionSelection, setCollectionSelection] = useState<string[] | null>(
    null
  );
  const [collectionMenuOpen, setCollectionMenuOpen] = useState(false);
  const [sessionStore, setSessionStore] = useState(loadChatSessions);
  const [batchRuns, setBatchRuns] = useState<BatchRunSummary[]>([]);
  const [batchRunsLoaded, setBatchRunsLoaded] = useState(false);
  const [batchRunsError, setBatchRunsError] = useState("");
  const [deleteError, setDeleteError] = useState("");
  const [selection, setSelection] = useState<SessionSelection | null>(
    loadSessionSelection
  );
  const [creatingBatch, setCreatingBatch] = useState(false);
  // 右侧证据明细：App 只持有选中 key 与 portal 容器，
  // 明细内容由拥有反馈状态的 BadCasePanel portal 进来。
  const [selectedEvidenceKey, setSelectedEvidenceKey] = useState<string | null>(
    null
  );
  const [detailContainer, setDetailContainer] = useState<HTMLElement | null>(
    null
  );

  const collectionOptions = useMemo(
    () => collectionNamesFromStatus(status),
    [status]
  );
  const selectedCollectionNames = useMemo(() => {
    if (collectionOptions.length === 0) {
      return [];
    }
    const baseSelection = collectionSelection ?? collectionOptions;
    const selected = collectionOptions.filter((name) =>
      baseSelection.includes(name)
    );
    return selected.length > 0 ? selected : collectionOptions;
  }, [collectionOptions, collectionSelection]);
  const requestCollections =
    collectionSelection === null ? undefined : selectedCollectionNames;

  useEffect(() => {
    setCollectionSelection((current) => {
      if (current === null) {
        return null;
      }
      const selected = collectionOptions.filter((name) => current.includes(name));
      if (
        selected.length === 0 ||
        selected.length === collectionOptions.length
      ) {
        return null;
      }
      return sameStringList(selected, current) ? current : selected;
    });
  }, [collectionOptions]);

  // 选中态归一：批量按原样保留，聊天校验会话存在性后回退。
  const effectiveSelection = useMemo<SessionSelection>(() => {
    if (selection?.kind === "batch") {
      return selection;
    }
    const candidateId =
      selection?.kind === "chat" ? selection.id : sessionStore.active_session_id;
    const exists = sessionStore.sessions.some(
      (session) => session.id === candidateId
    );
    return {
      kind: "chat",
      id: exists ? candidateId : findActiveSession(sessionStore).id
    };
  }, [selection, sessionStore]);

  const activeChatSession = useMemo(() => {
    if (effectiveSelection.kind !== "chat") {
      return findActiveSession(sessionStore);
    }
    return (
      sessionStore.sessions.find(
        (session) => session.id === effectiveSelection.id
      ) ?? findActiveSession(sessionStore)
    );
  }, [effectiveSelection, sessionStore]);

  useEffect(() => {
    persistChatSessions(sessionStore);
  }, [sessionStore]);

  useEffect(() => {
    persistSessionSelection(effectiveSelection);
  }, [effectiveSelection]);

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

  // 列表读取用单调请求号丢弃过期响应，避免迟到的旧快照覆盖乐观更新。
  const listRequestIdRef = useRef(0);
  const batchRunsLoadedRef = useRef(false);
  const refetchBatchRuns = useCallback(async () => {
    const requestId = ++listRequestIdRef.current;
    try {
      const payload = await listBatchRuns();
      if (requestId !== listRequestIdRef.current) {
        return;
      }
      batchRunsLoadedRef.current = true;
      setBatchRuns(payload.runs);
      setBatchRunsLoaded(true);
      setBatchRunsError("");
    } catch {
      // 仅首次加载失败降级提示；已有列表时静默保留上一次结果。
      if (requestId === listRequestIdRef.current && !batchRunsLoadedRef.current) {
        setBatchRunsError(BATCH_RUNS_LOAD_ERROR);
      }
    }
  }, []);

  // 乐观更新列表：先失效进行中的旧请求，再本地更新，避免被迟到响应回滚。
  const applyOptimisticRuns = useCallback(
    (updater: (current: BatchRunSummary[]) => BatchRunSummary[]) => {
      listRequestIdRef.current += 1;
      setBatchRuns(updater);
    },
    []
  );

  // 首次加载批量会话列表；失败时侧栏降级为只显示聊天会话。
  useEffect(() => {
    void refetchBatchRuns();
  }, [refetchBatchRuns]);

  // 存在非终态 run 时每 5s 轮询列表（递归 setTimeout）。
  const hasActiveRuns = batchRuns.some((run) => isBatchRunActive(run.status));
  useEffect(() => {
    if (!hasActiveRuns) {
      return;
    }
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const tick = async () => {
      await refetchBatchRuns();
      if (!cancelled) {
        timer = setTimeout(() => void tick(), listPollIntervalMs);
      }
    };

    timer = setTimeout(() => void tick(), listPollIntervalMs);
    return () => {
      cancelled = true;
      if (timer !== undefined) {
        clearTimeout(timer);
      }
    };
  }, [hasActiveRuns, listPollIntervalMs, refetchBatchRuns]);

  // 恢复选中批量会话时校验存在性，不存在回退最新聊天会话。
  useEffect(() => {
    if (!batchRunsLoaded || selection?.kind !== "batch") {
      return;
    }
    if (!batchRuns.some((run) => run.run_id === selection.id)) {
      setSelection({
        kind: "chat",
        id: mostRecentlyUpdatedSession(sessionStore.sessions).id
      });
    }
  }, [batchRunsLoaded, batchRuns, selection, sessionStore.sessions]);

  const resetEvidenceSelection = useCallback(() => {
    setSelectedEvidenceKey(null);
  }, []);

  const evidenceDetailContext = useMemo<EvidenceDetailContextValue>(
    () => ({
      container: detailContainer,
      selectedEvidenceKey,
      onSelectEvidence: setSelectedEvidenceKey
    }),
    [detailContainer, selectedEvidenceKey]
  );

  function selectSession(nextSelection: SessionSelection) {
    setSelection(nextSelection);
    setCreatingBatch(false);
    setDeleteError("");
    resetEvidenceSelection();
    if (nextSelection.kind === "chat") {
      setSessionStore((current) => ({
        ...current,
        active_session_id: nextSelection.id
      }));
    }
  }

  function createSession() {
    const session = createEmptySession();
    setSessionStore((current) => ({
      ...current,
      active_session_id: session.id,
      sessions: [session, ...current.sessions]
    }));
    selectSession({ kind: "chat", id: session.id });
  }

  function startBatchCreate() {
    setCreatingBatch(true);
    setDeleteError("");
    resetEvidenceSelection();
  }

  function handleBatchCreated(summary: BatchRunSummary) {
    // POST 响应即完整列表条目，乐观插入已足够；applyOptimisticRuns 会失效
    // 进行中的旧列表请求，避免迟到的旧快照把新会话回滚掉。
    applyOptimisticRuns((current) => [
      summary,
      ...current.filter((run) => run.run_id !== summary.run_id)
    ]);
    selectSession({ kind: "batch", id: summary.run_id });
  }

  function deleteChatSession(sessionId: string) {
    const nextStore = deleteSessionFromStore(sessionStore, sessionId);
    setSessionStore(nextStore);
    if (effectiveSelection.kind === "chat" && effectiveSelection.id === sessionId) {
      setSelection({ kind: "chat", id: nextStore.active_session_id });
      resetEvidenceSelection();
    }
  }

  async function handleDeleteBatchRun(runId: string) {
    setDeleteError("");
    try {
      await deleteBatchRun(runId);
    } catch (error) {
      // 409（运行中）等错误保持选中并展示后端 detail。
      setDeleteError(
        error instanceof ApiError ? error.detail : "删除批量会话失败"
      );
      return;
    }

    const remainingRuns = batchRuns.filter((run) => run.run_id !== runId);
    applyOptimisticRuns(() => remainingRuns);
    if (effectiveSelection.kind === "batch" && effectiveSelection.id === runId) {
      const fallback = latestSessionSelection(
        mergeSessionEntries(sessionStore.sessions, remainingRuns)
      );
      selectSession(fallback ?? { kind: "chat", id: activeChatSession.id });
    }
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

  function toggleCollection(collectionName: string) {
    if (collectionOptions.length <= 1) {
      return;
    }
    setCollectionSelection((current) => {
      const nextSet = new Set(current ?? collectionOptions);
      if (nextSet.has(collectionName)) {
        nextSet.delete(collectionName);
      } else {
        nextSet.add(collectionName);
      }
      const next = collectionOptions.filter((name) => nextSet.has(name));
      if (next.length === 0) {
        return current;
      }
      return next.length === collectionOptions.length ? null : next;
    });
  }

  const chatLatestResponse = useMemo(
    () => latestResponseFromTurns(activeChatSession.turns),
    [activeChatSession.turns]
  );
  const hasEvidenceContext =
    effectiveSelection.kind === "batch" || Boolean(chatLatestResponse);

  return (
    <EvidenceDetailContext.Provider value={evidenceDetailContext}>
    <div className="app-shell">
      <SessionSidebar
        chatSessions={sessionStore.sessions}
        batchRuns={batchRuns}
        batchRunsError={batchRunsError}
        deleteError={deleteError}
        selection={effectiveSelection}
        onSelect={selectSession}
        onDeleteChat={deleteChatSession}
        onDeleteBatch={(runId) => void handleDeleteBatchRun(runId)}
        onCreateSession={createSession}
        onCreateBatch={startBatchCreate}
      />

      <main className="qa-panel" aria-label="RAG 问答">
        <header className="panel-header">
          <div>
            <p className="eyebrow">xhbx-rag Web</p>
            <h1>销售知识库问答</h1>
          </div>
        </header>

        {(statusError || status.errors.length > 0) && (
          <div className="status-banner error" role="status">
            <AlertCircle size={18} aria-hidden="true" />
            <span>{statusError || status.errors.join("；")}</span>
          </div>
        )}

        {creatingBatch ? (
          <BatchCreateView onCreated={handleBatchCreated} />
        ) : effectiveSelection.kind === "batch" ? (
          <BatchRunView
            key={effectiveSelection.id}
            runId={effectiveSelection.id}
            pollIntervalMs={batchPollIntervalMs}
            onRunMutated={() => void refetchBatchRuns()}
          />
        ) : (
          <ChatView
            key={activeChatSession.id}
            session={activeChatSession}
            onUpdateSession={updateSessionTurns}
            selectedCollections={requestCollections}
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
              <dd className="collection-cell">
                {collectionOptions.length > 0 ? (
                  <div className="collection-select">
                    <button
                      aria-expanded={collectionMenuOpen}
                      aria-label="选择 Collection"
                      className="collection-select-button"
                      type="button"
                      onClick={() => setCollectionMenuOpen((open) => !open)}
                    >
                      <span className="collection-primary">
                        {collectionDisplayName(status, selectedCollectionNames[0])}
                      </span>
                      {selectedCollectionNames.length > 1 && (
                        <span className="collection-count">
                          +{selectedCollectionNames.length - 1}
                        </span>
                      )}
                      <ChevronDown size={15} aria-hidden="true" />
                    </button>
                    {collectionMenuOpen && (
                      <div
                        aria-label="可用 Collection"
                        className="collection-menu"
                        role="group"
                      >
                        {collectionOptions.map((name) => {
                          const checked = selectedCollectionNames.includes(name);
                          const label = collectionDisplayName(status, name);
                          return (
                            <label className="collection-option" key={name}>
                              <input
                                aria-label={label}
                                type="checkbox"
                                checked={checked}
                                disabled={
                                  checked && selectedCollectionNames.length === 1
                                }
                                onChange={() => toggleCollection(name)}
                              />
                              <span className="collection-option-text">
                                <span>{label}</span>
                                <small>{name}</small>
                              </span>
                            </label>
                          );
                        })}
                      </div>
                    )}
                  </div>
                ) : (
                  "未配置"
                )}
              </dd>
            </div>
          </dl>
        </section>

        <section className="source-detail">
          <div className="pane-heading">
            <FileText size={20} aria-hidden="true" />
            <h2>证据明细</h2>
          </div>
          <div className="evidence-detail-slot" ref={setDetailContainer} />
          {!selectedEvidenceKey && (
            <p className="empty-source">
              {hasEvidenceContext
                ? "点击一条检索证据查看明细。"
                : "暂无证据。"}
            </p>
          )}
        </section>
      </aside>
    </div>
    </EvidenceDetailContext.Provider>
  );
}

function collectionNamesFromStatus(status: StatusResponse): string[] {
  return uniqueNonEmptyStrings([
    ...(status.milvus_collections ?? []),
    status.milvus_collection,
    status.milvus_course_collection ?? ""
  ]);
}

function collectionDisplayName(status: StatusResponse, name: string): string {
  if (name === status.milvus_collection) {
    return CASE_COLLECTION_LABEL;
  }
  if (name === status.milvus_course_collection) {
    return COURSE_COLLECTION_LABEL;
  }
  return COLLECTION_LABELS[name] ?? name;
}

function uniqueNonEmptyStrings(values: string[]): string[] {
  const result: string[] = [];
  for (const value of values) {
    const normalized = value.trim();
    if (normalized && !result.includes(normalized)) {
      result.push(normalized);
    }
  }
  return result;
}

function sameStringList(left: string[], right: string[]): boolean {
  return (
    left.length === right.length &&
    left.every((value, index) => value === right[index])
  );
}
