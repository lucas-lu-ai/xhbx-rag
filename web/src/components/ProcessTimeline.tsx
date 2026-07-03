import { Activity, CheckCircle2 } from "lucide-react";

import { formatProcessPayload } from "../format";
import type { AnswerProcessStep } from "../types";

type ProcessTimelineProps = {
  active: boolean;
  steps: AnswerProcessStep[];
};

export function ProcessTimeline({ active, steps }: ProcessTimelineProps) {
  if (steps.length === 0 && !active) {
    return null;
  }

  return (
    <section className="process-panel" aria-label="处理过程">
      <div className="process-heading">
        <Activity size={16} aria-hidden="true" />
        <strong>处理过程</strong>
        {active && <span>运行中</span>}
      </div>
      {steps.length === 0 ? (
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
      )}
    </section>
  );
}
