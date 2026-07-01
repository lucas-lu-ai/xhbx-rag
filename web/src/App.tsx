import { FileSearch, MessageSquareText, SendHorizontal } from "lucide-react";

export function App() {
  return (
    <main className="app-shell">
      <header className="app-header">
        <div>
          <p className="eyebrow">xhbx-rag Web</p>
          <h1>知识库问答工作台</h1>
        </div>
        <span className="status-pill">本地服务</span>
      </header>

      <section className="workspace" aria-label="知识库问答">
        <div className="question-pane">
          <div className="pane-heading">
            <MessageSquareText size={20} aria-hidden="true" />
            <h2>提问</h2>
          </div>
          <textarea
            aria-label="输入问题"
            placeholder="输入一个关于 data 目录知识库的问题"
            rows={8}
          />
          <button type="button" className="primary-action" disabled>
            <SendHorizontal size={18} aria-hidden="true" />
            等待连接
          </button>
        </div>

        <aside className="source-pane" aria-label="溯源">
          <div className="pane-heading">
            <FileSearch size={20} aria-hidden="true" />
            <h2>溯源</h2>
          </div>
          <p className="empty-state">完成一次问答后，引用片段会显示在这里。</p>
        </aside>
      </section>
    </main>
  );
}
