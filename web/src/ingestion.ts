import type { IngestionJobStatus, IngestionStage } from "./types";

const STATUS_LABELS = {
  draft: "待确认",
  queued: "排队中",
  running: "运行中",
  rolling_back: "清理中",
  succeeded: "已完成",
  failed: "失败",
  deleting: "删除中"
} satisfies Record<IngestionJobStatus, string>;

const STAGE_LABELS = {
  uploaded: "上传完成",
  parsing: "解析中",
  chunking: "切分中",
  indexing: "入库中",
  completed: "已完成"
} satisfies Record<IngestionStage, string>;

const ACTIVE_STATUSES: ReadonlySet<IngestionJobStatus> = new Set([
  "queued",
  "running",
  "rolling_back",
  "deleting"
]);

export function ingestionStatusLabel(status: string): string {
  return isIngestionJobStatus(status) ? STATUS_LABELS[status] : "未知状态";
}

export function ingestionStageLabel(stage: string): string {
  return isIngestionStage(stage) ? STAGE_LABELS[stage] : "处理中";
}

export function isIngestionJobActive(status: string): boolean {
  return isIngestionJobStatus(status) && ACTIVE_STATUSES.has(status);
}

function isIngestionJobStatus(value: string): value is IngestionJobStatus {
  switch (value) {
    case "draft":
    case "queued":
    case "running":
    case "rolling_back":
    case "succeeded":
    case "failed":
    case "deleting":
      return true;
    default:
      return false;
  }
}

function isIngestionStage(value: string): value is IngestionStage {
  switch (value) {
    case "uploaded":
    case "parsing":
    case "chunking":
    case "indexing":
    case "completed":
      return true;
    default:
      return false;
  }
}
