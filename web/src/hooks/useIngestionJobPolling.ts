import { useEffect, useRef, useState } from "react";

import { getIngestionJobProgress } from "../api";
import { isIngestionJobActive } from "../ingestion";
import type { IngestionJobProgress } from "../types";

const DEFAULT_POLL_INTERVAL_MS = 2000;

type UseIngestionJobPollingOptions = {
  intervalMs?: number;
  fetchProgress?: (jobId: string) => Promise<IngestionJobProgress>;
};

type UseIngestionJobPollingResult = {
  progress: IngestionJobProgress | null;
  error: Error | null;
  isLoading: boolean;
};

export function useIngestionJobPolling(
  jobId: string | null,
  options: UseIngestionJobPollingOptions = {}
): UseIngestionJobPollingResult {
  const intervalMs = normalizedInterval(options.intervalMs);
  const fetchProgressRef = useRef(options.fetchProgress);
  fetchProgressRef.current = options.fetchProgress;

  const [progress, setProgress] = useState<IngestionJobProgress | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    setProgress(null);
    setError(null);
    setIsLoading(jobId !== null);

    if (jobId === null) {
      return () => {
        cancelled = true;
      };
    }

    const schedule = () => {
      timer = setTimeout(() => {
        void tick();
      }, intervalMs);
    };

    const tick = async () => {
      if (cancelled) {
        return;
      }
      setIsLoading(true);
      const fetchProgress =
        fetchProgressRef.current ??
        ((selectedJobId: string) => getIngestionJobProgress(selectedJobId));
      try {
        const nextProgress = await fetchProgress(jobId);
        if (cancelled) {
          return;
        }
        setProgress(nextProgress);
        setError(null);
        setIsLoading(false);
        if (isIngestionJobActive(nextProgress.status)) {
          schedule();
        }
      } catch (caught) {
        if (cancelled) {
          return;
        }
        setError(
          caught instanceof Error
            ? caught
            : new Error("无法加载入库任务进度")
        );
        setIsLoading(false);
        schedule();
      }
    };

    // 推迟到当前 effect 周期结束：既保持挂载后立即请求，也避免 StrictMode
    // 的预检查 effect 发出一条随后即过期的重复请求。
    void Promise.resolve().then(tick);

    return () => {
      cancelled = true;
      if (timer !== undefined) {
        clearTimeout(timer);
      }
    };
  }, [jobId, intervalMs]);

  return { progress, error, isLoading };
}

function normalizedInterval(value: number | undefined): number {
  if (value === undefined || !Number.isFinite(value) || value <= 0) {
    return DEFAULT_POLL_INTERVAL_MS;
  }
  return value;
}
