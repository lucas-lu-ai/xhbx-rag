import type { IngestionJobDetail, IngestionTarget } from "./types";

export const INGESTION_UPLOAD_ABORTED_ERROR = "上传已取消";
export const INGESTION_UPLOAD_INVALID_RESPONSE_ERROR = "上传响应无效";
export const INGESTION_UPLOAD_NETWORK_ERROR = "上传失败，请检查网络连接";
export const INGESTION_UPLOAD_TIMEOUT_ERROR = "上传超时，请重试";

type UploadEventSource = {
  addEventListener(type: string, listener: EventListener): void;
  removeEventListener(type: string, listener: EventListener): void;
};

type IngestionUploadRequest = UploadEventSource & {
  readonly upload: UploadEventSource;
  readonly status: number;
  readonly responseText: string;
  timeout: number;
  open(method: string, url: string, async?: boolean): void;
  send(body?: Document | XMLHttpRequestBodyInit | null): void;
  abort(): void;
};

type UploadIngestionOptions = {
  baseUrl?: string;
  onProgress?: (progress: number) => void;
  signal?: AbortSignal;
  timeoutMs?: number;
  xhrFactory?: () => IngestionUploadRequest;
};

export function uploadIngestionJob(
  file: File,
  target: IngestionTarget,
  options: UploadIngestionOptions = {}
): Promise<IngestionJobDetail> {
  if (options.signal?.aborted) {
    return Promise.reject(new Error(INGESTION_UPLOAD_ABORTED_ERROR));
  }

  const xhrFactory = options.xhrFactory ?? (() => new XMLHttpRequest());
  const xhr = xhrFactory();
  const formData = new FormData();
  formData.append("file", file);
  formData.append("target", target);

  return new Promise<IngestionJobDetail>((resolve, reject) => {
    let settled = false;

    const cleanup = () => {
      xhr.removeEventListener("load", handleLoad);
      xhr.removeEventListener("error", handleNetworkError);
      xhr.removeEventListener("timeout", handleTimeout);
      xhr.removeEventListener("abort", handleXhrAbort);
      xhr.upload.removeEventListener("progress", handleProgress);
      options.signal?.removeEventListener("abort", handleSignalAbort);
    };

    const settle = (callback: () => void) => {
      if (settled) {
        return;
      }
      settled = true;
      cleanup();
      callback();
    };

    function succeed(value: IngestionJobDetail) {
      settle(() => resolve(value));
    }

    function fail(message: string) {
      settle(() => reject(new Error(message)));
    }

    function handleLoad() {
      const status = safeStatus(xhr);
      if (status === null) {
        fail(INGESTION_UPLOAD_INVALID_RESPONSE_ERROR);
        return;
      }
      if (status === 0) {
        fail(INGESTION_UPLOAD_NETWORK_ERROR);
        return;
      }
      const responseText = safeResponseText(xhr);
      if (status < 200 || status >= 300) {
        fail(
          responseText === null
            ? `上传失败 (${status})`
            : httpErrorMessage(responseText, status)
        );
        return;
      }
      if (responseText === null) {
        fail(INGESTION_UPLOAD_INVALID_RESPONSE_ERROR);
        return;
      }

      try {
        const payload = JSON.parse(responseText) as unknown;
        if (isIngestionJobDetail(payload)) {
          succeed(payload);
        } else {
          fail(INGESTION_UPLOAD_INVALID_RESPONSE_ERROR);
        }
      } catch {
        fail(INGESTION_UPLOAD_INVALID_RESPONSE_ERROR);
      }
    }

    function handleNetworkError() {
      fail(INGESTION_UPLOAD_NETWORK_ERROR);
    }

    function handleTimeout() {
      fail(INGESTION_UPLOAD_TIMEOUT_ERROR);
    }

    function handleXhrAbort() {
      fail(INGESTION_UPLOAD_ABORTED_ERROR);
    }

    function handleSignalAbort() {
      if (settled) {
        return;
      }
      xhr.abort();
      fail(INGESTION_UPLOAD_ABORTED_ERROR);
    }

    function handleProgress(event: Event) {
      if (!(event instanceof ProgressEvent) || !event.lengthComputable || event.total <= 0) {
        return;
      }
      const percent = Math.round((event.loaded / event.total) * 100);
      options.onProgress?.(Math.min(100, Math.max(0, percent)));
    }

    xhr.addEventListener("load", handleLoad);
    xhr.addEventListener("error", handleNetworkError);
    xhr.addEventListener("timeout", handleTimeout);
    xhr.addEventListener("abort", handleXhrAbort);
    xhr.upload.addEventListener("progress", handleProgress);
    options.signal?.addEventListener("abort", handleSignalAbort, { once: true });

    // AbortSignal 不会重放已发生的 abort；挂 listener 后必须再检查一次，
    // 关闭初检与 listener 注册之间的同步竞态窗口。
    if (options.signal?.aborted) {
      handleSignalAbort();
      return;
    }

    try {
      xhr.open("POST", ingestionUploadEndpoint(options.baseUrl), true);
      if (options.timeoutMs !== undefined) {
        xhr.timeout = Math.max(0, options.timeoutMs);
      }
      xhr.send(formData);
    } catch {
      fail(INGESTION_UPLOAD_NETWORK_ERROR);
    }
  });
}

