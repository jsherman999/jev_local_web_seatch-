import pytest

from jev_service.browser_adapter import filter_select_actions, install, selectable


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
