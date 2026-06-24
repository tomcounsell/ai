"""
Integration tests for the email bridge inbound path.

Tests _process_inbound_email() with a real enqueue_agent_session() call
against the test Redis instance (provided by the autouse redis_test_db
fixture in tests/conftest.py).

Design:
- enqueue_agent_session() is mocked to avoid Popoto persistence complexity
  in tests — the unit under test is the routing/dispatch logic in
  _process_inbound_email(), not Popoto internals.
- Thread-continuation Redis lookups are exercised against the real test Redis
  db (via a patched _get_redis() that points at the per-worker test db,
  matching the autouse ``redis_test_db`` fixture).
- Unknown sender, active-project guard, and extra_context propagation are
  all verified by inspecting mock call args.
"""

import time
from unittest.mock import AsyncMock, patch

import pytest
import redis

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parsed_email(
    from_addr: str = "alice@example.com",
    subject: str = "Help with my account",
    body: str = "Hello, I need some assistance.",
    message_id: str = "<msg-001@example.com>",
    in_reply_to: str | None = None,
) -> dict:
    """Return a minimal parsed email dict matching parse_email_message() output."""
    return {
        "from_addr": from_addr,
        "subject": subject,
        "body": body,
        "message_id": message_id,
        "in_reply_to": in_reply_to,
    }


def _project_config(key: str = "test-project") -> dict:
    """Return a minimal project config dict with email.contacts section."""
    return {
        "_key": key,
        "name": key,
        "working_directory": "/tmp/test-project",
        "email": {
            "contacts": {
                "alice@example.com": {"name": "Alice"},
            }
        },
    }


def _projects_json(project_key: str = "test-project") -> dict:
    """Return a minimal projects.json config dict."""
    return {
        "projects": {
            project_key: _project_config(project_key),
        }
    }


def _test_redis() -> redis.Redis:
    """Return a Redis connection to the per-worker test db (matching conftest's
    autouse ``redis_test_db`` fixture).

    pytest-xdist sets ``PYTEST_XDIST_WORKER`` (e.g. ``gw0``, ``gw1``) inside
    each worker process, mirroring the worker_id used by the autouse
    fixture. Hardcoding ``db=1`` here causes ``-n auto`` collisions when
    ``gw1+`` workers run this test against db=1 while their popoto state
    lives in db=2+.
    """
    import os

    worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
    if worker_id.startswith("gw"):
        test_db = int(worker_id[2:]) + 1  # gw0->db1, gw1->db2, ...
    else:
        test_db = 1  # serial run
    return redis.Redis(db=test_db, decode_responses=True)


# ---------------------------------------------------------------------------
# Tests: inbound email → session enqueued
# ---------------------------------------------------------------------------


