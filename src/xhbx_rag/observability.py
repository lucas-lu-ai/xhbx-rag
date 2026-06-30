from __future__ import annotations

import json
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Mapping, Protocol, TextIO

from opentelemetry import trace as otel_trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor


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


class CompositeTraceSink:
    def __init__(self, sinks: list[TraceSink]) -> None:
        self.sinks = sinks

    def emit(self, step: str, payload: Mapping[str, Any]) -> None:
        for sink in self.sinks:
            sink.emit(step, payload)

    def close(self) -> None:
        for sink in reversed(self.sinks):
            close = getattr(sink, "close", None)
            if close is not None:
                close()


class StudioTraceSink:
    def __init__(
        self,
        *,
        tracer: Any,
        root_name: str,
        tracer_provider: Any | None = None,
    ) -> None:
        self.tracer = tracer
        self.tracer_provider = tracer_provider
        self.root_span = self.tracer.start_span(root_name)
        self.root_context = otel_trace.set_span_in_context(self.root_span)
        self.closed = False

    def emit(self, step: str, payload: Mapping[str, Any]) -> None:
        if self.closed:
            return
        with self.tracer.start_as_current_span(step, context=self.root_context) as span:
            span.set_attribute("rag.step", step)
            for key, value in payload.items():
                span.set_attribute(f"rag.payload.{key}", _span_attribute_value(value))

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.root_span.end()
        if self.tracer_provider is not None:
            self.tracer_provider.force_flush()
            self.tracer_provider.shutdown()


def create_studio_trace_sink(
    *,
    endpoint: str = "localhost:4317",
    root_name: str,
) -> StudioTraceSink:
    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": "xhbx-rag",
                "service.namespace": "agentscope",
                "service.version": "0.1.0",
            }
        )
    )
    exporter = OTLPSpanExporter(endpoint=endpoint, insecure=True, timeout=5)
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return StudioTraceSink(
        tracer=provider.get_tracer("xhbx_rag"),
        root_name=root_name,
        tracer_provider=provider,
    )


def close_trace(trace: TraceSink | None) -> None:
    if trace is None:
        return
    close = getattr(trace, "close", None)
    if close is not None:
        close()


def emit_trace(
    trace: TraceSink | None,
    step: str,
    payload: Mapping[str, Any],
) -> None:
    if trace is not None:
        trace.emit(step, payload)


def _span_attribute_value(value: Any) -> str | bool | int | float:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float, str)):
        return value
    if value is None:
        return ""
    return json.dumps(value, ensure_ascii=False, default=str)
