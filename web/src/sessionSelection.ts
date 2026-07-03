import { getStorage } from "./chatSessions";
import { isObject } from "./format";
import type { SessionSelection } from "./types";

export const ACTIVE_SESSION_STORAGE_KEY = "xhbx-rag.active-session.v1";

export function loadSessionSelection(): SessionSelection | null {
  const storage = getStorage();
  if (!storage) {
    return null;
  }

  try {
    const raw = storage.getItem(ACTIVE_SESSION_STORAGE_KEY);
    if (!raw) {
      return null;
    }
    return normalizeSessionSelection(JSON.parse(raw));
  } catch {
    return null;
  }
}

export function persistSessionSelection(selection: SessionSelection) {
  const storage = getStorage();
  if (!storage) {
    return;
  }

  try {
    storage.setItem(ACTIVE_SESSION_STORAGE_KEY, JSON.stringify(selection));
  } catch {
    // 持久化尽力而为，写失败不影响当前会话切换。
  }
}

export function normalizeSessionSelection(
  value: unknown
): SessionSelection | null {
  if (
    !isObject(value) ||
    (value.kind !== "chat" && value.kind !== "batch") ||
    typeof value.id !== "string" ||
    !value.id
  ) {
    return null;
  }
  return { kind: value.kind, id: value.id };
}
