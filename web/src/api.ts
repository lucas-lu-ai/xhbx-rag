import type {
  AnswerRequest,
  AnswerResponse,
  AnswerStreamEvent,
  BadCaseRequest,
  BadCaseResponse,
  RevealRequest,
  RevealResponse,
  StatusResponse
} from "./types";

type ApiOptions = {
  baseUrl?: string;
  fetcher?: typeof fetch;
};

type StreamHandlers = {
  onEvent?: (event: AnswerStreamEvent) => void;
};

export class ApiError extends Error {
  readonly status: number;
  readonly detail: string;
  readonly body: unknown;

  constructor(status: number, detail: string, body: unknown) {
    super(detail);
    this.name = "ApiError";
    this.status = status;
    this.detail = detail;
    this.body = body;
  }
}

export function getStatus(options: ApiOptions = {}): Promise<StatusResponse> {
  return requestJson<StatusResponse>("/api/status", { method: "GET" }, options);
}

export function answerQuestion(
  request: AnswerRequest,
  options: ApiOptions = {}
): Promise<AnswerResponse> {
  return requestJson<AnswerResponse>(
    "/api/answer",
    {
      method: "POST",
      body: JSON.stringify(request)
    },
    options
  );
}

export async function answerQuestionStream(
  request: AnswerRequest,
  handlers: StreamHandlers = {},
  options: ApiOptions = {}
): Promise<AnswerResponse> {
  const fetcher = options.fetcher ?? fetch;
  const response = await fetcher(endpoint("/api/answer/stream", options.baseUrl), {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(request)
  });

  if (!response.ok) {
    const body = await parseResponseBody(response);
    throw new ApiError(response.status, responseDetail(body, response.status), body);
  }
  if (!response.body) {
    throw new Error("浏览器不支持流式响应");
  }

  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let finalResponse: AnswerResponse | undefined;

  while (true) {
    const { done, value } = await reader.read();
    buffer += decoder.decode(value ?? new Uint8Array(), { stream: !done });
    const blocks = buffer.split(/\r?\n\r?\n/);
    buffer = blocks.pop() ?? "";

    for (const block of blocks) {
      const event = parseSseEvent(block);
      if (!event) {
        continue;
      }
      handlers.onEvent?.(event);
      if (event.type === "error") {
        throw new Error(event.detail);
      }
      if (event.type === "final") {
        finalResponse = event.response;
      }
    }

    if (done) {
      break;
    }
  }

  const trailingEvent = parseSseEvent(buffer);
  if (trailingEvent) {
    handlers.onEvent?.(trailingEvent);
    if (trailingEvent.type === "error") {
      throw new Error(trailingEvent.detail);
    }
    if (trailingEvent.type === "final") {
      finalResponse = trailingEvent.response;
    }
  }

  if (!finalResponse) {
    throw new Error("流式回答没有返回最终结果");
  }
  return finalResponse;
}

export function revealSource(
  request: RevealRequest,
  options: ApiOptions = {}
): Promise<RevealResponse> {
  return requestJson<RevealResponse>(
    "/api/source/reveal",
    {
      method: "POST",
      body: JSON.stringify(request)
    },
    options
  );
}

export function submitBadCase(
  request: BadCaseRequest,
  options: ApiOptions = {}
): Promise<BadCaseResponse> {
  return requestJson<BadCaseResponse>(
    "/api/bad-cases",
    {
      method: "POST",
      body: JSON.stringify(request)
    },
    options
  );
}

async function requestJson<T>(
  path: string,
  init: RequestInit,
  options: ApiOptions
): Promise<T> {
  const fetcher = options.fetcher ?? fetch;
  const headers = new Headers(init.headers);
  if (init.body !== undefined && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }

  const response = await fetcher(endpoint(path, options.baseUrl), {
    ...init,
    headers
  });
  const body = await parseResponseBody(response);

  if (!response.ok) {
    throw new ApiError(response.status, responseDetail(body, response.status), body);
  }

  return body as T;
}

function endpoint(path: string, baseUrl?: string): string {
  const configuredBase =
    baseUrl ?? (import.meta.env.VITE_API_BASE_URL as string | undefined) ?? "";
  return `${configuredBase.replace(/\/$/, "")}${path}`;
}

async function parseResponseBody(response: Response): Promise<unknown> {
  const text = await response.text();
  if (!text) {
    return null;
  }

  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function responseDetail(body: unknown, status: number): string {
  if (isObject(body) && typeof body.detail === "string") {
    return body.detail;
  }
  return `请求失败 (${status})`;
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function parseSseEvent(block: string): AnswerStreamEvent | null {
  const trimmed = block.trim();
  if (!trimmed) {
    return null;
  }

  let eventType = "";
  const dataLines: string[] = [];
  for (const line of trimmed.split(/\r?\n/)) {
    if (line.startsWith("event:")) {
      eventType = line.slice("event:".length).trim();
    }
    if (line.startsWith("data:")) {
      dataLines.push(line.slice("data:".length).trimStart());
    }
  }
  if (dataLines.length === 0) {
    return null;
  }

  let parsed: unknown;
  try {
    parsed = JSON.parse(dataLines.join("\n"));
  } catch {
    return null;
  }
  return toAnswerStreamEvent(parsed, eventType);
}

function toAnswerStreamEvent(
  value: unknown,
  fallbackType: string
): AnswerStreamEvent | null {
  if (!isObject(value)) {
    return null;
  }
  const type = typeof value.type === "string" ? value.type : fallbackType;
  if (
    type === "step" &&
    typeof value.step === "string" &&
    typeof value.message === "string"
  ) {
    return {
      type,
      step: value.step,
      message: value.message,
      payload: isObject(value.payload) ? value.payload : undefined
    };
  }
  if (type === "answer_delta" && typeof value.text === "string") {
    return { type, text: value.text };
  }
  if (type === "final" && isObject(value.response)) {
    return { type, response: value.response as AnswerResponse };
  }
  if (type === "error" && typeof value.detail === "string") {
    return { type, detail: value.detail };
  }
  return null;
}
