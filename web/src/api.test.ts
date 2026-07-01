import { answerQuestion, getStatus, revealSource } from "./api";

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: 200,
    headers: { "Content-Type": "application/json" },
    ...init
  });
}

test("getStatus calls status endpoint", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    expect(input).toBe("/api/status");
    return jsonResponse({
      ok: true,
      data_dir: "data",
      milvus_lite_path: ".local/milvus/xhbx_rag.db",
      milvus_collection: "xhbx_sales_chunks",
      config: { API_KEY: true },
      errors: []
    });
  });

  const status = await getStatus({ fetcher });

  expect(status.ok).toBe(true);
  expect(fetcher).toHaveBeenCalledTimes(1);
});

test("answerQuestion posts typed payload", async () => {
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    expect(input).toBe("http://127.0.0.1:8000/api/answer");
    expect(init?.method).toBe("POST");
    expect(init?.body).toBe(
      JSON.stringify({ query: "保单整理有什么作用？", top_n: 20, top_k: 5 })
    );
    expect(new Headers(init?.headers).get("Content-Type")).toBe("application/json");
    return jsonResponse({
      answer: "保单整理能帮助客户看清保障缺口。",
      citations: [],
      evidence_count: 0
    });
  });

  const result = await answerQuestion(
    { query: "保单整理有什么作用？", top_n: 20, top_k: 5 },
    { baseUrl: "http://127.0.0.1:8000/", fetcher }
  );

  expect(result.answer).toContain("保障缺口");
});

test("revealSource returns resolved path", async () => {
  const fetcher = vi.fn(async () =>
    jsonResponse({ ok: true, resolved_path: "/tmp/data/a.txt" })
  );

  const result = await revealSource({ source_path: "data/a.txt" }, { fetcher });

  expect(result.resolved_path).toBe("/tmp/data/a.txt");
});

test("api errors expose safe detail", async () => {
  const fetcher = vi.fn(async () =>
    jsonResponse({ detail: "问答服务暂时不可用" }, { status: 502 })
  );

  await expect(
    answerQuestion({ query: "x" }, { fetcher })
  ).rejects.toMatchObject({
    status: 502,
    detail: "问答服务暂时不可用"
  });
});
