import { ListChecks, Plus, Trash2 } from "lucide-react";

import {
  batchRunProgressText,
  batchRunStatusLabel,
  mergeSessionEntries
} from "../batchRuns";
import { formatSessionTime } from "../format";
import type {
  BatchRunSummary,
  ChatSession,
  SessionSelection
} from "../types";
import { WorkspaceNav } from "./WorkspaceNav";

type SessionSidebarProps = {
  chatSessions: ChatSession[];
  batchRuns: BatchRunSummary[];
  batchRunsError: string;
  deleteError: string;
  selection: SessionSelection;
  onSelect: (selection: SessionSelection) => void;
  onDeleteChat: (sessionId: string) => void;
  onDeleteBatch: (runId: string) => void;
  onCreateSession: () => void;
  onCreateBatch: () => void;
  onOpenIngestion: () => void;
};

export function SessionSidebar({
  chatSessions,
  batchRuns,
  batchRunsError,
  deleteError,
  selection,
  onSelect,
  onDeleteChat,
  onDeleteBatch,
  onCreateSession,
  onCreateBatch,
  onOpenIngestion
}: SessionSidebarProps) {
  const entries = mergeSessionEntries(chatSessions, batchRuns);

  return (
    <aside className="session-panel" aria-label="会话列表">
      <WorkspaceNav
        currentView="chat"
        onNavigate={(view) => {
          if (view === "ingestion") onOpenIngestion();
        }}
      />
      <header className="session-header">
        <div className="session-header-actions">
          <button
            className="ghost-button session-new-button"
            type="button"
            onClick={onCreateSession}
          >
            <Plus size={16} aria-hidden="true" />
            新会话
          </button>
          <button
            className="ghost-button session-new-button"
            type="button"
            onClick={onCreateBatch}
          >
            <ListChecks size={16} aria-hidden="true" />
            批量执行
          </button>
        </div>
      </header>
      {(batchRunsError || deleteError) && (
        <p className="session-error" role="status">
          {deleteError || batchRunsError}
        </p>
      )}
      <nav className="session-list" aria-label="历史会话">
        {entries.map((entry) =>
          entry.kind === "chat" ? (
            <ChatSessionRow
              key={entry.key}
              session={entry.session}
              selected={
                selection.kind === "chat" && selection.id === entry.session.id
              }
              onSelect={() => onSelect({ kind: "chat", id: entry.session.id })}
              onDelete={() => onDeleteChat(entry.session.id)}
            />
          ) : (
            <BatchRunRow
              key={entry.key}
              run={entry.run}
              selected={
                selection.kind === "batch" && selection.id === entry.run.run_id
              }
              onSelect={() => onSelect({ kind: "batch", id: entry.run.run_id })}
              onDelete={() => onDeleteBatch(entry.run.run_id)}
            />
          )
        )}
      </nav>
    </aside>
  );
}

function ChatSessionRow({
  session,
  selected,
  onSelect,
  onDelete
}: {
  session: ChatSession;
  selected: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <div className={selected ? "session-row selected" : "session-row"}>
      <button
        className="session-item"
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
      >
        <span>{session.title}</span>
        <small>
          {session.turns.length} 轮 · {formatSessionTime(session.updated_at)}
        </small>
      </button>
      <button
        className="session-delete-button"
        type="button"
        aria-label={`删除会话 ${session.title}`}
        onClick={onDelete}
      >
        <Trash2 size={15} aria-hidden="true" />
      </button>
    </div>
  );
}

function BatchRunRow({
  run,
  selected,
  onSelect,
  onDelete
}: {
  run: BatchRunSummary;
  selected: boolean;
  onSelect: () => void;
  onDelete: () => void;
}) {
  return (
    <div className={selected ? "session-row selected" : "session-row"}>
      <button
        className="session-item"
        type="button"
        aria-pressed={selected}
        onClick={onSelect}
      >
        <span className="session-title-line">
          <span className="session-badge">批量</span>
          {run.title || run.source_label}
        </span>
        <small>
          {batchRunStatusLabel(run.status)} · {batchRunProgressText(run)} ·{" "}
          {formatSessionTime(run.created_at)}
        </small>
      </button>
      <button
        className="session-delete-button"
        type="button"
        aria-label={`删除批量会话 ${run.title || run.source_label}`}
        onClick={onDelete}
      >
        <Trash2 size={15} aria-hidden="true" />
      </button>
    </div>
  );
}
