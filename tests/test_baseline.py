import json

import pytest

from jev_service.baseline import install


@pytest.fixture
def policy(monkeypatch):
    from jev_ultrafast import agent, model

    monkeypatch.setattr(agent, "choose", agent.choose)
    monkeypatch.setattr(agent, "field_text", agent.field_text)
    monkeypatch.setenv("TEXT_MODEL_API_KEY", "offline-test")
    install()

    def response(output):
        monkeypatch.setattr(model, "post_json", lambda *_: {
            "choices": [{"message": {"content": json.dumps(output)}}], "usage": {}, "model": "test",
        })
    return agent, response


PAGE = {"url": "https://example.com", "title": "Search", "text": "Search products", "actions": [
    {"id": "fill-1", "kind": "fill", "node": 1, "label": "Search products", "role": "textbox", "value": ""},
]}


def test_llm_can_only_choose_observed_targets(policy):
    agent, response = policy
    response({"operation": "TYPE_TEXT", "target": "999", "text": "backpack"})
    with pytest.raises(ValueError, match="unobserved target"):
        agent.choose(PAGE, "Search backpack", [])
    response({"operation": "EXECUTE_JAVASCRIPT", "target": None, "text": None})
    with pytest.raises(ValueError, match="unsupported operation"):
        agent.choose(PAGE, "Search backpack", [])


def test_text_comes_from_same_llm_decision_and_matching_context(policy):
    from jev_ultrafast.model import field_context

    agent, response = policy
    response({"operation": "TYPE_TEXT", "target": "1", "text": "backpack"})
    decision = agent.choose(PAGE, "Search backpack", [])
    assert decision["choice"] == "fill-1"
    context = field_context("Search backpack", PAGE["actions"][0], PAGE, [])
    assert agent.field_text(context)[0] == "backpack"
    with pytest.raises(ValueError, match="context changed"):
        agent.field_text({**context, "goal": "different"})
