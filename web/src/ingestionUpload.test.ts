import { uploadIngestionJob } from "./ingestionUpload";
import type { IngestionJobDetail } from "./types";

type FakeListener = EventListenerOrEventListenerObject;

class FakeEventTarget {
  private readonly listeners = new Map<string, Set<FakeListener>>();

  addEventListener(type: string, listener: FakeListener): void {
    const listeners = this.listeners.get(type) ?? new Set<FakeListener>();
    listeners.add(listener);
    this.listeners.set(type, listeners);
  }

  removeEventListener(type: string, listener: FakeListener): void {
    this.listeners.get(type)?.delete(listener);
  }

  emit(type: string, event: Event): void {
    for (const listener of [...(this.listeners.get(type) ?? [])]) {
      if (typeof listener === "function") {
        listener.call(this, event);
      } else {
        listener.handleEvent(event);
      }
    }
  }

  listenerCount(): number {
    return [...this.listeners.values()].reduce(
      (total, listeners) => total + listeners.size,
      0
    );
  }
}

class FakeXMLHttpRequest extends FakeEventTarget {
  readonly upload = new FakeEventTarget();
  readonly headers = new Map<string, string>();
  private statusValue = 0;
  private responseTextValue = "";
  throwOnStatus = false;
  throwOnResponseText = false;
  timeout = 0;
  method = "";
  url = "";
  async = true;
  body: Document | XMLHttpRequestBodyInit | null = null;
  abortCalls = 0;

  get status(): number {
    if (this.throwOnStatus) {
      throw new Error("status getter failed");
    }
    return this.statusValue;
  }

  get responseText(): string {
    if (this.throwOnResponseText) {
      throw new Error("responseText getter failed");
    }
    return this.responseTextValue;
  }

  open(method: string, url: string | URL, async = true): void {
    this.method = method;
    this.url = String(url);
    this.async = async;
  }

  setRequestHeader(name: string, value: string): void {
    this.headers.set(name.toLowerCase(), value);
  }

  send(body: Document | XMLHttpRequestBodyInit | null = null): void {
    this.body = body;
  }

  abort(): void {
    this.abortCalls += 1;
    this.emit("abort", new Event("abort"));
  }

  get formData(): FormData {
    if (!(this.body instanceof FormData)) {
      throw new Error("XHR body is not FormData");
    }
    return this.body;
  }

  emitProgress(loaded: number, total: number, lengthComputable = true): void {
    const event = new ProgressEvent("progress", {
      loaded,
      total,
      lengthComputable
    });
    this.upload.emit("progress", event);
  }

  resolve(status: number, body: unknown): void {
    this.statusValue = status;
    this.responseTextValue = JSON.stringify(body);
    this.emit("load", new Event("load"));
  }

  resolveText(status: number, body: string): void {
    this.statusValue = status;
    this.responseTextValue = body;
    this.emit("load", new Event("load"));
  }

  failNetwork(): void {
    this.emit("error", new Event("error"));
  }

  failTimeout(): void {
    this.emit("timeout", new Event("timeout"));
  }

  totalListenerCount(): number {
    return this.listenerCount() + this.upload.listenerCount();
  }
}

function ingestionDraftPayload(
  overrides: Partial<IngestionJobDetail> = {}
): IngestionJobDetail {
  return {
    job_id: "job-1",
    source_name: "课程.txt",
    source_kind: "file",
    target: "course",
    status: "draft",
    current_stage: "uploaded",
    attempt_count: 0,
    item_total: 1,
    item_done: 0,
    document_total: 1,
    chunk_total: 0,
    ignored_total: 0,
    warning_count: 0,
    error_code: null,
    error_detail: null,
    created_at: "2026-07-12T08:00:00+00:00",
    updated_at: "2026-07-12T08:00:00+00:00",
    started_at: null,
    finished_at: null,
    ignored_entries: [],
    items: [
      {
        item_index: 1,
        unit_key: "课程.txt",
        display_name: "课程",
        relative_paths: ["课程.txt"],
        document_count: 1,
        status: "pending",
        current_stage: "uploaded",
        chunk_count: 0,
        warning_count: 0,
        error_detail: null,
        updated_at: "2026-07-12T08:00:00+00:00"
      }
    ],
    attempt: null,
    events: [],
    ...overrides
  };
}

test("uploadIngestionJob sends multipart and reports progress", async () => {
  const xhr = new FakeXMLHttpRequest();
  const progress: number[] = [];
  const promise = uploadIngestionJob(
    new File(["课程"], "课程.txt", { type: "text/plain" }),
    "course",
    { xhrFactory: () => xhr, onProgress: (value) => progress.push(value) }
  );

  xhr.emitProgress(5, 10);
  xhr.resolve(201, ingestionDraftPayload());

  await expect(promise).resolves.toMatchObject({ status: "draft" });
  expect(progress).toEqual([50]);
  expect(xhr.method).toBe("POST");
  expect(xhr.url).toBe("/api/ingestion-jobs");
  expect(xhr.formData.get("target")).toBe("course");
  expect(xhr.formData.get("file")).toBeInstanceOf(File);
  expect(xhr.headers.has("content-type")).toBe(false);
  expect(xhr.totalListenerCount()).toBe(0);
});

test("upload progress ignores unknown totals and clamps rounded percentages", async () => {
  const xhr = new FakeXMLHttpRequest();
  const progress: number[] = [];
  const promise = uploadIngestionJob(new File(["x"], "x.txt"), "case", {
    xhrFactory: () => xhr,
    onProgress: (value) => progress.push(value)
  });

  xhr.emitProgress(5, 0, false);
  xhr.emitProgress(1, 3);
  xhr.emitProgress(101, 100);
  xhr.resolve(201, ingestionDraftPayload({ target: "case" }));

  await promise;
  expect(progress).toEqual([33, 100]);
});

