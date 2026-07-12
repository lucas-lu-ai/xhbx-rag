import {
  ingestionStageLabel,
  ingestionStatusLabel,
  isIngestionJobActive
} from "./ingestion";

test("ingestion status labels and active state are stable", () => {
  expect(ingestionStatusLabel("draft")).toBe("待确认");
  expect(ingestionStatusLabel("queued")).toBe("排队中");
  expect(ingestionStatusLabel("running")).toBe("运行中");
  expect(ingestionStatusLabel("rolling_back")).toBe("清理中");
  expect(ingestionStatusLabel("succeeded")).toBe("已完成");
  expect(ingestionStatusLabel("failed")).toBe("失败");
  expect(ingestionStatusLabel("deleting")).toBe("删除中");

  expect(isIngestionJobActive("draft")).toBe(false);
  expect(isIngestionJobActive("queued")).toBe(true);
  expect(isIngestionJobActive("running")).toBe(true);
  expect(isIngestionJobActive("rolling_back")).toBe(true);
  expect(isIngestionJobActive("succeeded")).toBe(false);
  expect(isIngestionJobActive("failed")).toBe(false);
  expect(isIngestionJobActive("deleting")).toBe(true);
});

test("ingestion stage labels cover every stage", () => {
  expect(ingestionStageLabel("uploaded")).toBe("上传完成");
  expect(ingestionStageLabel("parsing")).toBe("解析中");
  expect(ingestionStageLabel("chunking")).toBe("切分中");
  expect(ingestionStageLabel("indexing")).toBe("入库中");
  expect(ingestionStageLabel("completed")).toBe("已完成");
});

test("unknown runtime status and stage values use safe labels", () => {
  expect(ingestionStatusLabel("future_status")).toBe("未知状态");
  expect(ingestionStageLabel("future_stage")).toBe("处理中");
  expect(isIngestionJobActive("future_status")).toBe(false);
});
