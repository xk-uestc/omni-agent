from __future__ import annotations

import pytest

from backend.session import ConversationStore


def test_session_store_is_bounded_and_expires():
    now = [0.0]
    store = ConversationStore(max_sessions=1, max_turns=2, ttl_seconds=10, clock=lambda: now[0])
    store.remember("a", question="q1", effective_question="q1")
    store.remember("a", question="q2", effective_question="q2")
    store.remember("a", question="q3", effective_question="q3")
    assert [turn.question for turn in store.context("a")] == ["q2", "q3"]
    now[0] = 11
    assert store.context("a") == ()


def test_session_store_validates_ids():
    with pytest.raises(ValueError):
        ConversationStore.validate_id("bad id")
    assert ConversationStore.validate_id("ict8-a_1") == "ict8-a_1"


def test_sqlite_session_store_survives_new_store_instance(tmp_path):
    path = tmp_path / "sessions.sqlite"
    first = ConversationStore(storage_path=path, max_turns=2, ttl_seconds=60, clock=lambda: 10.0)
    first.remember("shared", question="原始问题", effective_question="原始问题")
    first.remember("shared", question="追问", effective_question="原始问题\n用户补充问题：追问")

    second = ConversationStore(storage_path=path, max_turns=2, ttl_seconds=60, clock=lambda: 11.0)
    turns = second.context("shared")
    assert [turn.question for turn in turns] == ["原始问题", "追问"]
    assert turns[-1].effective_question.endswith("用户补充问题：追问")


def test_sqlite_session_store_expires_by_ttl(tmp_path):
    path = tmp_path / "sessions.sqlite"
    now = [0.0]
    first = ConversationStore(storage_path=path, ttl_seconds=5, clock=lambda: now[0])
    first.remember("expiring", question="q", effective_question="q")
    now[0] = 6.0
    assert ConversationStore(storage_path=path, ttl_seconds=5, clock=lambda: now[0]).context("expiring") == ()


@pytest.mark.parametrize('persistent', [False, True])
def test_session_state_snapshots_cannot_be_modified_by_callers(tmp_path, persistent):
    store = ConversationStore(storage_path=tmp_path / 'copy.sqlite' if persistent else None)
    original = {'filters': [{'value': '华东'}]}
    store.remember('copy', question='q', effective_question='q', state=original)
    original['filters'][0]['value'] = '华南'
    read = store.context('copy')
    assert read[0].state['filters'][0]['value'] == '华东'
    read[0].state['filters'][0]['value'] = '华北'
    assert store.context('copy')[0].state['filters'][0]['value'] == '华东'


@pytest.mark.parametrize('persistent', [False, True])
def test_complete_turns_are_serialized_without_lost_updates(tmp_path, persistent):
    from concurrent.futures import ThreadPoolExecutor
    store = ConversationStore(max_turns=60,
        storage_path=tmp_path / 'turns.sqlite' if persistent else None)
    def increment(_):
        with store.turn('same'):
            history = store.context('same')
            value = history[-1].state['value'] + 1 if history else 1
            with store.turn('same'):  # Clarification-to-agent nested entry.
                store.remember('same', question='q', effective_question='q', state={'value': value})
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(increment, range(40)))
    assert [turn.state['value'] for turn in store.context('same')] == list(range(1, 41))
    assert store._turn_locks == {}


def test_other_sessions_do_not_wait_for_active_turn_and_failures_release_lock():
    from threading import Event, Thread
    store = ConversationStore()
    completed = Event()
    def other_session():
        with store.turn('other'):
            completed.set()
    with store.turn('first'):
        thread = Thread(target=other_session)
        thread.start()
        assert completed.wait(2)
    thread.join(2)
    with pytest.raises(RuntimeError):
        with store.turn('failed'):
            raise RuntimeError('intentional')
    assert store._turn_locks == {}
