from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol, TextIO


class TraceSink(Protocol):
    def emit(self, step: str, payload: Mapping[str, Any]) -> None:
        """Emit one business-level trace event."""


@dataclass(frozen=True)
class TraceEvent:
    step: str
    payload: dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "timestamp": self.timestamp,
            "step": self.step,
            "payload": self.payload,
        }


class MemoryTraceSink:
    def __init__(self) -> None:
        self.events: list[TraceEvent] = []

    def emit(self, step: str, payload: Mapping[str, Any]) -> None:
        self.events.append(TraceEvent(step=step, payload=dict(payload)))


class JsonlTraceSink:
    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = stream or sys.stderr

    def emit(self, step: str, payload: Mapping[str, Any]) -> None:
        event = TraceEvent(step=step, payload=dict(payload))
        self.stream.write(
            json.dumps(event.to_dict(), ensure_ascii=False, default=str) + "\n"
        )
        self.stream.flush()


def emit_trace(
    trace: TraceSink | None,
    step: str,
    payload: Mapping[str, Any],
) -> None:
    if trace is not None:
        trace.emit(step, payload)