test.each([
  [{ detail: "上传文件无效" }, "上传文件无效"],
  [{ detail: { message: "private" } }, "上传失败 (400)"],
  ["not-json", "上传失败 (400)"]
])("non-2xx responses expose only a string detail", async (body, message) => {
  const xhr = new FakeXMLHttpRequest();
  const promise = uploadIngestionJob(new File(["x"], "x.txt"), "case", {
    xhrFactory: () => xhr
  });

  if (typeof body === "string") {
    xhr.resolveText(400, body);
  } else {
    xhr.resolve(400, body);
  }

  await expect(promise).rejects.toThrow(message);
  expect(xhr.totalListenerCount()).toBe(0);
});

test("status zero load is reported as a fixed network error", async () => {
  const xhr = new FakeXMLHttpRequest();
  const promise = uploadIngestionJob(new File(["x"], "x.txt"), "case", {
    xhrFactory: () => xhr
  });

  xhr.resolve(0, {});

  await expect(promise).rejects.toThrow("上传失败，请检查网络连接");
  expect(xhr.totalListenerCount()).toBe(0);
});

test("non-2xx responseText getter failure rejects with fixed status error", async () => {
  const xhr = new FakeXMLHttpRequest();
  const promise = uploadIngestionJob(new File(["x"], "x.txt"), "case", {
    xhrFactory: () => xhr
  });
  xhr.throwOnResponseText = true;

  expect(() => xhr.resolve(500, {})).not.toThrow();

  await expect(promise).rejects.toThrow("上传失败 (500)");
  expect(xhr.totalListenerCount()).toBe(0);
});

test("2xx responseText getter failure rejects as invalid response", async () => {
  const xhr = new FakeXMLHttpRequest();
  const promise = uploadIngestionJob(new File(["x"], "x.txt"), "case", {
    xhrFactory: () => xhr
  });
  xhr.throwOnResponseText = true;

  expect(() => xhr.resolve(201, {})).not.toThrow();

  await expect(promise).rejects.toThrow("上传响应无效");
  expect(xhr.totalListenerCount()).toBe(0);
});

test("status getter failure rejects as invalid response", async () => {
  const xhr = new FakeXMLHttpRequest();
  const promise = uploadIngestionJob(new File(["x"], "x.txt"), "case", {
    xhrFactory: () => xhr
  });
  xhr.throwOnStatus = true;

  expect(() => xhr.resolve(201, ingestionDraftPayload())).not.toThrow();

  await expect(promise).rejects.toThrow("上传响应无效");
  expect(xhr.totalListenerCount()).toBe(0);
});

test("a syntactically valid but incomplete success payload is rejected", async () => {
  const xhr = new FakeXMLHttpRequest();
  const promise = uploadIngestionJob(new File(["x"], "x.txt"), "case", {
    xhrFactory: () => xhr
  });

  xhr.resolve(201, { status: "draft" });

  await expect(promise).rejects.toThrow("上传响应无效");
  expect(xhr.totalListenerCount()).toBe(0);
});

test.each([
  ["invalid-json", "上传响应无效"],
  ["network", "上传失败，请检查网络连接"],
  ["timeout", "上传超时，请重试"]
])("%s failures use fixed displayable errors", async (kind, message) => {
  const xhr = new FakeXMLHttpRequest();
  const promise = uploadIngestionJob(new File(["x"], "x.txt"), "case", {
    xhrFactory: () => xhr,
    timeoutMs: 1234
  });

  if (kind === "invalid-json") {
    xhr.resolveText(201, "not-json");
  } else if (kind === "network") {
    xhr.failNetwork();
  } else {
    xhr.failTimeout();
  }

  await expect(promise).rejects.toThrow(message);
  expect(xhr.timeout).toBe(1234);
  expect(xhr.totalListenerCount()).toBe(0);
});

test("an already aborted signal rejects before creating an XHR", async () => {
  const controller = new AbortController();
  controller.abort();
  const factory = vi.fn(() => new FakeXMLHttpRequest());

  await expect(
    uploadIngestionJob(new File(["x"], "x.txt"), "case", {
      signal: controller.signal,
      xhrFactory: factory
    })
  ).rejects.toThrow("上传已取消");
  expect(factory).not.toHaveBeenCalled();
});

test("abort between the initial check and listener registration cannot start upload", async () => {
  const controller = new AbortController();
  const xhr = new FakeXMLHttpRequest();

  const promise = uploadIngestionJob(new File(["x"], "x.txt"), "case", {
    signal: controller.signal,
    xhrFactory: () => {
      controller.abort();
      return xhr;
    }
  });

  await expect(promise).rejects.toThrow("上传已取消");
  expect(xhr.abortCalls).toBe(1);
  expect(xhr.body).toBeNull();
  expect(xhr.totalListenerCount()).toBe(0);
});

test("a running abort settles once and removes every listener", async () => {
  const controller = new AbortController();
  const addSpy = vi.spyOn(controller.signal, "addEventListener");
  const removeSpy = vi.spyOn(controller.signal, "removeEventListener");
  const xhr = new FakeXMLHttpRequest();
  const promise = uploadIngestionJob(new File(["x"], "x.txt"), "case", {
    signal: controller.signal,
    xhrFactory: () => xhr
  });

  controller.abort();
  xhr.resolve(201, ingestionDraftPayload());

  await expect(promise).rejects.toThrow("上传已取消");
  expect(xhr.abortCalls).toBe(1);
  expect(xhr.totalListenerCount()).toBe(0);
  expect(addSpy).toHaveBeenCalledWith("abort", expect.any(Function), { once: true });
  expect(removeSpy).toHaveBeenCalledWith("abort", expect.any(Function));
});
