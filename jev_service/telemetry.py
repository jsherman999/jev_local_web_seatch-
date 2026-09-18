"""Response-based accounting. Missing usage is unknown, never silently zero."""

import math


def number(value):
    return value if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


class UsageMeter:
    def __init__(self):
        self.calls = []

    def begin(self, provider, model):
        call = {"provider": provider, "model": model, "pending": True}
        self.calls.append(call)
        return call

    def finish(self, call, response):
        usage = response.get("usage") or {}
        call.update(
            pending=False,
            model=response.get("model", call["model"]),
            input_tokens=number(usage.get("input_tokens", usage.get("prompt_tokens"))),
            output_tokens=number(usage.get("output_tokens", usage.get("completion_tokens"))),
            cached_tokens=number((usage.get("prompt_tokens_details") or
                                  usage.get("input_tokens_details") or {}).get(
                                      "cached_tokens", usage.get("prompt_cache_hit_tokens", 0))),
            reported_cost_usd=number(usage.get("cost")),
        )

    def summary(self):
        groups = {}
        for call in self.calls:
            key = (call["provider"], call["model"])
            group = groups.setdefault(key, {
                "provider": key[0], "model": key[1], "calls": 0,
                "input_tokens": 0, "output_tokens": 0, "cached_tokens": 0,
                "reported_cost_usd": 0, "tokens_complete": True, "cost_complete": True,
            })
            group["calls"] += 1
            for field in ("input_tokens", "output_tokens", "cached_tokens", "reported_cost_usd"):
                value = call.get(field)
                if value is None:
                    group["cost_complete" if field == "reported_cost_usd" else "tokens_complete"] = False
                else:
                    group[field] += value
        return {"calls": len(self.calls), "groups": list(groups.values()),
                "pending_calls": sum(c["pending"] for c in self.calls)}
