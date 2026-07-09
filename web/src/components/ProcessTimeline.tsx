import { Activity, CheckCircle2, ChevronDown, ChevronRight } from "lucide-react";
import { useEffect, useRef, useState } from "react";

import { formatProcessPayload } from "../format";
import type { AnswerProcessStep } from "../types";

type ProcessTimelineProps = {
  active: boolean;
  steps: AnswerProcessStep[];
};

// 运行中自动展开跟随进度，回答完成自动折叠；此后由用户手动切换。
export function ProcessTimeline({ active, steps }: ProcessTimelineProps) {
  const [expanded, setExpanded] = useState(active);
  const previousActive = useRef(active);

  useEffect(() => {
    if (previousActive.current !== active) {
      setExpanded(active);
      previousActive.current = active;
    }
  }, [active]);

  if (steps.length === 0 && !active) {
    return null;
  }

  return (
    <section className="process-panel" aria-label="处理过程">
      <button
        type="button"
        className="process-heading process-toggle"
        aria-expanded={expanded}
        onClick={() => setExpanded((value) => !value)}
      >
        {expanded ? (
          <ChevronDown size={14} aria-hidden="true" />
        ) : (
          <ChevronRight size={14} aria-hidden="true" />
        )}
        <Activity size={16} aria-hidden="true" />
        <strong>处理过程</strong>
        {active && <span>运行中</span>}
      </button>
      {expanded &&
        (steps.length === 0 ? (
          <p className="meta-text">正在连接问答服务...</p>
        ) : (
          <ol className="process-list">
            {steps.map((step, index) => (
              <li key={`${step.step}-${index}`}>
                <CheckCircle2 size={15} aria-hidden="true" />
                <div>
                  <span>{step.message}</span>
                  {formatProcessPayload(step.payload) && (
                    <small>{formatProcessPayload(step.payload)}</small>
                  )}
                </div>
              </li>
            ))}
          </ol>
        ))}
    </section>
  );
}
