import { act, renderHook, waitFor } from "@testing-library/react";

import {
  batchRunDetail,
  batchRunProgressOf,
  batchRunQuestionDetail
} from "../test-utils";
import type { BatchRunDetail, BatchRunProgress } from "../types";
import {
  POLL_STOPPED_MESSAGE,
  useBatchRunPolling
} from "./useBatchRunPolling";

const pendingDetail = batchRunDetail({
  status: "pending",
  question_done: 0,
  questions: [batchRunQuestionDetail({ status: "pending", response: null })]
});

const runningDetail = batchRunDetail({
  status: "running",
  question_done: 0,
  questions: [batchRunQuestionDetail({ status: "running", response: null })]
});

const completedDetail = batchRunDetail({
  status: "completed",
  question_done: 1,
  questions: [batchRunQuestionDetail({ status: "succeeded" })]
});

function queueFetcher<T>(values: T[]): () => Promise<T> {
  let index = 0;
  return async () => {
    const value = values[Math.min(index, values.length - 1)];
    index += 1;
    return value;
  };
}

test("终态 run 只拉一次详情，不再轮询进度", async () => {
  const fetchDetail = vi.fn(async () => completedDetail);
  const fetchProgress = vi.fn(async () => batchRunProgressOf(completedDetail));

  const { result } = renderHook(() =>
    useBatchRunPolling("run-1", { intervalMs: 0, fetchDetail, fetchProgress })
  );

  await waitFor(() => {
    expect(result.current.detail?.status).toBe("completed");
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });

  expect(fetchDetail).toHaveBeenCalledTimes(1);
  expect(fetchProgress).not.toHaveBeenCalled();
});

test("进度签名变化才拉详情，到终态后停止轮询", async () => {
  const detailQueue: BatchRunDetail[] = [
    pendingDetail,
    runningDetail,
    completedDetail
  ];
  const progressQueue: BatchRunProgress[] = [
    batchRunProgressOf(runningDetail),
    batchRunProgressOf(runningDetail),
    batchRunProgressOf(completedDetail)
  ];
  const fetchDetail = vi.fn(queueFetcher(detailQueue));
  const fetchProgress = vi.fn(queueFetcher(progressQueue));

  const { result } = renderHook(() =>
    useBatchRunPolling("run-1", { intervalMs: 0, fetchDetail, fetchProgress })
  );

  await waitFor(() => {
    expect(result.current.detail?.status).toBe("completed");
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });

  // 初始详情 + 第一次进度变化 + 终态各拉一次；第二次进度签名相同不拉详情。
  expect(fetchDetail).toHaveBeenCalledTimes(3);
  expect(fetchProgress).toHaveBeenCalledTimes(3);
});

test("轮询连续失败 5 次后停止并给出提示", async () => {
  const fetchDetail = vi.fn(async () => pendingDetail);
  const fetchProgress = vi.fn(async () => {
    throw new Error("网络错误");
  });

  const { result } = renderHook(() =>
    useBatchRunPolling("run-1", { intervalMs: 0, fetchDetail, fetchProgress })
  );

  await waitFor(() => {
    expect(result.current.pollError).toBe(POLL_STOPPED_MESSAGE);
  });
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 20));
  });

  expect(fetchProgress).toHaveBeenCalledTimes(5);
});

test("refresh 立即重拉详情并重启轮询", async () => {
  const fetchDetail = vi
    .fn<() => Promise<BatchRunDetail>>()
    .mockResolvedValueOnce(completedDetail)
    .mockResolvedValue({
      ...completedDetail,
      questions: [
        batchRunQuestionDetail({
          status: "succeeded",
          response: { answer: "重试后的答案", citations: [], evidence_count: 0 }
        })
      ]
    });
  const fetchProgress = vi.fn(async () => batchRunProgressOf(completedDetail));

  const { result } = renderHook(() =>
    useBatchRunPolling("run-1", { intervalMs: 0, fetchDetail, fetchProgress })
  );

  await waitFor(() => {
    expect(result.current.detail?.status).toBe("completed");
  });

  act(() => {
    result.current.refresh();
  });

  await waitFor(() => {
    expect(result.current.detail?.questions[0]?.response?.answer).toBe(
      "重试后的答案"
    );
  });
  expect(fetchDetail).toHaveBeenCalledTimes(2);
});

test("patchDetail 本地更新详情（用于行级反馈回写）", async () => {
  const fetchDetail = vi.fn(async () => completedDetail);
  const fetchProgress = vi.fn(async () => batchRunProgressOf(completedDetail));

  const { result } = renderHook(() =>
    useBatchRunPolling("run-1", { intervalMs: 0, fetchDetail, fetchProgress })
  );

  await waitFor(() => {
    expect(result.current.detail?.status).toBe("completed");
  });

  act(() => {
    result.current.patchDetail((detail) => ({
      ...detail,
      questions: detail.questions.map((question) => ({
        ...question,
        bad_case: { bad_case_id: "bad-case-1" }
      }))
    }));
  });

  expect(result.current.detail?.questions[0]?.bad_case).toEqual({
    bad_case_id: "bad-case-1"
  });
});