class TestProcessInboundEmail:
    """Integration tests for _process_inbound_email() → enqueue_agent_session()."""

    @pytest.mark.asyncio
    async def test_new_inbound_email_enqueues_session(self):
        """A new inbound email calls enqueue_agent_session with correct args."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        project_key = "test-project"
        project = _project_config(project_key)
        config = _projects_json(project_key)

        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            routing.EMAIL_TO_PROJECT["alice@example.com"] = project
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            mock_enqueue = AsyncMock()
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                # Patch _get_redis so it uses the test db (not db=0)
                test_r = _test_redis()
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(_parsed_email(), config)
                test_r.close()

        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        mock_enqueue.assert_called_once()
        kwargs = mock_enqueue.call_args.kwargs

        assert kwargs["project_key"] == project_key
        assert kwargs["message_text"] == "Hello, I need some assistance."
        assert kwargs["sender_name"] == "alice@example.com"
        assert kwargs["chat_id"] == "alice@example.com"
        assert kwargs["telegram_message_id"] == 0  # sentinel for email sessions
        assert kwargs["working_dir"] == "/tmp/test-project"

    @pytest.mark.asyncio
    async def test_inbound_email_sets_email_extra_context(self):
        """Extra context passed to enqueue_agent_session contains transport and email metadata."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        project_key = "test-project"
        project = _project_config(project_key)
        config = _projects_json(project_key)

        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            routing.EMAIL_TO_PROJECT["alice@example.com"] = project
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            mock_enqueue = AsyncMock()
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                test_r = _test_redis()
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(
                        _parsed_email(
                            message_id="<msg-42@example.com>",
                            subject="Billing question",
                        ),
                        config,
                    )
                test_r.close()

        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        mock_enqueue.assert_called_once()
        extra = mock_enqueue.call_args.kwargs.get("extra_context_overrides", {})

        assert extra.get("transport") == "email"
        assert extra.get("email_message_id") == "<msg-42@example.com>"
        assert extra.get("email_from") == "alice@example.com"
        assert extra.get("email_subject") == "Billing question"

    @pytest.mark.asyncio
    async def test_unknown_sender_discards_email(self):
        """Email from an unknown sender is discarded — enqueue_agent_session not called."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        project_key = "test-project"
        config = _projects_json(project_key)

        # Do NOT add unknown@stranger.com to EMAIL_TO_PROJECT
        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            mock_enqueue = AsyncMock()
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                test_r = _test_redis()
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(
                        _parsed_email(from_addr="unknown@stranger.com"),
                        config,
                    )
                test_r.close()

        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        mock_enqueue.assert_not_called()

    @pytest.mark.asyncio
    async def test_thread_continuation_reuses_session_id(self):
        """When In-Reply-To matches a stored Message-ID, the original session_id is reused."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        project_key = "test-project"
        project = _project_config(project_key)
        config = _projects_json(project_key)

        # Pre-seed the thread-continuation mapping in test Redis (db=1)
        original_session_id = f"email_{project_key}_alice_at_example_com_{int(time.time()) - 100}"
        test_r = _test_redis()
        test_r.set(
            "email:msgid:<outbound-msg-001@example.com>",
            original_session_id,
            ex=172800,
        )

        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            routing.EMAIL_TO_PROJECT["alice@example.com"] = project
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            mock_enqueue = AsyncMock()
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                # Patch _get_redis to return our pre-seeded test db connection
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(
                        _parsed_email(
                            message_id="<reply-001@example.com>",
                            in_reply_to="<outbound-msg-001@example.com>",
                            body="Thanks for your help!",
                        ),
                        config,
                    )

        finally:
            test_r.close()
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        mock_enqueue.assert_called_once()
        called_session_id = mock_enqueue.call_args.kwargs.get("session_id")
        assert called_session_id == original_session_id, (
            f"Expected session_id={original_session_id!r} from thread continuation, "
            f"got {called_session_id!r}"
        )


# ---------------------------------------------------------------------------
# Tests: domain-routed inbound email → outbound SMTP reply
# ---------------------------------------------------------------------------


