from jev_service.telemetry import UsageMeter


def test_usage_counts_stale_and_terminal_decisions_without_double_counting():
    meter = UsageMeter()
    for _ in range(3):
        call = meter.begin("typesafe", "jev-latest")
        meter.finish(call, {"model": "jev-1.13.0", "usage": {"input_tokens": 100, "output_tokens": 20}})
    call = meter.begin("text", "gpt-5.4")
    meter.finish(call, {"usage": {"prompt_tokens": 50, "completion_tokens": 10,
                                  "prompt_tokens_details": {"cached_tokens": 20}, "cost": .001}})
    summary = meter.summary()
    assert summary["calls"] == 4
    jev, text = summary["groups"]
    assert jev["input_tokens"] == 300 and jev["output_tokens"] == 60
    assert jev["tokens_complete"] and not jev["cost_complete"]
    assert text["cached_tokens"] == 20 and text["reported_cost_usd"] == .001
    assert text["cost_complete"]


def test_inflight_and_missing_usage_are_unknown_not_free():
    meter = UsageMeter()
    call = meter.begin("text", "gpt-5.4")
    assert meter.summary()["pending_calls"] == 1
    assert not meter.summary()["groups"][0]["cost_complete"]
    meter.finish(call, {"usage": {"prompt_tokens": float("nan"), "completion_tokens": -1}})
    assert meter.summary()["pending_calls"] == 0
    assert not meter.summary()["groups"][0]["tokens_complete"]
    assert not meter.summary()["groups"][0]["cost_complete"]


def test_deepseek_cache_hits_are_counted_for_cost_estimates():
    meter = UsageMeter()
    call = meter.begin('text', 'deepseek-chat')
    meter.finish(call, {'usage': {'prompt_tokens': 100, 'completion_tokens': 20,
                                 'prompt_cache_hit_tokens': 80}})
    assert meter.summary()['groups'][0]['cached_tokens'] == 80
