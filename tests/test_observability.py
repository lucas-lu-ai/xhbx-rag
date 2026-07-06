import json
from datetime import datetime

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from xhbx_rag.observability import (
    CompositeTraceSink,
    MemoryTraceSink,
    StudioTraceSink,
    TraceEvent,
)


def test_trace_event_timestamp_is_local_time_without_extra_fields() -> None:
    before = datetime.now().replace(microsecond=0)

    event = TraceEvent(step="test.step", payload={})

    after = datetime.now().replace(microsecond=0)
    parsed = datetime.strptime(event.timestamp, "%Y-%m-%d %H:%M:%S")
    assert before <= parsed <= after


def test_studio_trace_sink_exports_step_spans_under_root_span() -> None:
    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    sink = StudioTraceSink(
        tracer=provider.get_tracer("xhbx_rag.tests"),
        root_name="xhbx-rag.search",
        tracer_provider=provider,
    )

    sink.emit(
        "search.query_understood",
        {
            "rewritten_query": "客户抗拒谈保险时如何开场",
            "filters": {"chunk_types": ["script"]},
        },
    )
    sink.close()

    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["search.query_understood", "xhbx-rag.search"]
    assert spans[0].parent.span_id == spans[1].context.span_id
    assert spans[0].attributes["rag.step"] == "search.query_understood"
    assert spans[0].attributes["rag.payload.rewritten_query"] == "客户抗拒谈保险时如何开场"
    assert json.loads(spans[0].attributes["rag.payload.filters"]) == {
        "chunk_types": ["script"]
    }


def test_composite_trace_sink_emits_and_closes_all_sinks() -> None:
    memory = MemoryTraceSink()
    closed: list[str] = []

    class _CloseableSink:
        def emit(self, step, payload):
            memory.emit(step, payload)

        def close(self):
            closed.append("closed")

    sink = CompositeTraceSink([memory, _CloseableSink()])

    sink.emit("search.completed", {"result_count": 1})
    sink.close()

    assert [event.step for event in memory.events] == [
        "search.completed",
        "search.completed",
    ]
    assert closed == ["closed"]