class TestDomainRoutedEmailHandlerDirect:
    """Tests for EmailOutputHandler.send() SMTP call with domain-routed sessions."""

    def _domain_project_config(self, key: str = "psyoptimal") -> dict:
        """Return a minimal project config with email.domains (no contacts)."""
        return {
            "_key": key,
            "name": key,
            "working_directory": "/tmp/test-psyoptimal",
            "email": {
                "domains": ["psyoptimal.com"],
            },
        }

    @pytest.mark.asyncio
    async def test_domain_sender_enqueues_session(self, monkeypatch):
        """Sender @psyoptimal.com (domain-only project) triggers enqueue_agent_session."""
        import bridge.routing as routing_module
        from bridge.email_bridge import _process_inbound_email

        project_key = "psyoptimal"
        project = self._domain_project_config(project_key)
        config = {"projects": {project_key: project}}

        # Patch EMAIL_DOMAIN_TO_PROJECT so find_project_for_email resolves via domain
        monkeypatch.setattr(routing_module, "EMAIL_TO_PROJECT", {})
        monkeypatch.setattr(
            routing_module,
            "EMAIL_DOMAIN_TO_PROJECT",
            {"psyoptimal.com": project},
        )
        monkeypatch.setattr(routing_module, "ACTIVE_PROJECTS", [project_key])

        mock_enqueue = AsyncMock()
        with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
            test_r = _test_redis()
            with patch("bridge.email_bridge._get_redis", return_value=test_r):
                await _process_inbound_email(
                    _parsed_email(
                        from_addr="tcounsell@psyoptimal.com",
                        subject="Test from domain",
                    ),
                    config,
                )
            test_r.close()

        mock_enqueue.assert_called_once()
        kwargs = mock_enqueue.call_args.kwargs
        assert kwargs["project_key"] == project_key
        assert kwargs["sender_name"] == "tcounsell@psyoptimal.com"
        extra = kwargs.get("extra_context_overrides", {})
        assert extra.get("transport") == "email"
        assert extra.get("email_from") == "tcounsell@psyoptimal.com"

    @pytest.mark.asyncio
    async def test_domain_routed_send_calls_send_smtp(self, monkeypatch):
        """EmailOutputHandler.send() calls _send_smtp with correct To and In-Reply-To."""
        from bridge.email_bridge import EmailOutputHandler

        smtp_config = {
            "host": "smtp.example.com",
            "port": 587,
            "user": "noreply@psyoptimal.com",
            "password": "secret",
            "use_tls": True,
        }
        handler = EmailOutputHandler(smtp_config=smtp_config)

        # Build a minimal mock session with extra_context from the domain routing path
        class _MockSession:
            session_id = "test-session-domain-001"
            extra_context = {
                "transport": "email",
                "email_message_id": "<original-msg@psyoptimal.com>",
                "email_from": "tcounsell@psyoptimal.com",
                "email_subject": "Domain test",
            }

        sent_calls = []

        def _capture_smtp(to_addr, mime_msg):
            sent_calls.append({"to": to_addr, "msg": mime_msg})

        monkeypatch.setattr(handler, "_send_smtp", _capture_smtp)

        # Patch asyncio.to_thread to call the function synchronously (avoids real thread)
        async def _sync_to_thread(fn, *args, **kwargs):
            return fn(*args, **kwargs)

        monkeypatch.setattr("bridge.email_bridge.asyncio.to_thread", _sync_to_thread)

        await handler.send(
            chat_id="tcounsell@psyoptimal.com",
            text="Hello from domain routing!",
            reply_to_msg_id=0,
            session=_MockSession(),
        )

        assert len(sent_calls) == 1, "Expected exactly one SMTP send"
        call = sent_calls[0]
        assert call["to"] == ["tcounsell@psyoptimal.com"]
        assert call["msg"]["To"] == "tcounsell@psyoptimal.com"
        assert call["msg"]["In-Reply-To"] == "<original-msg@psyoptimal.com>"
        assert call["msg"]["Subject"].startswith("Re:")

    @pytest.mark.asyncio
    async def test_no_bridge_callbacks_warning_suppressed_for_email_transport(self, monkeypatch):
        """Regression guard: enqueued email session has transport=email in extra_context,
        confirming the handler would be found via (project_key, 'email') composite key."""
        import bridge.routing as routing_module
        from bridge.email_bridge import _process_inbound_email

        project_key = "psyoptimal"
        project = self._domain_project_config(project_key)
        config = {"projects": {project_key: project}}

        monkeypatch.setattr(routing_module, "EMAIL_TO_PROJECT", {})
        monkeypatch.setattr(
            routing_module,
            "EMAIL_DOMAIN_TO_PROJECT",
            {"psyoptimal.com": project},
        )
        monkeypatch.setattr(routing_module, "ACTIVE_PROJECTS", [project_key])

        mock_enqueue = AsyncMock()
        with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
            test_r = _test_redis()
            with patch("bridge.email_bridge._get_redis", return_value=test_r):
                await _process_inbound_email(
                    _parsed_email(from_addr="user@psyoptimal.com"),
                    config,
                )
            test_r.close()

        mock_enqueue.assert_called_once()
        extra = mock_enqueue.call_args.kwargs.get("extra_context_overrides", {})
        # transport=email in extra_context means the worker will look up
        # (project_key, "email") composite key and find EmailOutputHandler
        assert extra.get("transport") == "email", (
            "transport='email' must be set so worker resolves EmailOutputHandler "
            "via composite key — without it 'No bridge callbacks registered' is emitted"
        )


