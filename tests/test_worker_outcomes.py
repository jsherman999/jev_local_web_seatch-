from jev_service.worker import blocked_reason


def test_model_blocked_is_distinct_from_decision_budget():
    state = {"status": "blocked", "decisions": [{"choice": "BLOCKED"}], "history": []}
    assert 'chose BLOCKED after 1 decision(s)' in blocked_reason(state, 1, 15)
    state['status'] = 'ready'
    assert 'Decision limit reached (15/15)' in blocked_reason(state, 15, 15)


def test_no_progress_is_distinct_from_model_blocked():
    state = {"status": "blocked", "decisions": [{"choice": "e1"}], "history": [{}, {}, {}]}
    assert 'no page progress' in blocked_reason(state, 3, 15)
