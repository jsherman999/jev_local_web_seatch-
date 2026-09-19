import pytest

from jev_service.browser_adapter import filter_select_actions, install, observe_ready, selectable


@pytest.mark.parametrize('screenshots', [False, True])
def test_empty_document_waits_for_content_without_replaying_input(monkeypatch, screenshots):
    empty = {"url": "https://example.com", "text": "", "actions": [{"kind": "wait"}]}
    ready = {**empty, "text": "Search products"}
    pages = iter([empty, empty, ready])
    calls, events = [], []
    monkeypatch.setattr('jev_service.browser_adapter.time.sleep', lambda _: None)

    def observe(**kwargs):
        calls.append(kwargs)
        return next(pages)

    assert observe_ready(None, observe, events.append, screenshot=screenshots) == ready
    assert calls == [{"screenshot": screenshots}] * 3
    assert [e['page_diagnostic']['status'] for e in events] == ['waiting', 'ready']


def test_permanently_empty_page_reports_failed_readiness_check(monkeypatch):
    from types import SimpleNamespace

    clock = iter([0, 11, 11])
    monkeypatch.setattr('jev_service.browser_adapter.time.monotonic', lambda: next(clock))
    browser = SimpleNamespace(evaluate=lambda _: 'complete')
    events = []
    with pytest.raises(RuntimeError, match='Page remained empty'):
        observe_ready(browser, lambda: {"url": "https://example.com", "text": "", "actions": []},
                      events.append)
    assert events[-1]['page_diagnostic']['failed_check'] == 'readable_content'
    assert events[-1]['page_diagnostic']['ready_state'] == 'complete'


def test_usable_page_does_not_add_a_delay(monkeypatch):
    def unexpected_wait(_):
        pytest.fail('Usable pages should not wait')
    monkeypatch.setattr('jev_service.browser_adapter.time.sleep', unexpected_wait)
    page = {"text": "", "actions": [{"kind": "fill"}]}
    assert observe_ready(None, lambda: page, lambda _: None) is page


def test_covered_native_options_are_removed_but_visible_button_is_kept():
    actions = [
        {"id": "e1", "node": 1, "kind": "select", "value": "asc"},
        {"id": "e2", "node": 1, "kind": "select", "value": "desc"},
        {"id": "e3", "node": 2, "kind": "click", "label": "Sort by: Featured"},
        {"id": "e4", "node": 3, "kind": "select", "value": "normal"},
    ]
    page = {"actions": actions, "marker": ["original"], "guards": {"2": ["guard"]}}
    filtered = filter_select_actions(page, [
        {"node": 1, "diagnostic": {"failed_check": "hit_target"}},
        {"node": 3, "diagnostic": {"failed_check": None}},
    ])
    assert [a["id"] for a in filtered["actions"]] == ["e3", "e4"]
    assert filtered["marker"] is page["marker"]
    assert filtered["guards"] is page["guards"]
    assert len(page["actions"]) == 4


@pytest.fixture
def adapter(monkeypatch):
    from jev_ultrafast import agent

    class FakeBrowser:
        executed = 0
        diagnostic = {"failed_check": None}

        def fresh(self, *_):
            return True

        def evaluate(self, *_):
            return self.diagnostic

        def act(self, *_args, **_kwargs):
            self.executed += 1
            return {"executed": "e1"}

    monkeypatch.setattr(agent, "Browser", FakeBrowser)
    monkeypatch.setattr(agent, "Agent", agent.Agent)
    events = []
    install(events.append)
    return agent.Browser(), events


def test_covered_preflight_is_safe_to_reobserve_without_mutation(adapter):
    from jev_ultrafast.browser import StalePage

    browser, events = adapter
    browser.diagnostic = {"failed_check": "hit_target"}
    with pytest.raises(StalePage, match="hit_target"):
        browser.act({"kind": "select", "node": 1}, {})
    assert browser.executed == 0
    assert events[-1]["browser_diagnostic"]["failed_check"] == "hit_target"


def test_normal_select_keeps_upstream_execution(adapter):
    browser, _ = adapter
    assert browser.act({"kind": "select", "node": 1}, {}) == {"executed": "e1"}
    assert browser.executed == 1


def test_only_a_verified_decoration_can_relax_the_mouse_hit_check():
    diagnostic = {"failed_check": "hit_target", "decorated_select": True,
                  "checks": {"hit_target": False, "enabled": True, "visible": True,
                             "in_viewport": True, "native_select": True, "enabled_option": True}}
    assert selectable(diagnostic)
    assert not selectable({**diagnostic, "decorated_select": False})
    assert not selectable({**diagnostic, "checks": {**diagnostic["checks"], "enabled_option": False}})
    assert not selectable({**diagnostic, "failed_check": "visible"})
    page = {"actions": [{"kind": "select", "node": 1}]}
    assert filter_select_actions(page, [{"node": 1, "diagnostic": diagnostic}])["actions"]


def test_decorated_select_executes_once_without_falling_back(adapter):
    browser, _ = adapter
    responses = iter([{"failed_check": "hit_target", "decorated_select": True, "checks": {}},
                      {"executed": True}])
    browser.evaluate = lambda *_: next(responses)
    action = {"id": "e1", "kind": "select", "node": 1}
    assert browser.act(action, {}) == {"executed": "e1"}
    assert browser.executed == 0
    assert browser.after_input == action


def test_ambiguous_selection_is_not_retried(adapter):
    from jev_ultrafast.browser import StalePage

    browser, _ = adapter
    attempts = []

    def evaluate(*_):
        attempts.append(True)
        if len(attempts) == 1:
            return {"failed_check": "hit_target", "decorated_select": True, "checks": {}}
        raise StalePage("Document navigated during input")

    browser.evaluate = evaluate
    with pytest.raises(RuntimeError, match="not replayed"):
        browser.act({"id": "e1", "kind": "select", "node": 1}, {})
    assert len(attempts) == 2
    assert browser.executed == 0


def test_typing_uses_field_guard_instead_of_changing_banner(adapter):
    browser, events = adapter
    page = {"page_key": ['document', 'url', 'form-values'], "guards": {'1': ['search', 'unchanged']}}
    action = {"kind": "fill", "node": 1}
    browser.evaluate = lambda _: [page['page_key'], page['guards']['1']]
    assert browser.fresh(page, action)
    browser.fill_preflight = (page, action)
    assert browser.fresh(page)
    # A changed document, form value or field guard must still reject input.
    for current in [[['new-document'], page['guards']['1']], [page['page_key'], ['changed']], None]:
        browser.evaluate = lambda _, current=current: current
        assert not browser.fresh(page, action)
    assert events[-1]['action_diagnostic']['failed_check'] == 'field_freshness'


def test_field_scope_is_active_only_during_selected_fill(monkeypatch):
    from types import SimpleNamespace

    from jev_ultrafast import agent

    page = {"actions": [{"id": "e1", "kind": "fill", "node": 1}]}

    class FakeAgent:
        def command(self, name, body=None):
            assert self.browser.fill_preflight == (page, page['actions'][0])
            raise ValueError('test failure')

    monkeypatch.setattr(agent, 'Agent', FakeAgent)
    monkeypatch.setattr(agent, 'Browser', agent.Browser)
    install(lambda _: None)
    instance = agent.Agent()
    instance.browser = SimpleNamespace()
    instance.state = {"page": page, "decision": {"choice": "e1"}}
    with pytest.raises(ValueError, match='test failure'):
        instance.command('act')
    assert instance.browser.fill_preflight is None
