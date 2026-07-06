import { BrainCircuit, ChevronDown, ChevronRight, LoaderCircle } from "lucide-react";
import { useEffect, useRef, useState } from "react";

type ThinkingProcessProps = {
  reasoning: string;
  live: boolean;
};

// 思考进行中自动展开并跟随滚动，思考结束自动折叠；此后由用户手动切换。
export function ThinkingProcess({ reasoning, live }: ThinkingProcessProps) {
  const [expanded, setExpanded] = useState(live);
  const previousLive = useRef(live);
  const contentRef = useRef<HTMLPreElement | null>(null);

  useEffect(() => {
    if (previousLive.current !== live) {
      setExpanded(live);
      previousLive.current = live;
    }
  }, [live]);

  useEffect(() => {
    if (live && expanded && contentRef.current) {
      contentRef.current.scrollTop = contentRef.current.scrollHeight;
    }
  }, [reasoning, live, expanded]);

  if (!reasoning) {
    return null;
  }

  return (
    <div className="thinking-block">
      <button
        type="button"
        className="thinking-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? (
          <ChevronDown size={14} aria-hidden="true" />
        ) : (
          <ChevronRight size={14} aria-hidden="true" />
        )}
        <BrainCircuit size={15} aria-hidden="true" />
        思考过程
        {live && <LoaderCircle className="spin" size={13} aria-hidden="true" />}
      </button>
      {expanded && (
        <pre className="thinking-content" ref={contentRef}>
          {reasoning}
        </pre>
      )}
    </div>
  );
}
