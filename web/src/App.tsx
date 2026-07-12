import { AlertCircle, ChevronDown, Database, FileText } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  ApiError,
  deleteBatchRun,
  deleteIngestionJob,
  getIngestionJob,
  getStatus,
  listBatchRuns,
  listIngestionJobs,
  retryIngestionJob,
  startIngestionJob
} from "./api";
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
import { IngestionCreateView } from "./components/IngestionCreateView";
import { IngestionDetailPanel } from "./components/IngestionDetailPanel";
import { IngestionRunView } from "./components/IngestionRunView";
import { IngestionSidebar } from "./components/IngestionSidebar";
import { SessionSidebar } from "./components/SessionSidebar";
import { isIngestionJobActive } from "./ingestion";
import {
  loadSessionSelection,
  persistSessionSelection
} from "./sessionSelection";
import type {
  BatchRunSummary,
  ChatTurn,
  IngestionJobDetail,
  IngestionJobProgress,
  IngestionJobSummary,
  SessionSelection,
  StatusResponse
} from "./types";
import {
  navigateWorkspaceLocation,
  parseWorkspaceLocation,
  subscribeWorkspaceLocation,
  type WorkspaceLocation
} from "./workspaceLocation";

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
  ingestionPollIntervalMs?: number;
};

type IngestionActionKind = "start" | "retry" | "delete";

type IngestionActionState = {
  jobId: string;
  kind: IngestionActionKind;
  token: number;
  pending: boolean;
  error: string;
};

