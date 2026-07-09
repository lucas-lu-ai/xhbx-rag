import Markdown from "react-markdown";
import remarkGfm from "remark-gfm";

type MarkdownMessageProps = {
  content: string;
  className?: string;
};

// 外链在新标签打开，并断开 opener 引用，避免反向 tabnabbing。
function MarkdownLink({
  href,
  children
}: {
  href?: string;
  children?: React.ReactNode;
}) {
  return (
    <a href={href} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}

// 用 react-markdown 渲染 LLM 回答，默认不解析裸 HTML（天然防 XSS），
// 危险协议链接由 react-markdown 内置 urlTransform 过滤。
export function MarkdownMessage({ content, className }: MarkdownMessageProps) {
  if (!content) {
    return null;
  }

  const rootClassName = className ? `markdown-body ${className}` : "markdown-body";

  return (
    <div className={rootClassName}>
      <Markdown remarkPlugins={[remarkGfm]} components={{ a: MarkdownLink }}>
        {content}
      </Markdown>
    </div>
  );
}