function ingestionUploadEndpoint(baseUrl?: string): string {
  const configuredBase =
    baseUrl ?? (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
  return `${configuredBase.replace(/\/$/, "")}/api/ingestion-jobs`;
}

function safeStatus(request: IngestionUploadRequest): number | null {
  try {
    return request.status;
  } catch {
    return null;
  }
}

function safeResponseText(request: IngestionUploadRequest): string | null {
  try {
    return request.responseText;
  } catch {
    return null;
  }
}

function httpErrorMessage(responseText: string, status: number): string {
  try {
    const body = JSON.parse(responseText) as unknown;
    if (isObject(body) && typeof body.detail === "string") {
      return body.detail;
    }
  } catch {
    // 非 JSON 错误响应只展示固定状态提示，绝不回显原始正文。
  }
  return `上传失败 (${status})`;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isIngestionJobDetail(value: unknown): value is IngestionJobDetail {
  return (
    isObject(value) &&
    typeof value.job_id === "string" &&
    typeof value.source_name === "string" &&
    isSourceKind(value.source_kind) &&
    isTarget(value.target) &&
    isJobStatus(value.status) &&
    isStage(value.current_stage) &&
    isNumber(value.attempt_count) &&
    isNumber(value.item_total) &&
    isNumber(value.item_done) &&
    isNumber(value.document_total) &&
    isNumber(value.chunk_total) &&
    isNumber(value.ignored_total) &&
    isNumber(value.warning_count) &&
    isNullableString(value.error_code) &&
    isNullableString(value.error_detail) &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string" &&
    isNullableString(value.started_at) &&
    isNullableString(value.finished_at) &&
    Array.isArray(value.ignored_entries) &&
    value.ignored_entries.every(isString) &&
    Array.isArray(value.items) &&
    value.items.every(isIngestionItem) &&
    (value.attempt === null || isIngestionAttempt(value.attempt)) &&
    Array.isArray(value.events) &&
    value.events.every(isIngestionEvent)
  );
}

function isIngestionItem(value: unknown): boolean {
  return (
    isObject(value) &&
    isNumber(value.item_index) &&
    typeof value.unit_key === "string" &&
    typeof value.display_name === "string" &&
    Array.isArray(value.relative_paths) &&
    value.relative_paths.every(isString) &&
    isNumber(value.document_count) &&
    isItemStatus(value.status) &&
    isStage(value.current_stage) &&
    isNumber(value.chunk_count) &&
    isNumber(value.warning_count) &&
    isNullableString(value.error_detail) &&
    typeof value.updated_at === "string"
  );
}

function isIngestionAttempt(value: unknown): boolean {
  return (
    isObject(value) &&
    isNumber(value.attempt_no) &&
    isAttemptStatus(value.status) &&
    isStage(value.current_stage) &&
    isCommitState(value.commit_state) &&
    isNullableString(value.error_code) &&
    isNullableString(value.error_detail) &&
    isNullableString(value.started_at) &&
    isNullableString(value.finished_at)
  );
}

function isIngestionEvent(value: unknown): boolean {
  return (
    isObject(value) &&
    isNumber(value.attempt_no) &&
    isNumber(value.sequence) &&
    typeof value.event_type === "string" &&
    typeof value.message === "string" &&
    isObject(value.payload) &&
    typeof value.created_at === "string"
  );
}

function isString(value: unknown): value is string {
  return typeof value === "string";
}

function isNumber(value: unknown): value is number {
  return typeof value === "number" && Number.isFinite(value);
}

function isNullableString(value: unknown): value is string | null {
  return value === null || typeof value === "string";
}

function isTarget(value: unknown): boolean {
  return value === "case" || value === "course";
}

function isSourceKind(value: unknown): boolean {
  return value === "file" || value === "zip";
}

function isJobStatus(value: unknown): boolean {
  return (
    value === "draft" ||
    value === "queued" ||
    value === "running" ||
    value === "rolling_back" ||
    value === "succeeded" ||
    value === "failed" ||
    value === "deleting"
  );
}

function isStage(value: unknown): boolean {
  return (
    value === "uploaded" ||
    value === "parsing" ||
    value === "chunking" ||
    value === "indexing" ||
    value === "completed"
  );
}

function isItemStatus(value: unknown): boolean {
  return (
    value === "pending" ||
    value === "running" ||
    value === "succeeded" ||
    value === "failed" ||
    value === "skipped"
  );
}

function isAttemptStatus(value: unknown): boolean {
  return (
    value === "queued" ||
    value === "running" ||
    value === "succeeded" ||
    value === "failed" ||
    value === "rolling_back"
  );
}

function isCommitState(value: unknown): boolean {
  return (
    value === "not_started" ||
    value === "prepared" ||
    value === "committed" ||
    value === "rolling_back" ||
    value === "rolled_back"
  );
}