export function App({
  batchPollIntervalMs = DEFAULT_BATCH_POLL_INTERVAL_MS,
  listPollIntervalMs = DEFAULT_LIST_POLL_INTERVAL_MS,
  ingestionPollIntervalMs = DEFAULT_BATCH_POLL_INTERVAL_MS
}: AppProps = {}) {
  const [workspaceLocation, setWorkspaceLocation] = useState<WorkspaceLocation>(
    () => parseWorkspaceLocation(window.location.search)
  );
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
  const [ingestionJobs, setIngestionJobs] = useState<IngestionJobSummary[]>([]);
  const [ingestionJobsLoading, setIngestionJobsLoading] = useState(false);
  const [ingestionJobsLoaded, setIngestionJobsLoaded] = useState(false);
  const [ingestionJobsError, setIngestionJobsError] = useState("");
  const [ingestionDetail, setIngestionDetail] = useState<IngestionJobDetail | null>(null);
  const [ingestionDetailLoading, setIngestionDetailLoading] = useState(false);
  const [ingestionDetailError, setIngestionDetailError] = useState("");
  const [ingestionAction, setIngestionAction] = useState<IngestionActionState | null>(null);
  const ingestionActionTokenRef = useRef(0);
  const ingestionPendingJobsRef = useRef(new Set<string>());
  const ingestionListRequestRef = useRef(0);
  const ingestionDetailRequestRef = useRef(0);
  const appMountedRef = useRef(true);

  const selectedIngestionJobId =
    workspaceLocation.view === "ingestion" ? workspaceLocation.jobId : undefined;
  const selectedIngestionJobIdRef = useRef(selectedIngestionJobId);
  selectedIngestionJobIdRef.current = selectedIngestionJobId;

  useEffect(() => {
    appMountedRef.current = true;
    const unsubscribe = subscribeWorkspaceLocation(setWorkspaceLocation);
    return () => {
      appMountedRef.current = false;
      unsubscribe();
    };
  }, []);

  const refetchIngestionJobs = useCallback(async (): Promise<IngestionJobSummary[] | null> => {
    const requestId = ++ingestionListRequestRef.current;
    setIngestionJobsLoading(true);
    try {
      const payload = await listIngestionJobs();
      if (!appMountedRef.current || requestId !== ingestionListRequestRef.current) return null;
      setIngestionJobs(payload.jobs);
      setIngestionJobsLoaded(true);
      setIngestionJobsError("");
      return payload.jobs;
    } catch (error) {
      if (!appMountedRef.current || requestId !== ingestionListRequestRef.current) return null;
      setIngestionJobsError(apiErrorMessage(error, "入库任务列表加载失败，请稍后刷新"));
      return null;
    } finally {
      if (appMountedRef.current && requestId === ingestionListRequestRef.current) {
        setIngestionJobsLoading(false);
      }
    }
  }, []);

  const applyOptimisticIngestionJobs = useCallback(
    (updater: (current: IngestionJobSummary[]) => IngestionJobSummary[]) => {
      ingestionListRequestRef.current += 1;
      setIngestionJobs(updater);
      setIngestionJobsLoaded(true);
    },
    []
  );

  const refetchIngestionDetail = useCallback(async (
    requestedJobId?: string
  ): Promise<IngestionJobDetail | null> => {
    const jobId = requestedJobId ?? selectedIngestionJobIdRef.current;
    if (!jobId) {
      ingestionDetailRequestRef.current += 1;
      setIngestionDetail(null);
      setIngestionDetailLoading(false);
      setIngestionDetailError("");
      return null;
    }
    const requestId = ++ingestionDetailRequestRef.current;
    setIngestionDetailError("");
    setIngestionDetail((current) => current?.job_id === jobId ? current : null);
    setIngestionDetailLoading(true);
    try {
      const payload = await getIngestionJob(jobId);
      if (
        !appMountedRef.current ||
        requestId !== ingestionDetailRequestRef.current ||
        selectedIngestionJobIdRef.current !== jobId
      ) return null;
      setIngestionDetail(payload);
      setIngestionDetailError("");
      return payload;
    } catch (error) {
      if (
        !appMountedRef.current ||
        requestId !== ingestionDetailRequestRef.current ||
        selectedIngestionJobIdRef.current !== jobId
      ) return null;
      setIngestionDetail((current) => current?.job_id === jobId ? current : null);
      setIngestionDetailError(apiErrorMessage(error, "入库任务详情加载失败，请重新选择"));
      return null;
    } finally {
      if (appMountedRef.current && requestId === ingestionDetailRequestRef.current) {
        setIngestionDetailLoading(false);
      }
    }
  }, []);

  useEffect(() => {
    if (workspaceLocation.view === "ingestion") void refetchIngestionJobs();
  }, [refetchIngestionJobs, workspaceLocation.view]);

  useEffect(() => {
    void refetchIngestionDetail(selectedIngestionJobId);
  }, [refetchIngestionDetail, selectedIngestionJobId]);

  const hasActiveIngestionJobs = ingestionJobs.some((job) => isIngestionJobActive(job.status));
  useEffect(() => {
    if (workspaceLocation.view !== "ingestion" || !hasActiveIngestionJobs) return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;
    const tick = async () => {
      await refetchIngestionJobs();
      if (!cancelled) timer = setTimeout(() => void tick(), listPollIntervalMs);
    };
    timer = setTimeout(() => void tick(), listPollIntervalMs);
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, [hasActiveIngestionJobs, listPollIntervalMs, refetchIngestionJobs, workspaceLocation.view]);

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

  function navigateWorkspace(view: WorkspaceLocation["view"]) {
    navigateWorkspaceLocation(view === "chat" ? { view: "chat" } : { view: "ingestion" });
  }

  function selectIngestionJob(jobId: string) {
    navigateWorkspaceLocation({ view: "ingestion", jobId });
  }

  function handleIngestionCreated(detail: IngestionJobDetail) {
    setIngestionDetail(detail);
    applyOptimisticIngestionJobs((current) => upsertIngestionJob(current, detail));
    selectIngestionJob(detail.job_id);
  }

  function beginIngestionAction(
    jobId: string,
    kind: IngestionActionKind
  ): number | null {
    if (ingestionPendingJobsRef.current.has(jobId)) return null;
    const token = ++ingestionActionTokenRef.current;
    ingestionPendingJobsRef.current.add(jobId);
    setIngestionAction({ jobId, kind, token, pending: true, error: "" });
    return token;
  }

  function finishIngestionAction(
    jobId: string,
    token: number,
    error: string
  ) {
    ingestionPendingJobsRef.current.delete(jobId);
    setIngestionAction((current) =>
      current?.jobId === jobId && current.token === token
        ? error
          ? { ...current, pending: false, error }
          : null
        : current
    );
  }

  async function runIngestionAction(
    kind: "start" | "retry",
    jobId: string,
    action: () => Promise<unknown>,
    fallbackError: string
  ) {
    const token = beginIngestionAction(jobId, kind);
    if (token === null) return;
    let actionError = "";
    try {
      await action();
    } catch (error) {
      actionError = apiErrorMessage(error, fallbackError);
    }

    if (!appMountedRef.current) return;
    if (!actionError) {
      applyOptimisticIngestionJobs((current) =>
        current.map((job) => job.job_id === jobId ? { ...job, status: "queued" } : job)
      );
      if (selectedIngestionJobIdRef.current === jobId) {
        setIngestionDetail((current) =>
          current?.job_id === jobId ? { ...current, status: "queued" } : current
        );
      }
    }

    const listPromise = refetchIngestionJobs();
    const detailPromise =
      actionError && selectedIngestionJobIdRef.current === jobId
        ? refetchIngestionDetail(jobId)
        : Promise.resolve(null);
    await Promise.all([listPromise, detailPromise]);
    if (appMountedRef.current) finishIngestionAction(jobId, token, actionError);
  }

  const handleIngestionProgress = useCallback(
    (progress: IngestionJobProgress) => {
      setIngestionDetail((current) =>
        current?.job_id === progress.job_id
          ? {
              ...current,
              status: progress.status,
              current_stage: progress.current_stage,
              item_total: progress.item_total,
              item_done: progress.item_done,
              document_total: progress.document_total,
              chunk_total: progress.chunk_total,
              warning_count: progress.warning_count,
              updated_at: progress.updated_at
            }
          : current
      );
      applyOptimisticIngestionJobs((current) =>
        current.map((job) =>
          job.job_id === progress.job_id
            ? {
                ...job,
                status: progress.status,
                current_stage: progress.current_stage,
                item_total: progress.item_total,
                item_done: progress.item_done,
                document_total: progress.document_total,
                chunk_total: progress.chunk_total,
                warning_count: progress.warning_count,
                updated_at: progress.updated_at
              }
            : job
        )
      );
    },
    [applyOptimisticIngestionJobs]
  );

  function commitDeletedIngestion(jobId: string): boolean {
    applyOptimisticIngestionJobs((current) => current.filter((job) => job.job_id !== jobId));
    const currentLocation = parseWorkspaceLocation(window.location.search);
    const wasSelected =
      currentLocation.view === "ingestion" && currentLocation.jobId === jobId;
    if (wasSelected) {
      ingestionDetailRequestRef.current += 1;
      setIngestionDetail(null);
      setIngestionDetailError("");
      navigateWorkspaceLocation({ view: "ingestion" }, { replace: true });
    }
    return wasSelected;
  }

  async function reconcileDeletedIngestion(wasSelected: boolean) {
    const jobs = await refetchIngestionJobs();
    if (!jobs || !wasSelected || !appMountedRef.current) return;
    navigateToIngestionFallback(jobs);
  }

  function navigateToIngestionFallback(jobs: IngestionJobSummary[]) {
    const currentLocation = parseWorkspaceLocation(window.location.search);
    if (currentLocation.view === "ingestion" && !currentLocation.jobId) {
      const fallback = jobs[0]?.job_id;
      if (fallback) {
        navigateWorkspaceLocation({ view: "ingestion", jobId: fallback }, { replace: true });
      }
    }
  }

  async function handleDeleteIngestion(): Promise<boolean> {
    if (!ingestionDetail) return false;
    const jobId = ingestionDetail.job_id;
    const token = beginIngestionAction(jobId, "delete");
    if (token === null) return false;
    try {
      await deleteIngestionJob(jobId);
      if (!appMountedRef.current) return false;
      const wasSelected = commitDeletedIngestion(jobId);
      finishIngestionAction(jobId, token, "");
      void reconcileDeletedIngestion(wasSelected);
      return true;
    } catch (error) {
      const actionError = apiErrorMessage(error, "删除任务失败，请稍后重试");
      if (!appMountedRef.current) return false;
      const jobsPromise = refetchIngestionJobs();
      const detailPromise =
        selectedIngestionJobIdRef.current === jobId
          ? refetchIngestionDetail(jobId)
          : Promise.resolve(null);
      const [jobs, reconciledDetail] = await Promise.all([jobsPromise, detailPromise]);
      if (!appMountedRef.current) return false;
      if (jobs && !jobs.some((job) => job.job_id === jobId)) {
        const wasSelected = commitDeletedIngestion(jobId);
        finishIngestionAction(jobId, token, "");
        if (wasSelected) navigateToIngestionFallback(jobs);
        return true;
      }
      if (reconciledDetail?.status === "deleting") {
        finishIngestionAction(jobId, token, actionError);
        return true;
      }
      finishIngestionAction(jobId, token, actionError);
      return false;
    }
  }

  const selectedIngestionAction =
    ingestionDetail && ingestionAction?.jobId === ingestionDetail.job_id
      ? ingestionAction
      : null;

  if (workspaceLocation.view === "ingestion") {
    return (
      <div className="app-shell ingestion-shell">
        <IngestionSidebar
          jobs={ingestionJobs}
          selectedJobId={workspaceLocation.jobId}
          loading={ingestionJobsLoading && !ingestionJobsLoaded}
          error={ingestionJobsError}
          onSelect={selectIngestionJob}
          onCreate={() => navigateWorkspaceLocation({ view: "ingestion" })}
          onRefresh={() => void refetchIngestionJobs()}
          onOpenChat={() => navigateWorkspace("chat")}
        />
        <main className="qa-panel ingestion-main" aria-label="文档入库工作台">
          <header className="panel-header">
            <div><p className="eyebrow">xhbx-rag Web</p><h1>文档入库工作台</h1></div>
          </header>
          {workspaceLocation.jobId ? (
            ingestionDetail ? (
              <IngestionRunView
                key={`${ingestionDetail.job_id}:${ingestionDetail.status}`}
                detail={ingestionDetail}
                actionPending={selectedIngestionAction?.pending ?? false}
                actionError={selectedIngestionAction?.error ?? ""}
                pollIntervalMs={ingestionPollIntervalMs}
                onStart={() =>
                  void runIngestionAction(
                    "start",
                    ingestionDetail.job_id,
                    () => startIngestionJob(ingestionDetail.job_id),
                    "启动任务失败，请稍后重试"
                  )
                }
                onRetry={() =>
                  void runIngestionAction(
                    "retry",
                    ingestionDetail.job_id,
                    () => retryIngestionJob(ingestionDetail.job_id),
                    "重试任务失败，请稍后重试"
                  )
                }
                onDelete={handleDeleteIngestion}
                onProgress={handleIngestionProgress}
                onRefresh={() => void refetchIngestionDetail(ingestionDetail.job_id)}
              />
            ) : (
              <div className="ingestion-main-state" role={ingestionDetailError ? "alert" : "status"}>
                <span>{ingestionDetailError || "正在加载任务详情…"}</span>
                {ingestionDetailError && (
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={() => void refetchIngestionDetail(workspaceLocation.jobId)}
                  >
                    重新加载任务详情
                  </button>
                )}
              </div>
            )
          ) : (
            <IngestionCreateView onCreated={handleIngestionCreated} />
          )}
        </main>
        <IngestionDetailPanel
          detail={ingestionDetail}
          loading={ingestionDetailLoading}
          error={ingestionDetailError}
          onReload={() => void refetchIngestionDetail(workspaceLocation.jobId)}
        />
      </div>
    );
  }

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
        onOpenIngestion={() => navigateWorkspace("ingestion")}
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

function apiErrorMessage(error: unknown, fallback: string): string {
  return error instanceof ApiError ? error.detail : fallback;
}

function upsertIngestionJob(
  jobs: IngestionJobSummary[],
  detail: IngestionJobDetail
): IngestionJobSummary[] {
  const {
    ignored_entries: _ignoredEntries,
    items: _items,
    attempt: _attempt,
    events: _events,
    ...summary
  } = detail;
  return [summary, ...jobs.filter((job) => job.job_id !== detail.job_id)];
}
