import { isObject } from "./format";
import type {
  AnswerProcessStep,
  AnswerResponse,
  ChatSession,
  ChatTurn,
  StoredChatSessions
} from "./types";

export const CHAT_SESSIONS_STORAGE_KEY = "xhbx-rag.chat-sessions.v1";
export const DEFAULT_SESSION_TITLE = "新会话";

const MAX_SESSION_TITLE_LENGTH = 32;

export function getStorage(): Storage | null {
  if (
    typeof localStorage === "undefined" ||
    typeof localStorage.getItem !== "function"
  ) {
    return null;
  }
  return localStorage;
}

export function loadChatSessions(): StoredChatSessions {
  const fallback = createDefaultSessionStore();
  const storage = getStorage();
  if (!storage) {
    return fallback;
  }

  try {
    const raw = storage.getItem(CHAT_SESSIONS_STORAGE_KEY);
    if (!raw) {
      return fallback;
    }
    return normalizeStoredChatSessions(JSON.parse(raw)) ?? fallback;
  } catch {
    return fallback;
  }
}

export function persistChatSessions(store: StoredChatSessions) {
  const storage = getStorage();
  if (!storage) {
    return;
  }

  try {
    storage.setItem(CHAT_SESSIONS_STORAGE_KEY, JSON.stringify(store));
  } catch {
    // 持久化尽力而为，写失败时内存中的会话仍可继续使用。
  }
}

export function normalizeStoredChatSessions(
  value: unknown
): StoredChatSessions | null {
  if (
    !isObject(value) ||
    value.version !== 1 ||
    typeof value.active_session_id !== "string" ||
    !Array.isArray(value.sessions)
  ) {
    return null;
  }

  const sessions = value.sessions.filter(isChatSession);
  if (sessions.length === 0) {
    return null;
  }

  const activeSessionId = sessions.some(
    (session) => session.id === value.active_session_id
  )
    ? value.active_session_id
    : sessions[0].id;

  return {
    version: 1,
    active_session_id: activeSessionId,
    sessions
  };
}

function isChatSession(value: unknown): value is ChatSession {
  return (
    isObject(value) &&
    typeof value.id === "string" &&
    typeof value.title === "string" &&
    typeof value.created_at === "string" &&
    typeof value.updated_at === "string" &&
    Array.isArray(value.turns)
  );
}

export function createDefaultSessionStore(): StoredChatSessions {
  const session = createEmptySession();
  return {
    version: 1,
    active_session_id: session.id,
    sessions: [session]
  };
}

export function createEmptySession(): ChatSession {
  const now = new Date().toISOString();
  return {
    id: makeTurnId(),
    title: DEFAULT_SESSION_TITLE,
    created_at: now,
    updated_at: now,
    turns: []
  };
}

export function findActiveSession(store: StoredChatSessions): ChatSession {
  return (
    store.sessions.find((session) => session.id === store.active_session_id) ??
    store.sessions[0] ??
    createEmptySession()
  );
}

export function updateSession(
  store: StoredChatSessions,
  sessionId: string,
  updater: (session: ChatSession) => ChatSession
): StoredChatSessions {
  let found = false;
  const sessions = store.sessions.map((session) => {
    if (session.id !== sessionId) {
      return session;
    }
    found = true;
    return updater(session);
  });

  if (!found) {
    return store;
  }

  return {
    ...store,
    sessions
  };
}

export function deleteSessionFromStore(
  store: StoredChatSessions,
  sessionId: string
): StoredChatSessions {
  const remaining = store.sessions.filter((session) => session.id !== sessionId);
  if (remaining.length === 0) {
    return createDefaultSessionStore();
  }

  const activeSessionId =
    store.active_session_id === sessionId
      ? mostRecentlyUpdatedSession(remaining).id
      : store.active_session_id;

  return {
    ...store,
    active_session_id: activeSessionId,
    sessions: remaining
  };
}

export function mostRecentlyUpdatedSession(sessions: ChatSession[]): ChatSession {
  return [...sessions].sort(
    (left, right) =>
      new Date(right.updated_at).getTime() - new Date(left.updated_at).getTime()
  )[0];
}

export function sessionTitleForQuestion(
  session: ChatSession,
  query: string
): string | undefined {
  if (session.title !== DEFAULT_SESSION_TITLE || session.turns.length > 0) {
    return undefined;
  }
  return makeSessionTitle(query);
}

export function makeSessionTitle(query: string): string {
  const normalized = query.replace(/\s+/g, " ").trim();
  if (normalized.length <= MAX_SESSION_TITLE_LENGTH) {
    return normalized || DEFAULT_SESSION_TITLE;
  }
  return `${normalized.slice(0, MAX_SESSION_TITLE_LENGTH)}...`;
}

export function makeTurnId(): string {
  if (typeof crypto !== "undefined" && "randomUUID" in crypto) {
    return crypto.randomUUID();
  }
  return `${Date.now()}-${Math.random()}`;
}

export function makeStreamingTurn(
  id: string,
  query: string,
  topN: number,
  topK: number
): ChatTurn {
  return {
    id,
    query,
    top_n: topN,
    top_k: topK,
    process_steps: [],
    streaming_answer: "",
    is_streaming: true
  };
}

export function appendProcessStep(
  turns: ChatTurn[],
  turnId: string,
  step: AnswerProcessStep
): ChatTurn[] {
  return turns.map((turn) =>
    turn.id === turnId
      ? {
          ...turn,
          process_steps: [...(turn.process_steps ?? []), step]
        }
      : turn
  );
}

export function appendAnswerDelta(
  turns: ChatTurn[],
  turnId: string,
  text: string
): ChatTurn[] {
  return turns.map((turn) =>
    turn.id === turnId
      ? {
          ...turn,
          streaming_answer: `${turn.streaming_answer ?? ""}${text}`
        }
      : turn
  );
}

export function appendThinkingDelta(
  turns: ChatTurn[],
  turnId: string,
  text: string
): ChatTurn[] {
  return turns.map((turn) =>
    turn.id === turnId
      ? {
          ...turn,
          streaming_reasoning: `${turn.streaming_reasoning ?? ""}${text}`
        }
      : turn
  );
}

export function completeTurn(
  turns: ChatTurn[],
  turnId: string,
  response: AnswerResponse
): ChatTurn[] {
  return turns.map((turn) =>
    turn.id === turnId
      ? {
          ...turn,
          response,
          streaming_answer: response.answer,
          is_streaming: false
        }
      : turn
  );
}

export function failTurn(
  turns: ChatTurn[],
  turnId: string,
  message: string
): ChatTurn[] {
  return turns.map((turn) =>
    turn.id === turnId ? { ...turn, error: message, is_streaming: false } : turn
  );
}

export function validateLimits(topN: number, topK: number): string {
  if (!Number.isInteger(topN) || topN < 1 || topN > 100) {
    return "召回数量必须在 1 到 100 之间。";
  }
  if (!Number.isInteger(topK) || topK < 1 || topK > 20) {
    return "引用数量必须在 1 到 20 之间。";
  }
  if (topK > topN) {
    return "引用数量不能大于召回数量。";
  }
  return "";
}