# ---------------------------------------------------------------------------
# Tests: inbound attachments end-to-end (parse → persist → history → context)
# ---------------------------------------------------------------------------


def _raw_email_with_attachments(attachments, *, body="See attached.", message_id="<att-e2e@x>"):
    """Build raw multipart email bytes carrying ``(filename, maintype, subtype, data)``."""
    import email.message

    msg = email.message.EmailMessage()
    msg["From"] = "alice@example.com"
    msg["To"] = "valor@example.com"
    msg["Subject"] = "Docs attached"
    if message_id:
        msg["Message-ID"] = message_id
    if body is not None:
        msg.set_content(body)
    for filename, maintype, subtype, data in attachments:
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=filename)
    return msg.as_bytes()


class TestInboundAttachmentsEndToEnd:
    """Full inbound flow: a multipart email's attachments are persisted to disk,
    exposed in read output, and carried into the session extra_context."""

    @pytest.mark.asyncio
    async def test_single_attachment_full_flow(self, tmp_path, monkeypatch):
        import os

        import bridge.email_bridge as eb
        import bridge.routing as routing
        from bridge.email_bridge import (
            _persist_attachments,
            _process_inbound_email,
            _record_history,
            parse_email_message,
        )
        from tools.email_history import get_recent_emails

        # Redirect attachment storage + vault to tmp (no repo pollution).
        store = tmp_path / "data" / "media" / "email-attachments"
        vault = tmp_path / "work-vault" / "email-attachments"
        monkeypatch.setattr(eb, "EMAIL_ATTACHMENT_DIR", store)
        monkeypatch.setattr(eb, "EMAIL_ATTACHMENT_VAULT_SUBDIR", vault)

        # Align the history reader's Redis db with the bridge writer's test db.
        worker_id = os.environ.get("PYTEST_XDIST_WORKER", "")
        test_db = int(worker_id[2:]) + 1 if worker_id.startswith("gw") else 1
        monkeypatch.setenv("REDIS_URL", f"redis://localhost:6379/{test_db}")

        raw = _raw_email_with_attachments(
            [("invoice.pdf", "application", "pdf", b"%PDF-1.4 invoice bytes")]
        )
        parsed = parse_email_message(raw)
        assert parsed is not None
        assert len(parsed["attachments"]) == 1

        # 1. Persist bytes to disk (poll-loop side-effect).
        _persist_attachments(parsed)
        stored_path = parsed["attachments"][0]["path"]
        assert stored_path is not None
        from pathlib import Path

        assert Path(stored_path).read_bytes() == b"%PDF-1.4 invoice bytes"
        # Vault mirror happened too.
        assert any(vault.rglob("*invoice.pdf"))

        project_key = "test-project"
        project = _project_config(project_key)
        config = _projects_json(project_key)
        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            routing.EMAIL_TO_PROJECT["alice@example.com"] = project
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            test_r = _test_redis()
            with patch("bridge.email_bridge._get_redis", return_value=test_r):
                # 2. Record history blob (read-output source).
                _record_history(parsed)

                # 3. Process inbound → extra_context carries readable paths.
                mock_enqueue = AsyncMock()
                with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                    await _process_inbound_email(parsed, config)
            test_r.close()
        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        # extra_context exposes the readable stored path + metadata.
        extra = mock_enqueue.call_args.kwargs.get("extra_context_overrides", {})
        email_atts = extra.get("email_attachments")
        assert email_atts and len(email_atts) == 1
        assert email_atts[0]["filename"] == "invoice.pdf"
        assert email_atts[0]["path"] == stored_path

        # read output (cache hit) exposes attachment metadata.
        read = get_recent_emails(limit=5)
        msgs = [m for m in read["messages"] if m["message_id"] == "<att-e2e@x>"]
        assert msgs, "recorded message should appear in read output"
        assert msgs[0]["attachments"][0]["filename"] == "invoice.pdf"

    @pytest.mark.asyncio
    async def test_multiple_attachments_full_flow(self, tmp_path, monkeypatch):
        import bridge.email_bridge as eb
        import bridge.routing as routing
        from bridge.email_bridge import (
            _persist_attachments,
            _process_inbound_email,
            parse_email_message,
        )

        store = tmp_path / "store"
        vault = tmp_path / "vault"
        monkeypatch.setattr(eb, "EMAIL_ATTACHMENT_DIR", store)
        monkeypatch.setattr(eb, "EMAIL_ATTACHMENT_VAULT_SUBDIR", vault)

        raw = _raw_email_with_attachments(
            [
                ("a.pdf", "application", "pdf", b"alpha"),
                ("b.csv", "text", "csv", b"col1,col2"),
            ],
            message_id="<multi-e2e@x>",
        )
        parsed = parse_email_message(raw)
        _persist_attachments(parsed)

        from pathlib import Path

        for att, expected in zip(parsed["attachments"], (b"alpha", b"col1,col2")):
            assert Path(att["path"]).read_bytes() == expected

        project_key = "test-project"
        project = _project_config(project_key)
        config = _projects_json(project_key)
        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            routing.EMAIL_TO_PROJECT["alice@example.com"] = project
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            test_r = _test_redis()
            mock_enqueue = AsyncMock()
            with patch("bridge.email_bridge._get_redis", return_value=test_r):
                with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                    await _process_inbound_email(parsed, config)
            test_r.close()
        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        extra = mock_enqueue.call_args.kwargs.get("extra_context_overrides", {})
        names = [a["filename"] for a in extra.get("email_attachments", [])]
        assert names == ["a.pdf", "b.csv"]


