import { FileUp, MessageSquareText } from "lucide-react";

import type { WorkspaceLocation } from "../workspaceLocation";

type WorkspaceNavProps = {
  currentView: WorkspaceLocation["view"];
  onNavigate: (view: WorkspaceLocation["view"]) => void;
};

export function WorkspaceNav({ currentView, onNavigate }: WorkspaceNavProps) {
  return (
    <nav className="workspace-nav" aria-label="工作台导航">
      <button
        type="button"
        aria-current={currentView === "chat" ? "page" : undefined}
        className={currentView === "chat" ? "workspace-nav-item active" : "workspace-nav-item"}
        onClick={() => onNavigate("chat")}
      >
        <MessageSquareText size={18} aria-hidden="true" />
        知识问答
      </button>
      <button
        type="button"
        aria-current={currentView === "ingestion" ? "page" : undefined}
        className={
          currentView === "ingestion" ? "workspace-nav-item active" : "workspace-nav-item"
        }
        onClick={() => onNavigate("ingestion")}
      >
        <FileUp size={18} aria-hidden="true" />
        文档入库
      </button>
    </nav>
  );
}
