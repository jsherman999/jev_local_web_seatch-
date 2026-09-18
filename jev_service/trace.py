"""Small, payload-free execution events for the teaching diagram."""

import time
from contextlib import contextmanager


class Trace:
    def __init__(self, emit, enabled=True):
        self.emit = emit
        self.enabled = enabled
        self.started = time.monotonic()
        self.sequence = 0
        self.cycle = 0

    def record(self, node, source, phase, span_id=None, **details):
        if not self.enabled:
            return None
        self.sequence += 1
        self.emit({"event": "trace", "seq": self.sequence, "node": node,
                   "source": source, "phase": phase, "span_id": span_id or self.sequence,
                   "at_ms": round((time.monotonic() - self.started) * 1000),
                   "cycle": self.cycle, "details": details})
        return self.sequence

    @contextmanager
    def span(self, node, source, **details):
        span_id = self.record(node, source, "start", **details)
        started = time.monotonic()
        outcome = {}
        try:
            yield outcome
        except BaseException as exc:
            self.record(node, source, "error", span_id, error_type=type(exc).__name__,
                        duration_ms=round((time.monotonic() - started) * 1000))
            raise
        else:
            self.record(node, source, "end", span_id,
                        duration_ms=round((time.monotonic() - started) * 1000), **outcome)

    def install_browser_hooks(self, baseline=False, isolated=True):
        from jev_ultrafast import agent

        original_browser = agent.Browser
        trace = self

        class ObservedBrowser(original_browser):
            def __init__(self, url):
                with trace.span("setup", "browser_setup" if isolated else "browser_open"):
                    super().__init__(url)

            def observe(self, *args, **kwargs):
                with trace.span("read", "browser_read") as outcome:
                    page = super().observe(*args, **kwargs)
                    outcome["available_actions"] = len(page["actions"])
                    return page

            def act(self, action, page, text=None):
                with trace.span("act", "browser_act", kind=action["kind"], target=action["id"]):
                    return super().act(action, page, text=text)

        agent.Browser = ObservedBrowser
        original_choose = agent.choose

        def choose(*args, **kwargs):
            decision = original_choose(*args, **kwargs)
            self.record("route", "baseline_choose" if baseline else "jev_choose", "instant",
                        operation=decision["operation"], target=decision.get("target"),
                        **({"confidence": decision.get("confidence")}
                           if not baseline else {}))
            return decision

        agent.choose = choose