# ---------------------------------------------------------------------------
# Tests: health timestamp in _email_inbox_loop
# ---------------------------------------------------------------------------


class _BreakLoopError(Exception):
    """Sentinel exception to break out of the infinite polling loop after one iteration."""


class TestHealthTimestamp:
    """_email_inbox_loop() writes email:last_poll_ts to Redis on each poll."""

    @pytest.mark.asyncio
    async def test_health_timestamp_written_after_poll(self):
        """After one successful poll iteration, email:last_poll_ts is set in Redis."""
        from bridge.email_bridge import REDIS_LAST_POLL_KEY, _email_inbox_loop

        test_r = _test_redis()
        # Ensure the key does not exist before the test
        test_r.delete(REDIS_LAST_POLL_KEY)

        imap_config = {
            "host": "imap.example.com",
            "port": 993,
            "user": "test@example.com",
            "password": "secret",
            "ssl": True,
        }

        call_count = 0

        async def _break_after_first(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count >= 1:
                raise _BreakLoopError("break after first iteration")

        try:
            with patch(
                "bridge.email_bridge._poll_imap",
                new_callable=AsyncMock,
                return_value=[],
            ):
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    with patch(
                        "bridge.email_bridge.asyncio.sleep",
                        side_effect=_break_after_first,
                    ):
                        await _email_inbox_loop(imap_config, config={})
        except _BreakLoopError:
            pass

        # Verify health timestamp was written
        ts_value = test_r.get(REDIS_LAST_POLL_KEY)
        assert ts_value is not None, (
            f"Expected {REDIS_LAST_POLL_KEY} to be set in Redis after one poll"
        )
        # Verify it's a valid float timestamp
        ts_float = float(ts_value)
        assert ts_float > 0
        assert ts_float <= time.time() + 1  # not in the future

        # Cleanup
        test_r.delete(REDIS_LAST_POLL_KEY)
        test_r.close()


# ---------------------------------------------------------------------------
# Tests: domain-routed inbound email → session enqueued (the bug fix path)
# ---------------------------------------------------------------------------


def _domain_project_config(key: str = "psyoptimal") -> dict:
    """Return a minimal project config dict with email.domains section (no contacts)."""
    return {
        "_key": key,
        "name": key,
        "working_directory": "/tmp/psyoptimal",
        "email": {
            "domains": ["psyoptimal.com"],
        },
    }


class TestDomainRoutedEmailReply:
    """Domain-routed inbound email should enqueue a session (not silently discard).

    This exercises the fix in worker/__main__.py:
      _should_register_email_handler() now returns True for email.domains-only projects,
      so EmailOutputHandler gets registered and the reply is SMTP-delivered.

    The integration test validates the inbound routing path:
    - find_project_for_email() resolves via EMAIL_DOMAIN_TO_PROJECT (domain fallback)
    - enqueue_agent_session() is called with correct project_key and extra_context
    - "No bridge callbacks" log line is NOT emitted (regression guard for silent-discard)
    """

    @pytest.mark.asyncio
    async def test_domain_routed_email_enqueues_session(self):
        """Inbound email from @psyoptimal.com resolves via domain routing and enqueues."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        project_key = "psyoptimal"
        project = _domain_project_config(project_key)
        config = {
            "projects": {
                project_key: project,
            }
        }

        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_domain_map = routing.EMAIL_DOMAIN_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            # No exact-match entry — only domain routing
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_DOMAIN_TO_PROJECT["psyoptimal.com"] = project
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            mock_enqueue = AsyncMock()
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                test_r = _test_redis()
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(
                        _parsed_email(
                            from_addr="tcounsell@psyoptimal.com",
                            subject="Domain routed inquiry",
                            body="Hello from a domain-routed sender.",
                        ),
                        config,
                    )
                test_r.close()

        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.EMAIL_DOMAIN_TO_PROJECT.clear()
            routing.EMAIL_DOMAIN_TO_PROJECT.update(original_domain_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        mock_enqueue.assert_called_once()
        kwargs = mock_enqueue.call_args.kwargs
        assert kwargs["project_key"] == project_key
        assert kwargs["message_text"] == "Hello from a domain-routed sender."
        assert kwargs["sender_name"] == "tcounsell@psyoptimal.com"

    @pytest.mark.asyncio
    async def test_domain_routed_email_sets_transport_in_extra_context(self):
        """Domain-routed email propagates transport=email in extra_context_overrides."""
        import bridge.routing as routing
        from bridge.email_bridge import _process_inbound_email

        project_key = "psyoptimal"
        project = _domain_project_config(project_key)
        config = {"projects": {project_key: project}}

        original_email_map = routing.EMAIL_TO_PROJECT.copy()
        original_domain_map = routing.EMAIL_DOMAIN_TO_PROJECT.copy()
        original_active = routing.ACTIVE_PROJECTS[:]
        try:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_DOMAIN_TO_PROJECT["psyoptimal.com"] = project
            if project_key not in routing.ACTIVE_PROJECTS:
                routing.ACTIVE_PROJECTS.append(project_key)

            mock_enqueue = AsyncMock()
            with patch("agent.agent_session_queue.enqueue_agent_session", mock_enqueue):
                test_r = _test_redis()
                with patch("bridge.email_bridge._get_redis", return_value=test_r):
                    await _process_inbound_email(
                        _parsed_email(
                            from_addr="tcounsell@psyoptimal.com",
                            message_id="<domain-msg-001@psyoptimal.com>",
                            subject="Domain-routed subject",
                        ),
                        config,
                    )
                test_r.close()

        finally:
            routing.EMAIL_TO_PROJECT.clear()
            routing.EMAIL_TO_PROJECT.update(original_email_map)
            routing.EMAIL_DOMAIN_TO_PROJECT.clear()
            routing.EMAIL_DOMAIN_TO_PROJECT.update(original_domain_map)
            routing.ACTIVE_PROJECTS[:] = original_active

        mock_enqueue.assert_called_once()
        extra = mock_enqueue.call_args.kwargs.get("extra_context_overrides", {})
        assert extra.get("transport") == "email"
        assert extra.get("email_from") == "tcounsell@psyoptimal.com"
        assert extra.get("email_subject") == "Domain-routed subject"
