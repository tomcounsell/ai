"""The one sanctioned vault writer: no credential byte on any path (Task 9)."""

from __future__ import annotations

import json
import pathlib
import subprocess
import uuid

from tools.vault_write import VaultWriteResult, render_resource_acquired_section, write_credential


def fresh_pk() -> str:
    return f"test-3215-vault-{uuid.uuid4().hex[:8]}"


class RecordingRunner:
    """A fake `op` invocation that records every argv it was called with,
    so a test can assert the secret value never appears in any of them."""

    def __init__(self, *, returncode: int = 0, stderr: str = ""):
        self.calls: list[list[str]] = []
        self.returncode = returncode
        self.stderr = stderr

    def __call__(self, argv: list[str]) -> subprocess.CompletedProcess:
        self.calls.append(argv)
        return subprocess.CompletedProcess(argv, self.returncode, stdout="", stderr=self.stderr)


class TestNoCredentialByteAnywhere:
    def test_refused_output_contains_no_credential_bytes(self, caplog):
        secret = f"distinctive-secret-{uuid.uuid4().hex}"
        runner = RecordingRunner(returncode=1, stderr="op: vault access denied")

        result = write_credential(
            "test-item", secret, vault="m-valor", project_key=fresh_pk(), runner=runner
        )

        assert result.state == "refused"
        assert secret not in (result.detail or "")
        assert secret not in repr(result)
        for record in caplog.records:
            assert secret not in record.getMessage()
        for call_argv in runner.calls:
            assert secret not in " ".join(call_argv)

    def test_created_result_never_carries_the_raw_value(self):
        secret = f"distinctive-secret-{uuid.uuid4().hex}"
        runner = RecordingRunner(returncode=0)

        result = write_credential(
            "test-item", secret, vault="m-valor", project_key=fresh_pk(), runner=runner
        )

        assert result.state == "created"
        assert secret not in (result.fingerprint or "")
        assert result.fingerprint.startswith("sha256:")
        assert secret not in repr(result)

    def test_evidence_row_carries_no_credential_byte(self):
        from models.improvement_evidence import ImprovementEvidence

        secret = f"distinctive-secret-{uuid.uuid4().hex}"
        pk = fresh_pk()
        runner = RecordingRunner(returncode=0)

        write_credential("test-item", secret, vault="m-valor", project_key=pk, runner=runner)

        rows = list(ImprovementEvidence.query.filter(project_key=pk, kind="resource_acquired"))
        assert len(rows) == 1
        assert secret not in (rows[0].text or "")
        assert secret not in (rows[0].detail or "")
        assert rows[0].detail.startswith("sha256:")


class TestRefusals:
    def test_empty_title_refuses_without_calling_op(self):
        runner = RecordingRunner()
        result = write_credential("", "value", runner=runner)
        assert result == VaultWriteResult("", None, "refused", detail="EMPTY")
        assert runner.calls == []

    def test_whitespace_value_refuses_without_calling_op(self):
        runner = RecordingRunner()
        result = write_credential("title", "   ", runner=runner)
        assert result.state == "refused"
        assert result.detail == "EMPTY"
        assert runner.calls == []

    def test_missing_op_binary_refuses(self):
        def raiser(argv):
            raise FileNotFoundError("op: command not found")

        result = write_credential("title", "value", runner=raiser)
        assert result.state == "refused"
        assert "not found" in result.detail

    def test_non_zero_exit_refuses_with_stderr_first_line(self):
        runner = RecordingRunner(returncode=1, stderr="op: vault access denied\nmore detail")
        result = write_credential("title", "value", runner=runner)
        assert result.state == "refused"
        assert result.detail == "op: vault access denied"


class TestRenderResourceAcquiredSection:
    def test_renders_one_seeded_row(self):
        class Row:
            text = "cloudflare-token"
            detail = "sha256:abc123"

        rendered = render_resource_acquired_section([Row()])
        assert "cloudflare-token" in rendered
        assert "sha256:abc123" in rendered

    def test_empty_list_renders_a_named_empty_state(self):
        assert "No resources acquired" in render_resource_acquired_section([])


class TestTemplateCategoryIsTheEnumSpelling:
    """`op item create --template` takes 1Password's ENUM category spelling.

    `op item template list` prints the DISPLAY name ("API Credential"), and
    handing that back to `op item create` fails with `"API Credential" is an
    unknown item type`. The integration test that would have caught this skips
    wherever `op` is unauthenticated, so this unit-level pin -- which needs no
    `op` at all -- is what keeps a display-name regression unconditionally red.
    """

    def _template_written_by(self, **kwargs) -> dict:
        """Capture the JSON template `write_credential` hands to `op`.

        The file is unlinked in `write_credential`'s `finally`, so the read has
        to happen inside the runner, while the call is in flight.
        """
        captured: dict = {}

        def runner(argv: list[str]) -> subprocess.CompletedProcess:
            template_path = argv[argv.index("--template") + 1]
            captured.update(json.loads(pathlib.Path(template_path).read_text()))
            return subprocess.CompletedProcess(argv, 0, stdout="", stderr="")

        write_credential("title", "value", runner=runner, **kwargs)
        return captured

    def test_default_category_is_the_enum_form_not_the_display_name(self):
        template = self._template_written_by()

        assert template["category"] == "API_CREDENTIAL"
        assert template["category"] != "API Credential"
        assert " " not in template["category"]

    def test_an_explicit_category_is_passed_through_unchanged(self):
        template = self._template_written_by(category="PASSWORD")

        assert template["category"] == "PASSWORD"
