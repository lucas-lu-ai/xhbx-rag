import type {
  AnswerRequest,
  AnswerResponse,
  RevealRequest,
  RevealResponse,
  StatusResponse
} from "./types";

type ApiOptions = {
  baseUrl?: string;
  fetcher?: typeof fetch;
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
