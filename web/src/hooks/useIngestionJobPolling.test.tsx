import { act, renderHook } from "@testing-library/react";
import { StrictMode, type PropsWithChildren } from "react";

import { ApiError } from "../api";
import type { IngestionJobProgress } from "../types";
import { useIngestionJobPolling } from "./useIngestionJobPolling";

function progressPayload(
  overrides: Partial<IngestionJobProgress> = {}
): IngestionJobProgress {
  return {
    job_id: "job-1",
    status: "running",
    current_stage: "parsing",
    attempt_no: 1,
    item_total: 2,
    item_done: 0,
    document_total: 2,
    chunk_total: 0,
    warning_count: 0,
    active_item_index: 1,
    message: "正在解析文档",
    updated_at: "2026-07-12T08:01:00+00:00",
    ...overrides
  };
}

function deferred<T>() {
  let resolvePromise: (value: T) => void = () => undefined;
  let rejectPromise: (reason: unknown) => void = () => undefined;
  const promise = new Promise<T>((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  return { promise, resolve: resolvePromise, reject: rejectPromise };
}

async function flushMicrotasks() {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
});

test("jobId null does not request progress", async () => {
  const fetchProgress = vi.fn(async () => progressPayload());

  const { result } = renderHook(() =>
    useIngestionJobPolling(null, { intervalMs: 20, fetchProgress })
  );
  await flushMicrotasks();

  expect(fetchProgress).not.toHaveBeenCalled();
  expect(result.current.progress).toBeNull();
  expect(result.current.error).toBeNull();
  expect(result.current.isLoading).toBe(false);
});

test("polls active job immediately and stops after terminal status", async () => {
  const fetchProgress = vi
    .fn<(jobId: string) => Promise<IngestionJobProgress>>()
    .mockResolvedValueOnce(progressPayload({ status: "running" }))
    .mockResolvedValueOnce(
      progressPayload({
        status: "succeeded",
        current_stage: "completed",
        active_item_index: null,
        message: "任务已完成"
      })
    );
  const { result } = renderHook(() =>
    useIngestionJobPolling("job-1", { intervalMs: 20, fetchProgress })
  );

  await flushMicrotasks();
  expect(result.current.progress?.status).toBe("running");
  expect(fetchProgress).toHaveBeenCalledTimes(1);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  expect(result.current.progress?.status).toBe("succeeded");
  expect(fetchProgress).toHaveBeenCalledTimes(2);

  await act(async () => {
    await vi.advanceTimersByTimeAsync(100);
  });
  expect(fetchProgress).toHaveBeenCalledTimes(2);
});

test("polling is serial and never overlaps a pending request", async () => {
  const first = deferred<IngestionJobProgress>();
  const fetchProgress = vi
    .fn<(jobId: string) => Promise<IngestionJobProgress>>()
    .mockReturnValueOnce(first.promise)
    .mockResolvedValueOnce(
      progressPayload({ status: "succeeded", current_stage: "completed" })
    );
  renderHook(() =>
    useIngestionJobPolling("job-1", { intervalMs: 20, fetchProgress })
  );

  await flushMicrotasks();
  expect(fetchProgress).toHaveBeenCalledTimes(1);
  await act(async () => {
    await vi.advanceTimersByTimeAsync(200);
  });
  expect(fetchProgress).toHaveBeenCalledTimes(1);

  await act(async () => {
    first.resolve(progressPayload({ status: "running" }));
    await first.promise;
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  expect(fetchProgress).toHaveBeenCalledTimes(2);
});

test("temporary errors remain visible and polling continues", async () => {
  const notFound = new ApiError(404, "入库任务不存在", {
    detail: "入库任务不存在"
  });
  const fetchProgress = vi
    .fn<(jobId: string) => Promise<IngestionJobProgress>>()
    .mockRejectedValueOnce(notFound)
    .mockResolvedValueOnce(
      progressPayload({ status: "succeeded", current_stage: "completed" })
    );
  const { result } = renderHook(() =>
    useIngestionJobPolling("job-1", { intervalMs: 20, fetchProgress })
  );

  await flushMicrotasks();
  expect(result.current.error).toBe(notFound);
  expect(result.current.error).toMatchObject({ status: 404 });

  await act(async () => {
    await vi.advanceTimersByTimeAsync(20);
  });
  expect(fetchProgress).toHaveBeenCalledTimes(2);
  expect(result.current.progress?.status).toBe("succeeded");
  expect(result.current.error).toBeNull();
});

test("job switching ignores stale results from the previous job", async () => {
  const first = deferred<IngestionJobProgress>();
  const second = deferred<IngestionJobProgress>();
  const fetchProgress = vi.fn((jobId: string) =>
    jobId === "job-1" ? first.promise : second.promise
  );
  const { result, rerender } = renderHook(
    ({ jobId }: { jobId: string | null }) =>
      useIngestionJobPolling(jobId, { intervalMs: 20, fetchProgress }),
    { initialProps: { jobId: "job-1" } }
  );
  await flushMicrotasks();

  rerender({ jobId: "job-2" });
  await flushMicrotasks();
  await act(async () => {
    second.resolve(
      progressPayload({
        job_id: "job-2",
        status: "succeeded",
        current_stage: "completed"
      })
    );
    await second.promise;
  });
  expect(result.current.progress?.job_id).toBe("job-2");

  await act(async () => {
    first.resolve(progressPayload({ job_id: "job-1", status: "running" }));
    await first.promise;
  });
  expect(result.current.progress?.job_id).toBe("job-2");
});

test("unmount clears the next poll timer", async () => {
  const fetchProgress = vi.fn(async () => progressPayload({ status: "running" }));
  const { unmount } = renderHook(() =>
    useIngestionJobPolling("job-1", { intervalMs: 20, fetchProgress })
  );
  await flushMicrotasks();
  expect(fetchProgress).toHaveBeenCalledTimes(1);

  unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100);
  });
  expect(fetchProgress).toHaveBeenCalledTimes(1);
});

test("StrictMode does not duplicate the immediate request or leak timers", async () => {
  const fetchProgress = vi.fn(async () => progressPayload({ status: "running" }));
  const wrapper = ({ children }: PropsWithChildren) => (
    <StrictMode>{children}</StrictMode>
  );
  const { unmount } = renderHook(
    () => useIngestionJobPolling("job-1", { intervalMs: 20, fetchProgress }),
    { wrapper }
  );

  await flushMicrotasks();
  expect(fetchProgress).toHaveBeenCalledTimes(1);
  unmount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(100);
  });
  expect(fetchProgress).toHaveBeenCalledTimes(1);
});
