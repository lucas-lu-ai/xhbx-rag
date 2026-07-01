export type ConfigStatus = Record<string, boolean>;

export type StatusResponse = {
  ok: boolean;
  data_dir: string;
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

export type AnswerResponse = {
  original_query?: string;
  rewritten_query?: string;
  intent?: string;
  filters?: Record<string, unknown>;
  answer: string;
  citations: Citation[];
  evidence_count: number;
  [key: string]: unknown;
};

export type ChatTurn = {
  id: string;
  query: string;
  response?: AnswerResponse;
  error?: string;
};

export type AnswerRequest = {
  query: string;
  top_n?: number;
  top_k?: number;
};

export type RevealRequest = {
  source_path: string;
};

export type RevealResponse = {
  ok: boolean;
  resolved_path: string;
};
