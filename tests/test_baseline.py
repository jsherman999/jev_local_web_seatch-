import json

import pytest

from jev_service.baseline import install, parse_action, response_options


@pytest.mark.parametrize('model_id', ['anthropic/claude-haiku-4.5', '~anthropic/claude-haiku-latest'])
def test_haiku_uses_strict_schema_and_supporting_route(policy, monkeypatch, model_id):
    from jev_ultrafast import model

    agent, _ = policy
    monkeypatch.setenv('TEXT_MODEL_BASE_URL', 'https://openrouter.ai/api/v1')
    monkeypatch.setenv('TEXT_MODEL', model_id)
    def post(url, key, body):
        assert body['model'] == model_id
        assert body['provider'] == {'require_parameters': True}
        schema = body['response_format']['json_schema']
        assert schema['strict'] is True
        assert schema['schema']['additionalProperties'] is False
        assert set(schema['schema']['required']) == {'operation', 'target', 'text'}
        assert 'TYPE_TEXT' in schema['schema']['properties']['operation']['enum']
        # Even a schema-valid provider response cannot target unobserved nodes.
        return completion('{"operation":"TYPE_TEXT","target":"999","text":"laptop"}')
    monkeypatch.setattr(model, 'post_json', post)
    with pytest.raises(ValueError, match='unobserved target'):
        agent.choose(PAGE, 'Search laptop', [])


def test_other_models_keep_existing_response_contract():
    assert response_options('https://openrouter.ai/api/v1', 'deepseek/deepseek-v4.1-flash', []) == {
        'response_format': {'type': 'json_object'}}


def completion(content, reason='stop', **message):
    return {'choices': [{'finish_reason': reason, 'message': {'content': content, **message}}]}


def test_fenced_json_still_uses_action_target_validation(policy, monkeypatch):
    from jev_ultrafast import model

    agent, _ = policy
    monkeypatch.setattr(model, 'post_json', lambda *_: completion(
        '```json\n{"operation":"TYPE_TEXT","target":"999","text":"query"}\n```'))
    with pytest.raises(ValueError, match='unobserved target'):
        agent.choose(PAGE, 'Search', [])


@pytest.mark.parametrize('fence', ['', 'json', 'JSON'])
def test_accepts_only_complete_fenced_action(fence):
    action = {'operation': 'DONE', 'target': None, 'text': None}
    assert parse_action(completion(f'```{fence}\n{json.dumps(action)}\n```')) == action


@pytest.mark.parametrize('content', ['Here is JSON: {"operation":"DONE"}', '[]', 'null',
                                    '{"operation":[]}', '{"operation":"DONE","target":42.5}',
                                    '{"operation":"DONE"}{"operation":"CLICK"}'])
def test_rejects_prose_multiple_objects_and_invalid_structures(content):
    with pytest.raises(ValueError, match='no action executed'):
        parse_action(completion(content))


def test_null_response_and_token_limit_have_specific_diagnostics():
    events = []
    with pytest.raises(ValueError, match='no action text'):
        parse_action(completion(None), events.append)
    assert events[-1]['response_diagnostic']['content_present'] is False
    for content in [None, '{"operation":"DONE"}']:
        with pytest.raises(ValueError, match='output token limit'):
            parse_action(completion(content, 'length'), events.append)
    assert events[-1]['response_diagnostic']['finish_reason'] == 'length'
    with pytest.raises(ValueError, match='refused or filtered'):
        parse_action(completion(None, refusal='Private refusal text'), events.append)
    assert 'Private refusal text' not in json.dumps(events)


def test_integer_target_and_extra_metadata_are_safe(policy):
    agent, response = policy
    response({'operation': 'TYPE_TEXT', 'target': 1, 'text': 'laptop',
              'explanation': 'extra metadata', 'javascript': 'must never execute'})
    assert agent.choose(PAGE, 'Search laptop', [])['choice'] == 'fill-1'
    response({'operation': 'TYPE_TEXT', 'target': 999, 'text': 'laptop'})
    with pytest.raises(ValueError, match='unobserved target'):
        agent.choose(PAGE, 'Search laptop', [])


@pytest.mark.parametrize('target', [True, False, 1.0, -1, 0, [], {}])
def test_ambiguous_targets_fail_with_specific_diagnostic(target):
    events = []
    with pytest.raises(ValueError, match='invalid target type'):
        parse_action(completion(json.dumps({'operation': 'CLICK', 'target': target})), events.append)
    assert events[-1]['action_validation']['failed_field'] == 'target'


def test_normalization_diagnostics_do_not_store_values():
    events = []
    result = parse_action(completion(json.dumps({'operation': 'CLICK', 'target': 1,
                                                'explanation': 'private content'})), events.append)
    assert result == {'operation': 'CLICK', 'target': '1', 'text': None}
    assert events[-1]['action_validation']['ignored_extra_fields'] == 1
    assert events[-1]['action_validation']['normalized_integer_target'] is True
    assert 'private content' not in json.dumps(events)


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


def test_nemo_schema_only_offers_observed_targets():
    opts = response_options('https://openrouter.ai/api/v1', 'mistralai/mistral-nemo',
                            ['CLICK', 'DONE'], {'CLICK': {'1': {}, '2': {}}})
    assert opts['response_format']['json_schema']['schema']['properties']['target']['enum'] == [None, '1', '2']


def test_multiple_or_unknown_tool_calls_are_never_executed():
    for calls in [[{'type': 'function', 'function': {'name': 'other', 'arguments': '{}'}}], [{}, {}]]:
        with pytest.raises(ValueError, match='exactly one browser_action'):
            parse_action({'choices': [{'message': {'tool_calls': calls}}]})
