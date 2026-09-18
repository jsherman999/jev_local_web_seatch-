import pytest

from jev_service.source_view import SOURCES, source_excerpt
from jev_service.trace import Trace


def test_trace_pairs_actual_spans_and_records_failure_without_payloads():
    events = []
    trace = Trace(events.append)
    trace.cycle = 2
    with trace.span("decide", "jev_choose", model="test") as outcome:
        outcome["model"] = "resolved-model"
    with pytest.raises(ValueError):
        with trace.span("act", "browser_act"):
            raise ValueError("sensitive payload must not be emitted")
    assert [e["phase"] for e in events] == ["start", "end", "start", "error"]
    assert events[0]["span_id"] == events[1]["span_id"]
    assert events[2]["span_id"] == events[3]["span_id"]
    assert all(e["cycle"] == 2 for e in events)
    assert events[1]["details"]["duration_ms"] >= 0
    assert "sensitive" not in str(events)
    assert events[3]["details"]["error_type"] == "ValueError"


def test_disabled_trace_does_not_emit_or_change_result():
    events = []
    with Trace(events.append, enabled=False).span("read", "browser_read"):
        pass
    assert events == []


def test_source_allowlist_resolves_real_function_definitions():
    for key in SOURCES:
        result = source_excerpt(key)
        assert result["code"].lstrip().startswith("def ")
        assert result["line"] > 0
    with pytest.raises(KeyError):
        source_excerpt("../../.env")
