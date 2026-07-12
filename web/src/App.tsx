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

type RefreshResult<T> =
  | { status: "success"; value: T; requestGeneration: number }
  | { status: "error"; error: string }
  | { status: "stale" };

type IngestionJobFreshness = {
  updatedAt: string | null;
  requestGeneration: number;
  tombstoned: boolean;
};

type IngestionDetailRefreshMode = "automatic" | "manual";

type IngestionDetailRefreshCoordinator = {
  inFlight: Promise<RefreshResult<IngestionJobDetail>> | null;
  trailing: boolean;
  generation: number;
  waiters: Array<{
    generation: number;
    resolve: (result: RefreshResult<IngestionJobDetail>) => void;
  }>;
  cancelled: boolean;
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
  const [ingestionActions, setIngestionActions] = useState<Record<string, IngestionActionState>>({});
  const ingestionActionTokenRef = useRef(0);
  const ingestionActionTokensRef = useRef(new Map<string, number>());
  const ingestionPendingJobsRef = useRef(new Set<string>());
  const ingestionListRequestRef = useRef(0);
  const ingestionGlobalGenerationRef = useRef(0);
  const ingestionFreshnessRef = useRef(new Map<string, IngestionJobFreshness>());
  const ingestionJobsRef = useRef<IngestionJobSummary[]>([]);
  const ingestionJobVersionsRef = useRef(new Map<string, number>());
  const ingestionDetailRequestRef = useRef(0);
  const ingestionDetailRefreshesRef = useRef(
    new Map<string, IngestionDetailRefreshCoordinator>()
  );
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
      for (const coordinator of ingestionDetailRefreshesRef.current.values()) {
        coordinator.cancelled = true;
        coordinator.trailing = false;
        const waiters = coordinator.waiters.splice(0);
        waiters.forEach(({ resolve }) => resolve({ status: "stale" }));
      }
      ingestionDetailRefreshesRef.current.clear();
      unsubscribe();
    };
  }, []);

  const nextIngestionGeneration = useCallback(
    () => ++ingestionGlobalGenerationRef.current,
    []
  );

  const acceptIngestionFreshness = useCallback((
    jobId: string,
    updatedAt: string | null,
    requestGeneration: number,
    tombstoned = false
  ): boolean => {
    const candidate: IngestionJobFreshness = {
      updatedAt,
      requestGeneration,
      tombstoned
    };
    const current = ingestionFreshnessRef.current.get(jobId);
    if (!isAcceptedIngestionFreshness(candidate, current)) return false;
    ingestionFreshnessRef.current.set(jobId, candidate);
    return true;
  }, []);

  const bumpIngestionJobVersion = useCallback((jobId: string) => {
    const version = (ingestionJobVersionsRef.current.get(jobId) ?? 0) + 1;
    ingestionJobVersionsRef.current.set(jobId, version);
  }, []);

  const refetchIngestionJobs = useCallback(async (): Promise<RefreshResult<IngestionJobSummary[]>> => {
    const requestId = ++ingestionListRequestRef.current;
    const requestGeneration = nextIngestionGeneration();
    const versionSnapshot = new Map(ingestionJobVersionsRef.current);
    setIngestionJobsLoading(true);
    try {
      const payload = await listIngestionJobs();
      if (!appMountedRef.current) return { status: "stale" };
      if (requestId === ingestionListRequestRef.current) {
        const currentJobs = ingestionJobsRef.current;
        const currentById = new Map(currentJobs.map((job) => [job.job_id, job]));
        const serverIds = new Set<string>();
        const removedJobIds: string[] = [];
        const mergedJobs: IngestionJobSummary[] = [];
        for (const serverJob of payload.jobs) {
          const jobId = serverJob.job_id;
          if (serverIds.has(jobId)) continue;
          serverIds.add(jobId);
          if (acceptIngestionFreshness(
            jobId,
            serverJob.updated_at,
            requestGeneration
          )) {
            mergedJobs.push(serverJob);
          } else {
            const localJob = currentById.get(jobId);
            if (
              localJob &&
              !ingestionFreshnessRef.current.get(jobId)?.tombstoned
            ) mergedJobs.push(localJob);
          }
        }
        for (const localJob of currentJobs) {
          const jobId = localJob.job_id;
          if (serverIds.has(jobId)) continue;
          const changedWithoutFreshness =
            (ingestionJobVersionsRef.current.get(jobId) ?? 0) !==
              (versionSnapshot.get(jobId) ?? 0) &&
            !ingestionFreshnessRef.current.has(jobId);
          if (changedWithoutFreshness) {
            mergedJobs.push(localJob);
            continue;
          }
          if (acceptIngestionFreshness(jobId, null, requestGeneration, true)) {
            bumpIngestionJobVersion(jobId);
            removedJobIds.push(jobId);
          } else if (!ingestionFreshnessRef.current.get(jobId)?.tombstoned) {
            mergedJobs.push(localJob);
          }
        }
        ingestionJobsRef.current = mergedJobs;
        setIngestionJobs(mergedJobs);
        setIngestionJobsLoaded(true);
        setIngestionJobsError("");
        const selectedJobId = selectedIngestionJobIdRef.current;
        const selectedSummary = mergedJobs.find((job) => job.job_id === selectedJobId);
        if (selectedSummary) {
          setIngestionDetail((current) =>
            current && current.job_id === selectedJobId
              ? mergeSummaryIntoDetail(current, selectedSummary)
              : current
          );
        } else if (selectedJobId && removedJobIds.includes(selectedJobId)) {
          clearTombstonedIngestionJobSelection(selectedJobId);
        }
      }
      return { status: "success", value: payload.jobs, requestGeneration };
    } catch (error) {
      if (!appMountedRef.current || requestId !== ingestionListRequestRef.current) {
        return { status: "stale" };
      }
      const message = apiErrorMessage(error, "入库任务列表加载失败，请稍后刷新");
      setIngestionJobsError(message);
      return { status: "error", error: message };
    } finally {
      if (appMountedRef.current && requestId === ingestionListRequestRef.current) {
        setIngestionJobsLoading(false);
      }
    }
  }, [
    acceptIngestionFreshness,
    bumpIngestionJobVersion,
    nextIngestionGeneration
  ]);

  const applyOptimisticIngestionJobs = useCallback(
    (
      jobId: string,
      updatedAt: string | null,
      updater: (current: IngestionJobSummary[]) => IngestionJobSummary[]
    ): boolean => {
      const requestGeneration = nextIngestionGeneration();
      if (!acceptIngestionFreshness(jobId, updatedAt, requestGeneration)) return false;
      bumpIngestionJobVersion(jobId);
      const next = updater(ingestionJobsRef.current);
      ingestionJobsRef.current = next;
      setIngestionJobs(next);
      setIngestionJobsLoaded(true);
      return true;
    },
    [acceptIngestionFreshness, bumpIngestionJobVersion, nextIngestionGeneration]
  );

  const applyIngestionSummaryCandidate = useCallback((
    summary: IngestionJobSummary,
    requestGeneration: number
  ): boolean => {
    const jobId = summary.job_id;
    if (!acceptIngestionFreshness(jobId, summary.updated_at, requestGeneration)) return false;
    bumpIngestionJobVersion(jobId);
    const next = upsertIngestionSummary(ingestionJobsRef.current, summary);
    ingestionJobsRef.current = next;
    setIngestionJobs(next);
    setIngestionJobsLoaded(true);
    if (selectedIngestionJobIdRef.current === jobId) {
      setIngestionDetail((current) =>
        current?.job_id === jobId ? mergeSummaryIntoDetail(current, summary) : current
      );
    }
    return true;
  }, [acceptIngestionFreshness, bumpIngestionJobVersion]);

  const performIngestionDetailRefresh = useCallback(async (
    jobId: string
  ): Promise<RefreshResult<IngestionJobDetail>> => {
    const requestId = ++ingestionDetailRequestRef.current;
    const requestGeneration = nextIngestionGeneration();
    setIngestionDetailError("");
    setIngestionDetail((current) => current?.job_id === jobId ? current : null);
    setIngestionDetailLoading(true);
    try {
      const payload = await getIngestionJob(jobId);
      if (
        !appMountedRef.current ||
        requestId !== ingestionDetailRequestRef.current ||
        selectedIngestionJobIdRef.current !== jobId
      ) return { status: "stale" };
      if (!acceptIngestionFreshness(jobId, payload.updated_at, requestGeneration)) {
        return { status: "stale" };
      }
      setIngestionDetail(payload);
      setIngestionDetailError("");
      bumpIngestionJobVersion(jobId);
      const summary = summaryFromIngestionDetail(payload);
      const nextJobs = upsertIngestionSummary(ingestionJobsRef.current, summary);
      ingestionJobsRef.current = nextJobs;
      setIngestionJobs(nextJobs);
      setIngestionJobsLoaded(true);
      return { status: "success", value: payload, requestGeneration };
    } catch (error) {
      if (
        !appMountedRef.current ||
        requestId !== ingestionDetailRequestRef.current ||
        selectedIngestionJobIdRef.current !== jobId
      ) return { status: "stale" };
      const message = apiErrorMessage(error, "入库任务详情加载失败，请重新选择");
      setIngestionDetail((current) => current?.job_id === jobId ? current : null);
      setIngestionDetailError(message);
      return { status: "error", error: message };
    } finally {
      if (appMountedRef.current && requestId === ingestionDetailRequestRef.current) {
        setIngestionDetailLoading(false);
      }
    }
  }, [
    acceptIngestionFreshness,
    bumpIngestionJobVersion,
    nextIngestionGeneration
  ]);

  const launchIngestionDetailRefresh = useCallback(function launch(
    jobId: string,
    coordinator: IngestionDetailRefreshCoordinator
  ): Promise<RefreshResult<IngestionJobDetail>> {
    const generation = ++coordinator.generation;
    const request = performIngestionDetailRefresh(jobId);
    coordinator.inFlight = request;
    void request.then((result) => {
      if (
        coordinator.cancelled ||
        ingestionDetailRefreshesRef.current.get(jobId) !== coordinator
      ) return;
      coordinator.inFlight = null;
      const remainingWaiters = [] as IngestionDetailRefreshCoordinator["waiters"];
      for (const waiter of coordinator.waiters) {
        if (waiter.generation <= generation) waiter.resolve(result);
        else remainingWaiters.push(waiter);
      }
      coordinator.waiters = remainingWaiters;
      if (!coordinator.trailing) return;
      coordinator.trailing = false;
      void launch(jobId, coordinator);
    });
    return request;
  }, [performIngestionDetailRefresh]);

  const requestIngestionDetailRefresh = useCallback((
    jobId: string,
    mode: IngestionDetailRefreshMode = "manual"
  ): Promise<RefreshResult<IngestionJobDetail>> => {
    let coordinator = ingestionDetailRefreshesRef.current.get(jobId);
    if (!coordinator || coordinator.cancelled) {
      coordinator = {
        inFlight: null,
        trailing: false,
        generation: 0,
        waiters: [],
        cancelled: false
      };
      ingestionDetailRefreshesRef.current.set(jobId, coordinator);
    }
    if (!coordinator.inFlight) {
      if (mode === "automatic") {
        return launchIngestionDetailRefresh(jobId, coordinator);
      }
      const generation = coordinator.generation + 1;
      const result = new Promise<RefreshResult<IngestionJobDetail>>((resolve) => {
        coordinator?.waiters.push({ generation, resolve });
      });
      launchIngestionDetailRefresh(jobId, coordinator);
      return result;
    }
    coordinator.trailing = true;
    if (mode === "automatic") return coordinator.inFlight;
    const generation = coordinator.generation + 1;
    return new Promise((resolve) => {
      coordinator?.waiters.push({ generation, resolve });
    });
  }, [launchIngestionDetailRefresh]);

  const cancelIngestionDetailRefresh = useCallback((jobId: string) => {
    const coordinator = ingestionDetailRefreshesRef.current.get(jobId);
    if (!coordinator) return;
    coordinator.cancelled = true;
    coordinator.trailing = false;
    const waiters = coordinator.waiters.splice(0);
    waiters.forEach(({ resolve }) => resolve({ status: "stale" }));
    ingestionDetailRefreshesRef.current.delete(jobId);
  }, []);

  useEffect(() => {
    if (workspaceLocation.view === "ingestion") void refetchIngestionJobs();
  }, [refetchIngestionJobs, workspaceLocation.view]);

  useEffect(() => {
    if (!selectedIngestionJobId) {
      ingestionDetailRequestRef.current += 1;
      setIngestionDetail(null);
      setIngestionDetailLoading(false);
      setIngestionDetailError("");
      return;
    }
    void requestIngestionDetailRefresh(selectedIngestionJobId, "manual");
    return () => cancelIngestionDetailRefresh(selectedIngestionJobId);
  }, [cancelIngestionDetailRefresh, requestIngestionDetailRefresh, selectedIngestionJobId]);

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
    const accepted = applyOptimisticIngestionJobs(
      detail.job_id,
      detail.updated_at,
      (current) => upsertIngestionJob(current, detail)
    );
    if (!accepted) return;
    setIngestionDetail(detail);
    selectIngestionJob(detail.job_id);
  }

  function beginIngestionAction(
    jobId: string,
    kind: IngestionActionKind
  ): number | null {
    if (ingestionPendingJobsRef.current.has(jobId)) return null;
    const token = ++ingestionActionTokenRef.current;
    nextIngestionGeneration();
    ingestionActionTokensRef.current.set(jobId, token);
    ingestionPendingJobsRef.current.add(jobId);
    setIngestionActions((current) => ({
      ...current,
      [jobId]: { jobId, kind, token, pending: true, error: "" }
    }));
    return token;
  }

  function finishIngestionAction(
    jobId: string,
    token: number,
    error: string
  ) {
    if (ingestionActionTokensRef.current.get(jobId) !== token) return;
    ingestionActionTokensRef.current.delete(jobId);
    ingestionPendingJobsRef.current.delete(jobId);
    setIngestionActions((current) => {
      const action = current[jobId];
      if (!action || action.token !== token) return current;
      if (error) {
        return { ...current, [jobId]: { ...action, pending: false, error } };
      }
      const next = { ...current };
      delete next[jobId];
      return next;
    });
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
      const accepted = applyOptimisticIngestionJobs(
        jobId,
        ingestionJobsRef.current.find((job) => job.job_id === jobId)?.updated_at ?? null,
        (current) => current.map((job) =>
          job.job_id === jobId ? { ...job, status: "queued" } : job
        )
      );
      if (accepted && selectedIngestionJobIdRef.current === jobId) {
        setIngestionDetail((current) =>
          current?.job_id === jobId ? { ...current, status: "queued" } : current
        );
      }
    }

    const listPromise = refetchIngestionJobs();
    const detailPromise: Promise<RefreshResult<IngestionJobDetail>> =
      actionError && selectedIngestionJobIdRef.current === jobId
        ? requestIngestionDetailRefresh(jobId, "manual")
        : Promise.resolve({ status: "stale" });
    const [listResult] = await Promise.all([listPromise, detailPromise]);
    if (
      ingestionActionTokensRef.current.get(jobId) === token &&
      actionError &&
      listResult.status === "success"
    ) {
      const summary = listResult.value.find((job) => job.job_id === jobId);
      if (summary) {
        applyIngestionSummaryCandidate(summary, listResult.requestGeneration);
      }
    }
    if (appMountedRef.current) finishIngestionAction(jobId, token, actionError);
  }

  const handleIngestionProgress = useCallback(
    (progress: IngestionJobProgress) => {
      const accepted = applyOptimisticIngestionJobs(
        progress.job_id,
        progress.updated_at,
        (current) => current.map((job) =>
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
      if (!accepted) return;
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
    },
    [applyOptimisticIngestionJobs]
  );

  function clearTombstonedIngestionJobSelection(jobId: string): boolean {
    const currentLocation = parseWorkspaceLocation(window.location.search);
    const wasSelected =
      currentLocation.view === "ingestion" && currentLocation.jobId === jobId;
    if (wasSelected) {
      cancelIngestionDetailRefresh(jobId);
      ingestionDetailRequestRef.current += 1;
      setIngestionDetail(null);
      setIngestionDetailError("");
      navigateWorkspaceLocation({ view: "ingestion" }, { replace: true });
    }
    return wasSelected;
  }

  function commitDeletedIngestion(
    jobId: string,
    requestGeneration = nextIngestionGeneration()
  ): { accepted: boolean; wasSelected: boolean } {
    const alreadyTombstoned = ingestionFreshnessRef.current.get(jobId)?.tombstoned === true;
    const accepted =
      alreadyTombstoned ||
      acceptIngestionFreshness(jobId, null, requestGeneration, true);
    if (!accepted) return { accepted: false, wasSelected: false };
    if (!alreadyTombstoned) bumpIngestionJobVersion(jobId);
    const next = ingestionJobsRef.current.filter((job) => job.job_id !== jobId);
    ingestionJobsRef.current = next;
    setIngestionJobs(next);
    setIngestionJobsLoaded(true);
    return {
      accepted: true,
      wasSelected: clearTombstonedIngestionJobSelection(jobId)
    };
  }

  async function reconcileDeletedIngestion(wasSelected: boolean) {
    const result = await refetchIngestionJobs();
    if (result.status !== "success" || !wasSelected || !appMountedRef.current) return;
    navigateToIngestionFallback(ingestionJobsRef.current);
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
      if (
        !appMountedRef.current ||
        ingestionActionTokensRef.current.get(jobId) !== token
      ) return false;
      const { wasSelected } = commitDeletedIngestion(jobId);
      finishIngestionAction(jobId, token, "");
      void reconcileDeletedIngestion(wasSelected);
      return true;
    } catch (error) {
      const actionError = apiErrorMessage(error, "删除任务失败，请稍后重试");
      if (
        !appMountedRef.current ||
        ingestionActionTokensRef.current.get(jobId) !== token
      ) return false;
      const jobsPromise = refetchIngestionJobs();
      const detailPromise: Promise<RefreshResult<IngestionJobDetail>> =
        selectedIngestionJobIdRef.current === jobId
          ? requestIngestionDetailRefresh(jobId, "manual")
          : Promise.resolve({ status: "stale" });
      const [jobsResult, detailResult] = await Promise.all([jobsPromise, detailPromise]);
      if (
        !appMountedRef.current ||
        ingestionActionTokensRef.current.get(jobId) !== token
      ) return false;
      if (ingestionFreshnessRef.current.get(jobId)?.tombstoned) {
        finishIngestionAction(jobId, token, "");
        return true;
      }
      if (
        jobsResult.status === "success" &&
        !jobsResult.value.some((job) => job.job_id === jobId)
      ) {
        const { accepted, wasSelected } = commitDeletedIngestion(
          jobId,
          jobsResult.requestGeneration
        );
        if (!accepted) {
          finishIngestionAction(jobId, token, actionError);
          return false;
        }
        finishIngestionAction(jobId, token, "");
        if (wasSelected) navigateToIngestionFallback(jobsResult.value);
        return true;
      }
      const summary =
        jobsResult.status === "success"
          ? jobsResult.value.find((job) => job.job_id === jobId)
          : undefined;
      const summaryAccepted = summary && jobsResult.status === "success"
        ? applyIngestionSummaryCandidate(summary, jobsResult.requestGeneration)
        : false;
      const reconciledStatus =
        detailResult.status === "success"
          ? detailResult.value.status
          : summaryAccepted
            ? summary?.status
            : undefined;
      if (reconciledStatus === "deleting") {
        finishIngestionAction(jobId, token, actionError);
        return true;
      }
      finishIngestionAction(jobId, token, actionError);
      return false;
    }
  }

  const selectedIngestionAction =
    ingestionDetail ? ingestionActions[ingestionDetail.job_id] ?? null : null;

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
                onRefresh={() => {
                  void requestIngestionDetailRefresh(ingestionDetail.job_id, "automatic");
                }}
              />
            ) : (
              <div className="ingestion-main-state" role={ingestionDetailError ? "alert" : "status"}>
                <span>{ingestionDetailError || "正在加载任务详情…"}</span>
                {ingestionDetailError && (
                  <button
                    className="ghost-button"
                    type="button"
                    onClick={() => {
                      if (workspaceLocation.jobId) {
                        void requestIngestionDetailRefresh(workspaceLocation.jobId, "manual");
                      }
                    }}
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
          onReload={() => {
            if (workspaceLocation.jobId) {
              void requestIngestionDetailRefresh(workspaceLocation.jobId, "manual");
            }
          }}
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
  return upsertIngestionSummary(jobs, summaryFromIngestionDetail(detail));
}

function summaryFromIngestionDetail(
  detail: IngestionJobDetail
): IngestionJobSummary {
  const {
    ignored_entries: _ignoredEntries,
    items: _items,
    attempt: _attempt,
    events: _events,
    ...summary
  } = detail;
  return summary;
}

function upsertIngestionSummary(
  jobs: IngestionJobSummary[],
  summary: IngestionJobSummary
): IngestionJobSummary[] {
  return [summary, ...jobs.filter((job) => job.job_id !== summary.job_id)];
}

function mergeSummaryIntoDetail(
  detail: IngestionJobDetail,
  summary: IngestionJobSummary
): IngestionJobDetail {
  return {
    ...detail,
    ...summary,
    ignored_entries: detail.ignored_entries,
    items: detail.items,
    attempt: detail.attempt,
    events: detail.events
  };
}

function isAcceptedIngestionFreshness(
  candidate: IngestionJobFreshness,
  current: IngestionJobFreshness | undefined
): boolean {
  if (!current) return true;
  if (current.tombstoned) {
    return candidate.tombstoned &&
      candidate.requestGeneration >= current.requestGeneration;
  }
  const candidateTime = validIsoTimestamp(candidate.updatedAt);
  const currentTime = validIsoTimestamp(current.updatedAt);
  if (candidateTime !== null && currentTime !== null) {
    if (candidateTime !== currentTime) return candidateTime > currentTime;
  }
  return candidate.requestGeneration >= current.requestGeneration;
}

function validIsoTimestamp(value: string | null): number | null {
  if (!value || !/^\d{4}-\d{2}-\d{2}T/.test(value)) return null;
  const timestamp = Date.parse(value);
  return Number.isNaN(timestamp) ? null : timestamp;
}
