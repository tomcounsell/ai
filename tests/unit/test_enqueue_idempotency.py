"""Single-winner session enqueue, and the reflection key that uses it (#3183).

A reflection tick mints its session id from the wall clock, so two ticks
inside one due window used to enqueue two sessions for the same work. The
idempotency key closes that: the first caller binds it to an
``agent_session_id`` and creates the row, and every later caller returns the
bound id having created nothing.

The test that matters most is
``test_created_row_carries_the_bound_id``. A binding passed to
``AgentSession`` under ``agent_session_id=`` is silently dropped (the
constructor pops it as the AutoKeyField's read name) and the row gets a fresh
id, leaving the key pointing at a session that does not exist. A test that
only asserted "one row exists" would pass under that broken form.
"""

import pytest

from agent import enqueue_idempotency


class FakeRedis:
    def __init__(self):
        self.store: dict[str, str] = {}

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self.store:
            return None
        self.store[key] = value
        return True

    def get(self, key):
        return self.store.get(key)

    def delete(self, key):
        return self.store.pop(key, None) is not None


@pytest.fixture
def redis(monkeypatch):
    fake = FakeRedis()
    monkeypatch.setattr("utils.redis_client.text_redis", lambda: fake)
    return fake


class TestBind:
    def test_the_first_caller_wins(self, redis):
        session_id, won = enqueue_idempotency.bind("reflection:x:60")
        assert won is True
        assert redis.store["enqueue:idem:reflection:x:60"] == session_id

    def test_the_second_caller_gets_the_bound_id(self, redis):
        first, _ = enqueue_idempotency.bind("reflection:x:60")
        second, won = enqueue_idempotency.bind("reflection:x:60")
        assert won is False
        assert second == first

    def test_different_windows_bind_separately(self, redis):
        first, _ = enqueue_idempotency.bind("reflection:x:60")
        second, won = enqueue_idempotency.bind("reflection:x:120")
        assert won is True
        assert second != first

    def test_the_minted_id_matches_popotos_autokey_shape(self, redis):
        """A readable id raises ModelException from AgentSession.__init__.

        AutoFieldMixin pins STRATEGY_LENGTHS = {"uuid4": 32, ...} and checks
        the length, so a hand-rolled key does not quietly fall back to a
        generated one.
        """
        session_id, _ = enqueue_idempotency.bind("reflection:x:60")
        assert len(session_id) == 32
        int(session_id, 16)  # raises unless it is hex

    def test_an_expired_key_is_retaken(self, redis, monkeypatch):
        """The key vanishing between the SET NX and the read is not a crash."""
        calls = {"n": 0}
        real_set = redis.set

        def _set(key, value, nx=False, ex=None):
            calls["n"] += 1
            if calls["n"] == 1:
                return None  # pretend someone holds it
            return real_set(key, value, nx=nx, ex=ex)

        monkeypatch.setattr(redis, "set", _set)
        session_id, won = enqueue_idempotency.bind("reflection:x:60")
        assert won is True
        assert redis.store["enqueue:idem:reflection:x:60"] == session_id

    def test_release_drops_the_binding(self, redis):
        enqueue_idempotency.bind("reflection:x:60")
        enqueue_idempotency.release("reflection:x:60")
        assert "enqueue:idem:reflection:x:60" not in redis.store


