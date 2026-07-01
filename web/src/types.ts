export type ConfigStatus = Record<string, boolean>;

export type StatusResponse = {
  ok: boolean;
  data_dir: string;
  milvus_mode: string;
  milvus_target: string;
  milvus_lite_path: string;
  milvus_collection: string;
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
  [key: string]: unknown;
};

export type RetrievalEvidence = {
  chunk_id?: string;
  chunk_type?: string;
  text?: string;
  text_preview?: string;
  score?: number;
  rerank_score?: number;
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
  streaming_answer?: string;
  is_streaming?: boolean;
  response?: AnswerResponse;
  error?: string;
};

export type AnswerRequest = {
  query: string;
  top_n?: number;
  top_k?: number;
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

export type EvidenceFeedbackJudgement = "should_use" | "should_not_use";

export type EvidenceFeedback = {
  chunk_id?: string;
  judgement: EvidenceFeedbackJudgement;
  label: string;
  text_preview: string;
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
