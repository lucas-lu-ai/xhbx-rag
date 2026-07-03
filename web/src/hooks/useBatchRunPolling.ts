import { useCallback, useEffect, useRef, useState } from "react";

import { getBatchRunDetail, getBatchRunProgress } from "../api";
import { batchRunProgressSignature, isBatchRunActive } from "../batchRuns";
import type { BatchRunDetail, BatchRunProgress } from "../types";

const DEFAULT_POLL_INTERVAL_MS = 2000;
const MAX_CONSECUTIVE_POLL_FAILURES = 5;

export const POLL_RETRY_MESSAGE = "进度刷新失败，正在自动重试...";
export const POLL_STOPPED_MESSAGE = "进度刷新连续失败，已停止自动刷新。";

type UseBatchRunPollingOptions = {
  intervalMs?: number;
  fetchProgress?: (runId: string) => Promise<BatchRunProgress>;
  fetchDetail?: (runId: string) => Promise<BatchRunDetail>;
};

type UseBatchRunPollingResult = {
  detail: BatchRunDetail | null;
  loadError: string;
  pollError: string;
  refresh: () => void;
  patchDetail: (updater: (detail: BatchRunDetail) => BatchRunDetail) => void;
};

// 递归 setTimeout 轮询：progress 签名变化才拉详情；到终态再拉一次并停止。
export function useBatchRunPolling(
  runId: string,
  options: UseBatchRunPollingOptions = {}
): UseBatchRunPollingResult {
  const intervalMs = options.intervalMs ?? DEFAULT_POLL_INTERVAL_MS;
  const fetchersRef = useRef({
    fetchProgress: options.fetchProgress,
    fetchDetail: options.fetchDetail
  });
  fetchersRef.current = {
    fetchProgress: options.fetchProgress,
    fetchDetail: options.fetchDetail
  };

  const [detail, setDetail] = useState<BatchRunDetail | null>(null);
  const [loadError, setLoadError] = useState("");
  const [pollError, setPollError] = useState("");
  const generationRef = useRef(0);
  const timerRef = useRef<ReturnType<typeof setTimeout> | undefined>(undefined);
  const lastSignatureRef = useRef("");
  const failureCountRef = useRef(0);

  const start = useCallback(() => {
    generationRef.current += 1;
    const generation = generationRef.current;
    if (timerRef.current !== undefined) {
      clearTimeout(timerRef.current);
    }
    failureCountRef.current = 0;
    setPollError("");

    const isCurrent = () => generationRef.current === generation;
    const loadProgress =
      fetchersRef.current.fetchProgress ??
      ((id: string) => getBatchRunProgress(id));
    const loadDetail =
      fetchersRef.current.fetchDetail ?? ((id: string) => getBatchRunDetail(id));

    const schedule = () => {
      timerRef.current = setTimeout(() => {
        void tick();
      }, intervalMs);
    };

    async function tick() {
      try {
        const progress = await loadProgress(runId);
        if (!isCurrent()) {
          return;
        }
        failureCountRef.current = 0;
        setPollError("");
        const signature = batchRunProgressSignature(progress);
        const changed = signature !== lastSignatureRef.current;
        lastSignatureRef.current = signature;
        const terminal = !isBatchRunActive(progress.status);
        if (changed || terminal) {
          const nextDetail = await loadDetail(runId);
          if (!isCurrent()) {
            return;
          }
          setDetail(nextDetail);
        }
        if (!terminal) {
          schedule();
        }
      } catch {
        if (!isCurrent()) {
          return;
        }
        failureCountRef.current += 1;
        if (failureCountRef.current >= MAX_CONSECUTIVE_POLL_FAILURES) {
          setPollError(POLL_STOPPED_MESSAGE);
          return;
        }
        setPollError(POLL_RETRY_MESSAGE);
        schedule();
      }
    }

    async function loadInitialDetail() {
      try {
        const nextDetail = await loadDetail(runId);
        if (!isCurrent()) {
          return;
        }
        setDetail(nextDetail);
        setLoadError("");
        lastSignatureRef.current = batchRunProgressSignature(nextDetail);
        if (isBatchRunActive(nextDetail.status)) {
          schedule();
        }
      } catch (error) {
        if (!isCurrent()) {
          return;
        }
        setLoadError(
          error instanceof Error ? error.message : "无法加载批量会话详情"
        );
      }
    }

    void loadInitialDetail();
  }, [runId, intervalMs]);

  useEffect(() => {
    setDetail(null);
    setLoadError("");
    setPollError("");
    lastSignatureRef.current = "";
    start();
    return () => {
      // 使进行中的请求过期并停止后续轮询，丢弃迟到响应。
      generationRef.current += 1;
      if (timerRef.current !== undefined) {
        clearTimeout(timerRef.current);
      }
    };
  }, [start]);

  const refresh = useCallback(() => {
    start();
  }, [start]);

  const patchDetail = useCallback(
    (updater: (current: BatchRunDetail) => BatchRunDetail) => {
      setDetail((current) => (current ? updater(current) : current));
    },
    []
  );

  return { detail, loadError, pollError, refresh, patchDetail };
}