class TestSeamBinding:
    @pytest.mark.asyncio
    async def test_created_row_carries_the_bound_id(self, redis, monkeypatch):
        """The row's key must equal the value stored under the key.

        Passing the preallocated id as ``agent_session_id=`` instead of ``id=``
        drops it WITHOUT raising, and the binding then points at nothing.
        """
        from agent import agent_session_queue as queue

        created = {}

        async def _async_create(**kwargs):
            created.update(kwargs)

        monkeypatch.setattr(queue.AgentSession, "async_create", _async_create)
        monkeypatch.setattr(
            queue.AgentSession.query, "async_count", _make_async(lambda **kw: 1), raising=False
        )
        monkeypatch.setattr(queue, "_delete_stale_terminal_duplicates", lambda sid: 0)
        monkeypatch.setattr(queue.AgentSession, "get_by_id", staticmethod(lambda _id: None))

        depth, agent_session_id = await queue._push_agent_session(
            project_key="valor",
            session_id="0_1",
            working_dir="/tmp",
            message_text="run it",
            sender_name="reflection (x)",
            chat_id="0",
            telegram_message_id=0,
            idempotency_key="reflection:x:60",
        )

        assert depth == 1
        assert created["id"] == agent_session_id, "the row must be created under the bound id"
        assert "agent_session_id" not in created, (
            "AgentSession.__init__ pops agent_session_id and drops the binding"
        )
        assert redis.store["enqueue:idem:reflection:x:60"] == agent_session_id

    @pytest.mark.asyncio
    async def test_a_duplicate_tick_creates_nothing(self, redis, monkeypatch):
        from agent import agent_session_queue as queue

        creates = []

        async def _async_create(**kwargs):
            creates.append(kwargs)

        monkeypatch.setattr(queue.AgentSession, "async_create", _async_create)
        monkeypatch.setattr(
            queue.AgentSession.query, "async_count", _make_async(lambda **kw: 1), raising=False
        )
        monkeypatch.setattr(queue, "_delete_stale_terminal_duplicates", lambda sid: 0)

        bound, _ = enqueue_idempotency.bind("reflection:x:60")
        monkeypatch.setattr(
            queue.AgentSession, "get_by_id", staticmethod(lambda _id: object() if _id else None)
        )

        _depth, agent_session_id = await queue._push_agent_session(
            project_key="valor",
            session_id="0_2",
            working_dir="/tmp",
            message_text="run it",
            sender_name="reflection (x)",
            chat_id="0",
            telegram_message_id=0,
            idempotency_key="reflection:x:60",
        )

        assert agent_session_id == bound
        assert creates == [], "the losing tick must not create a session"

    @pytest.mark.asyncio
    async def test_a_loser_whose_winner_died_creates_under_the_bound_id(self, redis, monkeypatch):
        """Race 5: the key is bound but no row exists yet.

        The loser must create the session under the SAME preallocated id, or
        the binding stays pointed at nothing until it expires.
        """
        from agent import agent_session_queue as queue

        created = {}

        async def _async_create(**kwargs):
            created.update(kwargs)

        monkeypatch.setattr(queue.AgentSession, "async_create", _async_create)
        monkeypatch.setattr(
            queue.AgentSession.query, "async_count", _make_async(lambda **kw: 1), raising=False
        )
        monkeypatch.setattr(queue, "_delete_stale_terminal_duplicates", lambda sid: 0)
        monkeypatch.setattr(queue.AgentSession, "get_by_id", staticmethod(lambda _id: None))

        bound, _ = enqueue_idempotency.bind("reflection:x:60")
        _depth, agent_session_id = await queue._push_agent_session(
            project_key="valor",
            session_id="0_3",
            working_dir="/tmp",
            message_text="run it",
            sender_name="reflection (x)",
            chat_id="0",
            telegram_message_id=0,
            idempotency_key="reflection:x:60",
        )
        assert agent_session_id == bound
        assert created["id"] == bound


class TestReflectionKey:
    def test_the_key_floors_to_the_tick_period(self):
        """Sub-tick jitter must not put two ticks in different windows."""
        import inspect

        from agent import reflection_scheduler

        source = inspect.getsource(reflection_scheduler._enqueue_agent_reflection)
        assert 'f"reflection:{entry.name}:{int(due_epoch) // 60 * 60}"' in source

    def test_a_scheduleless_reflection_keeps_the_unkeyed_behaviour(self):
        """There is no due window to key on, so there is no key."""
        import inspect

        from agent import reflection_scheduler

        source = inspect.getsource(reflection_scheduler._enqueue_agent_reflection)
        assert "if due_epoch is not None else None" in source

    def test_the_due_window_is_read_before_mark_started(self):
        """mark_started() writes ran_at, which is the due computation's input.

        Reading the window after it would make a crash-retry of the same tick
        key on a different window and enqueue a second session.
        """
        import inspect

        from agent.reflection_scheduler import ReflectionScheduler

        source = inspect.getsource(ReflectionScheduler.tick)
        due_at = source.index("due_epoch = reflection_due_epoch(")
        run_at = source.index("run_reflection(entry, state")
        assert due_at < run_at


def _make_async(fn):
    async def _inner(*args, **kwargs):
        return fn(*args, **kwargs)

    return _inner
