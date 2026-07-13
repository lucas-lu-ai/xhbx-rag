export type ConfigStatus = Record<string, boolean>;

export type StatusResponse = {
  ok: boolean;
  data_dir: string;
  milvus_mode: string;
  milvus_target: string;
  milvus_lite_path: string;
  milvus_collection: string;
  milvus_course_collection?: string;
  milvus_collections?: string[];
  batch_concurrency: number;
  web_retrieval_top_n: number;
  web_retrieval_top_k: number;
  config: ConfigStatus;
  errors: string[];
};

export type Citation = {
  filename?: string;
  source_type?: string;
  source_path?: string;
  locator?: Record<string, unknown>;
  locator_confidence?: string;
  anchor_id?: string;
  quote?: string;
  source_excerpt?: string;
  display_location: string;
  display_excerpt: string;
  can_reveal: boolean;
  // 后端标注：是否为模型选中的引用，以及所属证据序号（1-based）。旧数据可能缺失。
  selected?: boolean;
  evidence_index?: number;
  [key: string]: unknown;
};

export type RetrievalEvidence = {
  chunk_id?: string;
  chunk_type?: string;
  text?: string;
  text_preview?: string;
  score?: number;
  rerank_score?: number;
  matched_tag_paths?: string[];
  tag_boost_factor?: number;
  metadata?: Record<string, unknown>;
  citations?: Citation[];
  [key: string]: unknown;
};

export type AnswerResponse = {
  original_query?: string;
  rewritten_query?: string;
  intent?: string;
  filters?: Record<string, unknown>;
  answer: string;
  reasoning?: string;
  citations: Citation[];
  evidence_count: number;
  retrieval_evidences?: RetrievalEvidence[];
  [key: string]: unknown;
};

export type ChatTurn = {
  id: string;
  query: string;
  top_n: number;
  top_k: number;
  process_steps?: AnswerProcessStep[];
  streaming_reasoning?: string;
  streaming_answer?: string;
  is_streaming?: boolean;
  response?: AnswerResponse;
  error?: string;
};

export type ChatSession = {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  turns: ChatTurn[];
};

export type StoredChatSessions = {
  version: 1;
  active_session_id: string;
  sessions: ChatSession[];
};

export type AnswerRequest = {
  query: string;
  top_n?: number;
  top_k?: number;
  collections?: string[];
};

export type AnswerProcessStep = {
  step: string;
  message: string;
  payload?: Record<string, unknown>;
};

export type AnswerStreamStepEvent = {
  type: "step";
  step: string;
  message: string;
  payload?: Record<string, unknown>;
};

export type AnswerStreamDeltaEvent = {
  type: "answer_delta";
  text: string;
};

export type AnswerStreamThinkingEvent = {
  type: "thinking_delta";
  text: string;
};

export type AnswerStreamFinalEvent = {
  type: "final";
  response: AnswerResponse;
};

export type AnswerStreamErrorEvent = {
  type: "error";
  detail: string;
};

export type AnswerStreamEvent =
  | AnswerStreamStepEvent
  | AnswerStreamDeltaEvent
  | AnswerStreamThinkingEvent
  | AnswerStreamFinalEvent
  | AnswerStreamErrorEvent;

export type RevealRequest = {
  source_path: string;
};

export type RevealResponse = {
  ok: boolean;
  resolved_path: string;
};

export type BadCaseIssueType =
  | "usable"
  | "inaccurate"
  | "incomplete"
  | "citation_issue"
  | "customer_mismatch"
  | "off_topic"
  | "missing_talk_track"
  | "case_mismatch"
  | "citation_mismatch"
  | "not_customer_ready"
  | "compliance_risk"
  | "missing_knowledge"
  | "ranking_wrong"
  | "citation_wrong"
  | "answer_unsupported"
  | "other";

export type BadCaseFeedbackResult =
  | "usable"
  | "inaccurate"
  | "incomplete"
  | "citation_issue"
  | "customer_mismatch";

export type BadCaseProblemTag =
  | "off_topic"
  | "missing_talk_track"
  | "case_mismatch"
  | "citation_mismatch"
  | "not_customer_ready"
  | "compliance_risk"
  | "other";

export type EvidenceFeedbackJudgement =
  | "should_use"
  | "should_not_use"
  | "ranking_low";

export type EvidenceFeedback = {
  chunk_id?: string;
  judgement: EvidenceFeedbackJudgement;
  label: string;
  text_preview: string;
  // “不该用”反馈附带的不可用理由；后端 evidence_feedback 为宽松 dict，可透传。
  reason?: string;
};

export type BadCaseRequest = {
  query: string;
  rewritten_query: string;
  answer: string;
  top_n: number;
  top_k: number;
  feedback_result: BadCaseFeedbackResult;
  problem_tags: BadCaseProblemTag[];
  problem_detail: string;
  expected_answer: string;
  reference_note: string;
  evidence_feedback: EvidenceFeedback[];
  issue_types: BadCaseIssueType[];
  expected_knowledge: string;
  expected_source: string;
  note: string;
  citations: Citation[];
  retrieval_evidences: RetrievalEvidence[];
};

export type BadCaseResponse = {
  ok: boolean;
  bad_case_id: string;
};

export type BatchSourceFormat = "txt" | "csv" | "xlsx" | "pasted";

export type BatchQuestionStatus = "pending" | "running" | "succeeded" | "failed";

export type BatchBadCaseJsonlRecord = BadCaseRequest & {
  batch_source_label: string;
  row_index: number;
  input_answer: string;
};

export type BatchQuestion = {
  id: string;
  row_index: number;
  query: string;
  input_answer: string;
  top_n: number;
  top_k: number;
  status: BatchQuestionStatus;
  process_steps: AnswerProcessStep[];
  streaming_answer: string;
  response?: AnswerResponse;
  error?: string;
  bad_case_payload?: BatchBadCaseJsonlRecord;
};

export type BatchRunState = {
  source_label: string;
  source_format: BatchSourceFormat;
  headers: string[];
  rows: string[][];
  questions: BatchQuestion[];
  running: boolean;
  active_question_id?: string;
};

export type BatchRunStatus = "pending" | "running" | "completed" | "interrupted";

export type BatchRunSummary = {
  run_id: string;
  title: string;
  status: BatchRunStatus;
  source_label: string;
  source_format: BatchSourceFormat;
  question_total: number;
  question_done: number;
  question_failed: number;
  created_at: string;
  updated_at: string;
};

export type BatchRunListResponse = {
  runs: BatchRunSummary[];
};

export type BatchRunQuestionProgress = {
  row_index: number;
  status: BatchQuestionStatus;
  updated_at: string;
};

export type BatchRunProgress = {
  run_id: string;
  status: BatchRunStatus;
  question_total: number;
  question_done: number;
  question_failed: number;
  updated_at: string;
  questions: BatchRunQuestionProgress[];
};

export type BatchRunQuestionDetail = {
  row_index: number;
  query: string;
  input_answer: string;
  top_n: number;
  top_k: number;
  status: BatchQuestionStatus;
  response: AnswerResponse | null;
  error: string | null;
  bad_case: Record<string, unknown> | null;
  updated_at: string;
};

export type BatchRunDetail = BatchRunSummary & {
  questions: BatchRunQuestionDetail[];
  headers?: string[];
  rows?: string[][];
};

export type CreateBatchRunQuestion = {
  row_index: number;
  query: string;
  input_answer: string;
  top_n: number;
  top_k: number;
};

export type CreateBatchRunRequest = {
  title: string;
  source_label: string;
  source_format: BatchSourceFormat;
  headers: string[];
  rows: string[][];
  questions: CreateBatchRunQuestion[];
};

export type BatchRowBadCaseRequest = BadCaseRequest & {
  input_answer: string;
  batch_source_label: string;
};

export type OkResponse = {
  ok: boolean;
};

export type IngestionTarget = "case" | "course";

export type IngestionSourceKind = "file" | "zip";

export type IngestionJobStatus =
  | "draft"
  | "queued"
  | "running"
  | "rolling_back"
  | "succeeded"
  | "failed"
  | "deleting";

export type IngestionStage =
  | "uploaded"
  | "parsing"
  | "chunking"
  | "indexing"
  | "completed";

export type IngestionItemStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "skipped";

export type IngestionAttemptStatus =
  | "queued"
  | "running"
  | "succeeded"
  | "failed"
  | "rolling_back";

export type IngestionCommitState =
  | "not_started"
  | "prepared"
  | "committed"
  | "rolling_back"
  | "rolled_back";

export type IngestionPreflightItem = {
  item_index: number;
  unit_key: string;
  display_name: string;
  relative_paths: string[];
  document_count: number;
  status: IngestionItemStatus;
  current_stage: IngestionStage;
  chunk_count: number;
  warning_count: number;
  error_detail: string | null;
  updated_at: string;
};

export type IngestionJobSummary = {
  job_id: string;
  source_name: string;
  source_kind: IngestionSourceKind;
  target: IngestionTarget;
  status: IngestionJobStatus;
  current_stage: IngestionStage;
  attempt_count: number;
  item_total: number;
  item_done: number;
  document_total: number;
  chunk_total: number;
  ignored_total: number;
  warning_count: number;
  error_code: string | null;
  error_detail: string | null;
  created_at: string;
  updated_at: string;
  started_at: string | null;
  finished_at: string | null;
};

export type IngestionAttempt = {
  attempt_no: number;
  status: IngestionAttemptStatus;
  current_stage: IngestionStage;
  commit_state: IngestionCommitState;
  error_code: string | null;
  error_detail: string | null;
  started_at: string | null;
  finished_at: string | null;
};

export type IngestionEvent = {
  attempt_no: number;
  sequence: number;
  event_type: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
};

export type IngestionJobDetail = IngestionJobSummary & {
  ignored_entries: string[];
  items: IngestionPreflightItem[];
  attempt: IngestionAttempt | null;
  events: IngestionEvent[];
};

export type IngestionJobProgress = {
  job_id: string;
  status: IngestionJobStatus;
  current_stage: IngestionStage;
  attempt_no: number | null;
  item_total: number;
  item_done: number;
  document_total: number;
  chunk_total: number;
  warning_count: number;
  active_item_index: number | null;
  message: string | null;
  updated_at: string;
};

export type IngestionJobListResponse = {
  jobs: IngestionJobSummary[];
};

export type IngestionStartResponse = {
  ok: boolean;
  job_id: string;
  status: "queued";
};

export type IngestionRetryResponse = IngestionStartResponse & {
  attempt_no: number;
};

export type IngestionDeleteResponse = {
  ok: boolean;
  job_id: string;
  status: "deleted";
};

export type SessionSelection = {
  kind: "chat" | "batch";
  id: string;
};
