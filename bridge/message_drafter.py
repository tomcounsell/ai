"""
Message drafting for user-visible delivery.

Agent responses are passed through deterministic composition:
1. Process narration is stripped
2. Per-medium wire-format is validated
3. Very long responses are attached as a .txt file
4. The agent's own text is composed with emoji, stage progress, and link footer
5. context_summary and expectations are derived for session routing

No LLM rewriting of the agent's output. The drafter's job is
validation + structural composition, not summarization.

Anti-fabrication rule: expectations must NEVER be fabricated.
Only explicit questions (from ## Open Questions sections or sentences
ending in "?") may populate expectations. Declarative plans are NOT questions.
"""

import logging
import os
import re
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from bridge.message_quality import (
    PROCESS_NARRATION_PATTERNS as _PROCESS_NARRATION_PATTERNS,
)
from config.enums import SessionType

logger = logging.getLogger(__name__)


def _safe_int(env_key: str, default: int) -> int:
    """Parse an env var as int, falling back to *default* on any error."""
    try:
        return int(os.environ.get(env_key, str(default)))
    except (ValueError, TypeError):
        logger.warning("%s has non-integer value, using default %d", env_key, default)
        return default


def _safe_float(env_key: str, default: float) -> float:
    """Parse an env var as float, falling back to *default* on any error."""
    try:
        return float(os.environ.get(env_key, str(default)))
    except (ValueError, TypeError):
        logger.warning("%s has non-float value, using default %s", env_key, default)
        return default


# Thresholds (overridable via env vars)
# FILE_ATTACH_THRESHOLD: character count above which the full agent response is
# also sent as a .txt file attachment. Valid range: 500-10000 (default 3000).
FILE_ATTACH_THRESHOLD = _safe_int("FILE_ATTACH_THRESHOLD", 3000)

# Short-output early return threshold (D5a): texts shorter than this skip the
# LLM drafter and return as-is. 200 chars matches the current bridge/response.py
# threshold and bounds per-message latency on short replies.
SHORT_OUTPUT_THRESHOLD = 200


def _truncate_at_sentence_boundary(text: str, limit: int = 4096) -> str:
    """Truncate text at a sentence boundary within the character limit.

    Finds the last sentence-ending punctuation (. ! ?) followed by whitespace
    or end-of-string within the limit. Falls back to raw truncation with
    ellipsis if no sentence boundary is found within the last 500 characters.

    Args:
        text: The text to truncate.
        limit: Maximum character count (default: Telegram's 4096 limit).

    Returns:
        Truncated text ending at a complete sentence, or '...' fallback.
    """
    if not text or len(text) <= limit:
        return text or ""

    # Reserve space for potential ellipsis
    search_text = text[: limit - 3]

    # Look for sentence boundaries in the last 500 chars
    search_start = max(0, len(search_text) - 500)
    search_window = search_text[search_start:]

    # Match . or ! or ? followed by whitespace or end
    matches = list(re.finditer(r"[.!?](?:\s|$)", search_window))

    if matches:
        last_match = matches[-1]
        cut_pos = search_start + last_match.start() + 1
        return text[:cut_pos].rstrip()

    # No sentence boundary found -- fall back to raw truncation
    return text[: limit - 3] + "..."


def _extract_open_questions(text: str) -> list[str]:
    """Extract questions from '## Open Questions' sections in agent output.

    Scans the text for a markdown '## Open Questions' heading and extracts
    substantive question items from the content below it. Returns an empty
    list if no section is found, the section is empty, or it contains only
    placeholder text.

    Only numbered/bulleted list items with substantive text are treated as
    questions. The heading itself is the signal -- items under it are questions
    regardless of punctuation (per plan design decision).

    Args:
        text: Raw agent output text to scan.

    Returns:
        List of verbatim question strings, or empty list if none found.
    """
    if not text:
        return []

    # Find the ## Open Questions section, but skip resolved/answered sections.
    # Match "## Open Questions" but NOT "## Open Questions (Resolved)" or similar.
    pattern = r"^## Open Questions(?!\s*\((?:Resolved|Answered|Closed|Done)\)).*$"
    match = re.search(pattern, text, re.MULTILINE | re.IGNORECASE)
    if not match:
        return []

    # Extract content after the heading until the next ## heading or end of text
    section_start = match.end()
    next_heading = re.search(r"^## ", text[section_start:], re.MULTILINE)
    if next_heading:
        section_content = text[section_start : section_start + next_heading.start()]
    else:
        section_content = text[section_start:]

    # Extract list items (numbered or bulleted)
    questions = []
    # Match lines starting with number+period, dash, asterisk, or bullet
    list_item_pattern = re.compile(r"^\s*(?:\d+[\.\)]\s*|[-*+]\s*|•\s*)(.*)", re.MULTILINE)
    for item_match in list_item_pattern.finditer(section_content):
        item_text = item_match.group(1).strip()
        # Skip empty, whitespace-only, or placeholder items
        if not item_text:
            continue
        # Skip obvious placeholders
        placeholder_patterns = [
            r"^TBD\.?$",
            r"^TODO\.?$",
            r"^N/?A\.?$",
            r"^None\.?$",
            r"^\?+$",
            r"^\.+$",
        ]
        if any(re.match(p, item_text, re.IGNORECASE) for p in placeholder_patterns):
            continue
        questions.append(item_text)

    return questions


def _strip_process_narration(text: str) -> str:
    """Strip process narration lines from agent output before drafting.

    Removes lines like "Let me check...", "Now let me read..." that are
    process noise, not meaningful content. Only strips if meaningful content
    remains after filtering.
    """
    lines = text.split("\n")
    filtered = []
    for line in lines:
        stripped = line.strip()
        if not stripped:
            filtered.append(line)
            continue
        is_narration = any(p.match(stripped) for p in _PROCESS_NARRATION_PATTERNS)
        if not is_narration:
            filtered.append(line)

    result = "\n".join(filtered).strip()
    # Don't return empty string if everything was stripped
    return result if result else text


@dataclass
class Violation:
    """A wire-format violation surfaced by a per-medium validator."""

    rule: str
    line: int | None = None
    snippet: str = ""


@dataclass
class MessageDraft:
    """Result of drafting an agent response.

    The drafter is a verbatim pass-through + validator — no LLM rewriting.
    The agent's own text reaches the human after narration stripping and
    structural composition (emoji prefix, SDLC stage line, link footer).

    Attributes:
        text: The composed message text for delivery. Empty string signals
            needs_self_draft (wire-format violation or empty promise the
            agent can fix by rewriting itself via the self-draft steering
            path). was_drafted has been removed — the drafter no longer
            calls Haiku or any other LLM.
        full_output_file: Path to the full-output .txt file when the raw
            response exceeds FILE_ATTACH_THRESHOLD. None otherwise. Over-
            length responses still deliver (text is not emptied); the file
            is an additional attachment.
        needs_self_draft: True when a BLOCKING condition fired (wire-format
            violation or empty promise) and the agent should rewrite via
            the self-draft steering path. NOT set for over-length — those
            still deliver with a file pointer.
        artifacts: Dict of extracted artifacts (commits, urls, files_changed,
            test_results, errors).
        context_summary: Coarse one-sentence routing hint for session_router.py
            and bridge/telegram_bridge.py. Derived deterministically by
            _derive_context_summary from the narration-stripped text (first
            non-blank, non-heading line, ≤140 chars). None when the stripped
            text is empty. Not user-facing prose — a routing hint only.
        expectations: Verbatim questions extracted from ## Open Questions
            sections by _extract_open_questions (sole source). None when no
            questions are found (never ""). The None-vs-empty distinction
            matters: _persist_routing_fields in output_handler.py only writes
            expectations when it is not None, preserving any prior persisted
            value when no new questions are present.
        violations: List of wire-format violations from the per-medium
            validator. Informational — surfaced to the agent for editing
            via the review-gate presentation.
    """

    text: str
    full_output_file: Path | None = None
    needs_self_draft: bool = False
    artifacts: dict[str, list[str]] = field(default_factory=dict)
    context_summary: str | None = None
    expectations: str | None = None
    violations: list[Violation] = field(default_factory=list)


_TABLE_SEPARATOR_PATTERN = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)+\|?\s*$")


def validate_telegram(text: str) -> list[Violation]:
    """Validate text against Telegram wire-format rules.

    Current rules:
    - No markdown tables (the ``| --- | --- |`` separator row).

    Returns a list of Violation entries; empty list == pass. The validator
    is informational — the caller surfaces violations in the draft
    presentation so the agent can edit before sending. No server-side
    rewrites (plan §Part B).
    """
    if not text:
        return []
    violations: list[Violation] = []
    for idx, raw_line in enumerate(text.split("\n"), start=1):
        if _TABLE_SEPARATOR_PATTERN.match(raw_line):
            violations.append(
                Violation(
                    rule="no_markdown_tables",
                    line=idx,
                    snippet=raw_line.strip()[:80],
                )
            )
    return violations


# Email rules forbid markdown syntax on the wire (recipients may read with
# clients that render plain text). Match headings, bold/italic/strikethrough
# markers, fenced/inline code, bullet markdown, hyperlink markdown, and tables.
_EMAIL_MARKDOWN_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("no_fenced_code", re.compile(r"^```", re.MULTILINE)),
    ("no_inline_code", re.compile(r"`[^`\n]+`")),
    ("no_markdown_headings", re.compile(r"^#{1,6}\s", re.MULTILINE)),
    ("no_bold_markdown", re.compile(r"\*\*[^*\n]+\*\*")),
    ("no_italic_markdown", re.compile(r"(?<![*_])(?:\*|_)[^*_\n]+(?:\*|_)(?![*_])")),
    ("no_markdown_links", re.compile(r"\[[^\]]+\]\([^\)]+\)")),
    ("no_markdown_bullets", re.compile(r"^\s*[-*+]\s", re.MULTILINE)),
]


def validate_email(text: str) -> list[Violation]:
    """Validate text against email wire-format rules (plain prose only).

    Rejects markdown syntax: fenced/inline code, headings, bold/italic,
    hyperlink markdown, bullet markers, and tables. Returns a list of
    Violation entries; empty list == pass.
    """
    if not text:
        return []
    violations: list[Violation] = []
    for rule, pattern in _EMAIL_MARKDOWN_PATTERNS:
        match = pattern.search(text)
        if match:
            # Line number of the first match
            prefix = text[: match.start()]
            line_no = prefix.count("\n") + 1
            violations.append(
                Violation(
                    rule=rule,
                    line=line_no,
                    snippet=match.group(0)[:80],
                )
            )
    # Tables: same detection as Telegram
    violations.extend(validate_telegram(text))
    return violations


# Rule name emitted by detect_local_file_reference; exported so callers
# (e.g. agent/output_handler.py's self-draft instruction builder) can match
# on the constant instead of a bare string literal.
LOCAL_FILE_PATH_RULE = "local_file_path_reference"

# Local filesystem paths and macOS-only shell command references are
# meaningless once a message leaves the machine that produced it (Telegram
# or email). Case-sensitive — these are Unix path conventions.
_LOCAL_FILE_PATH_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"/tmp/\S+"),  # temp-file paths
    re.compile(r"/Users/\S+"),  # absolute macOS home-directory paths
    re.compile(r"/home/\S+"),  # absolute Linux home-directory paths
    re.compile(r"~/\S+"),  # tilde-relative paths
    # macOS `open` command references, backtick-wrapped or bare.
    re.compile(r"`open (?:-a\s+\S+\s+)?\S+`|\bopen -a \S+"),
]


def detect_local_file_reference(text: str) -> list[Violation]:
    """Detect references to machine-local filesystem paths or `open` commands.

    Local paths (``/tmp/...``, ``/Users/...``, ``/home/...``, ``~/...``) and
    macOS-only ``open -a ...`` command references only resolve on the machine
    that produced the message — meaningless to a Telegram or email recipient
    reading on a different machine. Medium-agnostic: called for both mediums
    regardless of the per-medium validator dispatch.

    Returns a list of Violation entries; empty list == pass.
    """
    if not text:
        return []
    violations: list[Violation] = []
    for idx, raw_line in enumerate(text.split("\n"), start=1):
        for pattern in _LOCAL_FILE_PATH_PATTERNS:
            match = pattern.search(raw_line)
            if match:
                violations.append(
                    Violation(
                        rule=LOCAL_FILE_PATH_RULE,
                        line=idx,
                        snippet=match.group(0)[:80],
                    )
                )
    return violations


# --- Secret-exclusion gate for convert_local_paths_to_attachments -------
# Tunable/provisional: these sets and the vault root are the current
# best-effort exfiltration guard, not an exhaustive secret classifier. Env-
# overridable (comma-separated) so a deployment can extend the list without
# a code change.
_SECRET_EXCLUDED_EXTENSIONS: frozenset[str] = frozenset(
    ext.strip().lower()
    for ext in os.environ.get(
        "LOCAL_PATH_ATTACH_EXCLUDED_EXTENSIONS",
        ".env,.pem,.key,.p12,.pfx,.crt,.cer,.keychain",
    ).split(",")
    if ext.strip()
)
_SECRET_KNOWN_BASENAMES: frozenset[str] = frozenset(
    name.strip()
    for name in os.environ.get(
        "LOCAL_PATH_ATTACH_EXCLUDED_BASENAMES",
        "id_rsa,id_ed25519,id_ecdsa,id_dsa,credentials,known_hosts,authorized_keys",
    ).split(",")
    if name.strip()
)
_SECRET_VAULT_ROOT = os.path.realpath(
    os.path.expanduser(os.environ.get("LOCAL_PATH_ATTACH_VAULT_ROOT", "~/Desktop/Valor/"))
)

# Trailing punctuation stripped from a matched path token before the
# existence check and before scrubbing (sentence-final periods, trailing
# parens/quotes, etc.).
_TRAILING_PUNCTUATION = ".,;:)]}'\""


def _is_secret_excluded(realpath: str) -> bool:
    """Return True if *realpath* must never be attached (secret-exclusion gate).

    Component-based (not basename-only) dot-directory check, plus a
    case-insensitive sensitive-extension check, a known-secret-basename
    check, and a secrets-vault prefix check. See
    ``convert_local_paths_to_attachments`` docstring for the full rationale.
    """
    parts = [p for p in os.path.normpath(realpath).split(os.sep) if p not in ("", ".", "..")]
    if any(part.startswith(".") for part in parts):
        return True

    basename = os.path.basename(realpath)
    _, ext = os.path.splitext(basename)
    if ext.lower() in _SECRET_EXCLUDED_EXTENSIONS:
        return True

    if basename in _SECRET_KNOWN_BASENAMES:
        return True

    try:
        common = os.path.commonpath([realpath, _SECRET_VAULT_ROOT])
    except ValueError:
        # Different drives/roots (unlikely on this platform) — not under vault.
        common = None
    if common == _SECRET_VAULT_ROOT:
        return True

    return False


def convert_local_paths_to_attachments(
    text: str | None,
) -> tuple[str, list[str], int, int]:
    """Convert machine-local file-path references in *text* into attachments.

    Reuses the four filesystem-path patterns in ``_LOCAL_FILE_PATH_PATTERNS``
    (``/tmp/...``, ``/Users/...``, ``/home/...``, ``~/...``) — NOT the
    ``open -a ...`` command pattern, which is not a file to attach. Every
    occurrence is matched via ``pattern.finditer`` (not ``search``), so
    multiple paths in the same text are all detected.

    For each matched token:

    1. Trailing punctuation (``.,;:)]}'"``) is stripped from the token so a
       sentence-final period or wrapping parens don't corrupt the path.
    2. Existence is checked via ``os.path.isfile(os.path.expanduser(token))``
       — ``~`` is expanded first because ``os.path.isfile`` does not expand
       it itself.
    3. The expanded path is resolved via ``os.path.realpath`` so a symlink
       cannot be used to bypass the secret-exclusion gate below.
    4. The secret-exclusion gate (see ``_is_secret_excluded``) skips the
       token — treating it exactly like a dead path (scrubbed, not
       attached) — if the realpath has ANY dot-prefixed path component
       (catching both dotfile basenames and secrets living in a
       dot-*directory* like ``~/.ssh/id_rsa``), a sensitive extension
       (case-insensitive), a known secret basename, or lives under the
       secrets vault (``~/Desktop/Valor/`` by default).
    5. Surviving tokens are appended to ``attached`` as their expanded
       absolute path.

    The SAME trimmed token (not the raw regex match) is scrubbed from the
    text, so adjacent prose/punctuation survives intact (e.g.
    ``/tmp/a.txt,and more`` → attach + scrub only the path, leaving
    ``,and more``). Doubled whitespace left behind by scrubbing is
    collapsed. Both dead paths and secret-excluded paths are scrubbed but
    NOT attached, and are indistinguishable downstream — a secret path is
    never revealed to have been referenced.

    Returns the canonical 4-tuple ``(scrubbed_text, attached_paths,
    dead_count, skipped_count)``:

    * ``scrubbed_text`` — *text* with every detected local-path token
      removed.
    * ``attached_paths`` — expanded absolute paths of existing,
      non-secret-excluded files.
    * ``dead_count`` — number of scrubbed tokens that do not exist on disk.
    * ``skipped_count`` — number of scrubbed tokens excluded by the
      secret-exclusion gate.

    Returns ``(text, [], 0, 0)`` when nothing converts (including for
    empty/``None`` input). Never raises: any internal exception is caught
    and the original text is returned unconverted, so a conversion bug can
    never suppress delivery.
    """
    if not text:
        return (text or "", [], 0, 0)

    try:
        attached: list[str] = []
        dead_count = 0
        skipped_count = 0
        tokens_to_scrub: list[str] = []

        for pattern in _LOCAL_FILE_PATH_PATTERNS:
            if pattern is _LOCAL_FILE_PATH_PATTERNS[-1]:
                # The `open -a ...` command pattern is not a file reference —
                # do not attempt to convert or scrub it here.
                continue
            for match in pattern.finditer(text):
                raw = match.group(0)
                # A comma is almost never part of a real filesystem path, but
                # `\S+` happily captures adjacent prose glued on with no
                # space (e.g. "/tmp/a.txt,and more" -> "/tmp/a.txt,and").
                # Truncate at the first comma before the trailing-punctuation
                # strip so the path candidate never includes glued-on prose.
                if "," in raw:
                    raw = raw.split(",", 1)[0]
                token = raw.rstrip(_TRAILING_PUNCTUATION)
                if not token:
                    continue

                expanded = os.path.expanduser(token)
                if not os.path.isfile(expanded):
                    dead_count += 1
                    tokens_to_scrub.append(token)
                    continue

                realpath = os.path.realpath(expanded)
                if _is_secret_excluded(realpath):
                    skipped_count += 1
                    tokens_to_scrub.append(token)
                    continue

                attached.append(os.path.abspath(expanded))
                tokens_to_scrub.append(token)

        if not tokens_to_scrub:
            return (text, [], 0, 0)

        scrubbed = text
        for token in tokens_to_scrub:
            scrubbed = scrubbed.replace(token, "")
        # Collapse doubled whitespace left behind by scrubbing (but preserve
        # newlines as single newlines, not collapsed into spaces).
        scrubbed = re.sub(r"[ \t]{2,}", " ", scrubbed)
        scrubbed = re.sub(r" +\n", "\n", scrubbed)
        scrubbed = re.sub(r"\n +", "\n", scrubbed)
        scrubbed = scrubbed.strip()

        return (scrubbed, attached, dead_count, skipped_count)
    except Exception:
        logger.warning(
            "convert_local_paths_to_attachments raised; returning original text", exc_info=True
        )
        return (text, [], 0, 0)


def format_violations(violations: list[Violation], medium: str) -> str:
    """Render violations as a ``⚠️`` note for the review gate presentation."""
    if not violations:
        return ""
    lines = [f"⚠️ {len(violations)} wire-format violation(s) for medium={medium}:"]
    for v in violations:
        where = f"line {v.line}" if v.line else ""
        lines.append(f"  • {v.rule} {where}: {v.snippet!r}")
    return "\n".join(lines)


def _validate_for_medium(text: str, medium: str) -> list[Violation]:
    """Dispatch to the per-medium validator, plus medium-agnostic checks.

    Local file-path references are checked regardless of medium — a
    machine-local path is meaningless on both Telegram and email.
    """
    violations: list[Violation] = []
    if medium == "telegram":
        violations.extend(validate_telegram(text))
    elif medium == "email":
        violations.extend(validate_email(text))
    violations.extend(detect_local_file_reference(text))
    return violations


def extract_artifacts(text: str) -> dict[str, list[str]]:
    """
    Extract key artifacts from agent output.

    Pulls out commit hashes, URLs, changed files, test results,
    and error indicators so they can be preserved in summaries.
    """
    artifacts: dict[str, list[str]] = {}

    # Git commit hashes (7-40 hex chars preceded by common keywords)
    commit_pat = r"(?:commit|pushed|merged|created)\s+([a-f0-9]{7,40})"
    commits = re.findall(commit_pat, text, re.IGNORECASE)
    # Also match standalone short hashes in common git output patterns
    commits += re.findall(r"\b([a-f0-9]{7,12})\b(?=\s)", text)
    if commits:
        # dedupe preserving order
        artifacts["commits"] = list(dict.fromkeys(commits))

    # URLs (http/https)
    urls = re.findall(r'https?://[^\s\)>\]"\']+', text)
    if urls:
        artifacts["urls"] = list(dict.fromkeys(urls))

    # Files changed (common git diff output patterns)
    file_pat = r"(?:modified|created|deleted|renamed|changed):\s*" r"(.+?)(?:\n|$)"
    files_changed = re.findall(file_pat, text, re.IGNORECASE)
    files_changed += re.findall(r"^\s*[MADR]\s+(\S+)", text, re.MULTILINE)
    if files_changed:
        artifacts["files_changed"] = list(dict.fromkeys(f.strip() for f in files_changed))

    # Test results
    test_pat = r"(\d+\s+passed" r"(?:,\s*\d+\s+(?:failed|error|warning|skipped))*)"
    test_matches = re.findall(test_pat, text, re.IGNORECASE)
    if test_matches:
        artifacts["test_results"] = test_matches

    # Error indicators
    errors = re.findall(
        r"(?:error|exception|failed|failure):\s*(.+?)(?:\n|$)",
        text,
        re.IGNORECASE,
    )
    if errors:
        artifacts["errors"] = errors[:5]  # Cap at 5

    return artifacts


def _detect_empty_promise(text_lower: str) -> bool:
    """Detect if the agent acknowledged feedback without concrete evidence.

    Backward-compat shim — the actual heuristic logic now lives in
    :mod:`bridge.promise_gate`. This wrapper preserves the old contract
    (returns True when the text looks like an empty promise) so existing
    call sites continue to work without structural changes.

    The new heuristic in ``bridge.promise_gate`` covers BOTH the legacy
    behavioral-change class ("got it / will do / going forward") AND the
    new forward-deferral class ("I'll come back with X / will follow up /
    stay tuned / more soon / I'll report back").
    """
    from bridge.promise_gate import _detect_empty_promise as _impl

    return _impl(text_lower)


def _derive_context_summary(raw_text: str) -> str | None:
    """Derive a coarse context summary from the narration-stripped raw text.

    Returns the first non-blank, non-heading line, capped at ~140 chars
    at a word boundary. This is a deliberately simple deterministic helper
    — string slicing only, no NLP or LLM. Its purpose is to populate
    session.context_summary for session_router.py and other routing readers
    with a coarse topic hint for the session.

    The summary is a ROUTING HINT, not a quality deliverable and not
    user-facing prose. Callers that need a precise summary should not rely
    on this field for display.

    Args:
        raw_text: The narration-stripped agent output text.

    Returns:
        First non-blank, non-heading line, capped at 140 chars (word
        boundary), or None for empty/whitespace-only input.
    """
    if not raw_text or not raw_text.strip():
        return None

    # Take the first non-blank line as a proxy for the opening sentence
    for line in raw_text.split("\n"):
        stripped = line.strip()
        # Skip blank lines and markdown heading/separator lines
        if not stripped or stripped.startswith("#") or stripped.startswith("---"):
            continue
        # Strip leading bullet/list markers
        stripped = re.sub(r"^[-*+•]\s+", "", stripped)
        stripped = re.sub(r"^\d+[.)]\s+", "", stripped)
        if not stripped:
            continue
        # Cap at 140 chars at a word boundary
        if len(stripped) <= 140:
            return stripped
        # Truncate at a word boundary within 140 chars
        truncated = stripped[:137]
        last_space = truncated.rfind(" ")
        if last_space > 100:
            truncated = truncated[:last_space]
        return truncated + "..."
    return None


def linkify_references(text: str, project_key: str | None = None) -> str:
    """Convert plain PR #N and Issue #N references to markdown links.

    Uses the project_key to look up the GitHub org/repo from the registered
    project config. If no project config is found or the text already
    contains markdown links for a reference, it is left unchanged.

    Originally in bridge/formatting.py; folded into bridge/message_drafter.py
    per the message-drafter consolidation (plan #1035 Part A).

    Args:
        text: The text potentially containing PR #N or Issue #N references.
        project_key: Project key for GitHub org/repo lookup. If None, text
            is returned unchanged.

    Returns:
        Text with plain references converted to markdown links.
    """
    from bridge.routing import load_config

    if not text or not project_key or not str(project_key).strip():
        return text

    try:
        all_projects = load_config().get("projects", {})
        config = all_projects.get(str(project_key), {})
        github_config = config.get("github", {})
        org = github_config.get("org")
        repo = github_config.get("repo")
    except Exception:
        return text

    if not org or not repo:
        return text

    base_url = f"https://github.com/{org}/{repo}"

    # Negative lookbehind for [ ensures we don't double-link already-linked refs.
    text = re.sub(
        r"(?<!\[)PR #(\d+)(?!\])",
        lambda m: f"[PR #{m.group(1)}]({base_url}/pull/{m.group(1)})",
        text,
    )
    text = re.sub(
        r"(?<!\[)Issue #(\d+)(?!\])",
        lambda m: f"[Issue #{m.group(1)}]({base_url}/issues/{m.group(1)})",
        text,
    )
    return text


def linkify_references_from_session(text: str, session) -> str:
    """Convenience wrapper that extracts project_key from a session object.

    Args:
        text: The text to linkify.
        session: Object with a project_key attribute (e.g., AgentSession).

    Returns:
        Text with plain references converted to markdown links.
    """
    if not session:
        return text
    project_key = getattr(session, "project_key", None)
    return linkify_references(text, project_key)


def _linkify_references(text: str, session) -> str:
    """Backward-compat alias for linkify_references_from_session."""
    return linkify_references_from_session(text, session)


def _get_status_emoji(session, is_completion: bool = True) -> str:
    """Get the status emoji prefix for milestone-selective display.

    Milestone-selective: completion emoji is reserved for true milestones
    (merged PR, closed issue, failed session). Routine completions get
    no emoji prefix. In-progress work gets the hourglass.

    Args:
        session: AgentSession or mock with .status and .get_links().
        is_completion: Whether the output is classified as completion.

    Returns:
        Emoji string or empty string for routine completions.
    """
    if not session:
        # No session context — fall back to simple logic
        return "✅" if is_completion else "⏳"

    status = session.status
    if status in ("failed",):
        return "❌"

    # Check for milestone events: merged PR or closed issue
    is_milestone = False
    if hasattr(session, "get_links"):
        try:
            links = session.get_links()
            # PR link on completed session suggests merge milestone
            if links.get("pr") and status in ("completed",):
                is_milestone = True
        except Exception as e:
            logger.debug(f"Failed to get session links for emoji selection: {e}")

    if status in ("completed",):
        return "✅" if is_milestone else ""

    # Running/active/pending — in-progress or routine completion
    if is_completion:
        return ""  # Routine completion, no emoji
    return "⏳"


def _write_full_output_file(text: str) -> Path:
    """Write full agent output to a temp file for attachment."""
    fd, path = tempfile.mkstemp(suffix=".txt", prefix="valor_full_output_")
    with os.fdopen(fd, "w") as f:
        f.write(text)
    return Path(path)


# Compact self-draft instruction injected via session steering when a blocking
# condition (wire-format violation, empty promise) fires. Derived from the
# drafter quality rules but kept short to avoid polluting the agent's context
# window.
SELF_DRAFT_INSTRUCTION = (
    "Your message was flagged by the delivery validator for a wire-format violation "
    "or an unsubstantiated promise. Please rewrite it yourself and resend. "
    "Rules: lead with outcomes, not process. Use 2-4 bullet points starting with "
    '"\\u2022 ". Omit internal code details, line counts, and plans for next steps. '
    "Preserve any commit hashes, PR/issue numbers, and explicit questions. "
    "Do NOT include narration like 'Let me investigate' or 'I will check'. "
    "If your work produced no substantive results, say so plainly."
)

# Sentinel returned by drafter callers when self-draft steering was injected.
# Distinguishes "message deferred to agent self-draft" from "send failed" so the
# bridge callback does not log a spurious error. Retained as a module symbol for
# external references even though the primary historical caller
# (send_response_with_files) was deleted in the #1074 follow-up.
STEERING_DEFERRED = "STEERING_DEFERRED"


def _normalize_question_prefix(text: str) -> str:
    """Normalize legacy '? ' question prefix to '>> ' for visual distinction.

    Accepts both '? ' and '>> ' prefixes. Lines starting with '? ' are
    converted to '>> '. Lines already using '>> ' are left unchanged.
    """
    lines = text.split("\n")
    normalized = []
    for line in lines:
        stripped = line.lstrip()
        if stripped.startswith("? "):
            normalized.append(line.replace("? ", ">> ", 1))
        else:
            normalized.append(line)
    return "\n".join(normalized)


def _parse_draft_and_questions(
    summary_text: str,
) -> tuple[str, str | None]:
    """Parse draft output into bullets and optional questions.

    The text may produce (using >> prefix, or legacy ? prefix):
        * Bullet 1
        * Bullet 2
        ---
        >> Question 1
        >> Question 2

    Returns (bullets, questions) where questions is None if no
    --- separator found. The >> prefix is the canonical format;
    ? prefix is accepted for backward compatibility and normalized
    to >> on output.
    """
    if "\n---\n" in summary_text:
        bullets, questions = summary_text.split("\n---\n", 1)
        questions = questions.strip()
        if questions:
            questions = _normalize_question_prefix(questions)
            return bullets.strip(), questions
        return bullets.strip(), None
    # Also handle --- at the very start (edge case)
    if summary_text.strip().startswith("---"):
        raw = summary_text.strip().lstrip("-").strip()
        if raw:
            return "", _normalize_question_prefix(raw)
        return "", None
    return summary_text, None


def _compose_structured_draft(summary_text: str, session=None, is_completion: bool = True) -> str:
    """Compose the full structured draft with emoji, stage line, bullets, questions, and links.

    Two modes:

    Chat (non-SDLC):
        ✅
        • Bullet point 1
        • Bullet point 2

        >> Question needing input

    SDLC:
        ⏳
        ISSUE 243 → PLAN → ▶ BUILD → TEST → REVIEW → DOCS
        • Bullet point 1
        • Bullet point 2

        >> Question needing input
        Issue #243 | PR #250
    """
    # Re-read session from Redis to pick up stage data written during execution.
    # The session object passed in may have been loaded before session_progress.py
    # wrote [stage] entries and link URLs — re-reading ensures we get fresh data.
    if session and hasattr(session, "session_id") and session.session_id:
        try:
            from models.agent_session import AgentSession

            fresh_sessions = list(AgentSession.query.filter(session_id=session.session_id))
            if fresh_sessions:
                session = fresh_sessions[0]
                logger.debug(f"Refreshed session {session.session_id} for structured draft")
        except Exception as e:
            logger.debug(f"Could not refresh session for draft: {e}")

    # Teammate bypass: return prose directly without emoji prefix, bullet parsing,
    # or structured template. The agent's text is already in conversational form.
    if session and (getattr(session, "session_type", None) == SessionType.TEAMMATE):
        return summary_text.strip()

    # Parse questions from text output
    bullets, questions = _parse_draft_and_questions(summary_text)

    parts = []

    # Status emoji prefix (no message echo — Telegram reply-to provides context)
    emoji = _get_status_emoji(session, is_completion)
    if emoji:
        parts.append(emoji)

    # Summary text (bullets or prose)
    parts.append(bullets.strip())

    # Questions section (if any)
    if questions:
        parts.append("")  # blank line separator
        parts.append(questions)

    # Linkify PR #N and Issue #N references
    result = "\n".join(parts)
    result = _linkify_references(result, session)
    return result


async def draft_message(
    raw_response: str,
    session=None,
    *,
    medium: str = "telegram",
    persona: str | None = None,
) -> MessageDraft:
    """Draft an agent response for user-visible delivery.

    Verbatim pass-through with validation and deterministic structural
    composition. No LLM rewriting — the agent's own text is used after
    narration stripping and composition. Haiku, OpenRouter, and all
    LLM-rewrite paths have been removed.

    Flow:
    1. Strip process narration from raw text (_strip_process_narration)
    2. Apply deterministic structural composition (_compose_structured_draft)
       on the agent's own text (emoji prefix, SDLC stage line, link footer)
    3. Run _validate_for_medium on the composed text
    4. If over FILE_ATTACH_THRESHOLD, write full-output file (delivery still
       proceeds — text is NOT emptied for over-length responses)
    5. If _detect_empty_promise fires (empty promise: agent acknowledged feedback
       without substance — "will do", "going forward" etc.) OR _validate_for_medium
       returns any non-empty violations list (markdown table, local file-path
       reference, etc.):
       return MessageDraft(text="", needs_self_draft=True, violations=[...])
       — caller injects a self-draft steering nudge back to the agent
       (PRIMARY flag-handling path, not a failure fallback). This promotion
       happens on BOTH return paths that can carry a violations list: the
       short-output early return and this main-path return. All promoted
       drafts route through the self-draft steering path
       (agent/output_handler.py:429-441), where a local_file_path_reference
       violation adds an attach-via-`--file` instruction telling the agent to
       use `tools/send_message.py "<caption>" --file <path>` instead of
       re-pasting a dead local path.
    6. Populate context_summary from _derive_context_summary(stripped_raw_text)
    7. Populate expectations from _extract_open_questions(raw_response)
       (None when no questions found, never "")
    8. Return MessageDraft(text=<composed>, context_summary=..., expectations=...,
       violations=[...])

    Args:
        raw_response: The raw agent output text.
        session: Optional AgentSession for context enrichment (SDLC stage
            progress, persona bypass for Teammate, link footer).
        medium: Delivery medium discriminator. "telegram" (default) or "email".
            Per-medium validator rules enforce wire-format constraints.
        persona: Optional persona name (pm/dev/teammate/customer-service) for
            tone hints. Not used today — medium and persona stay orthogonal.

    Returns:
        MessageDraft with verbatim composed text, routing fields, and any
        wire-format violations.
    """
    if not raw_response or not raw_response.strip():
        # Even with empty response, render SDLC progress if available
        if session:
            fallback = _compose_structured_draft("", session=session, is_completion=True)
            if fallback.strip():
                return MessageDraft(text=fallback)
        return MessageDraft(text=raw_response or "")

    artifacts = extract_artifacts(raw_response)

    # Short-output early return: skip composition for brief non-SDLC replies
    # (per Risk 1 + D5a in docs/plans/message-drafter.md — bounds per-message
    # latency). Skip only when *all* conditions hold:
    #   - len < 200 chars
    #   - no SDLC session (SDLC needs stage progress + link footer)
    #   - no artifacts (commit hashes, PRs, URLs deserve drafter polish)
    #   - no explicit question to the human (? triggers expectations handling)
    #   - no fenced code block (preserve formatting)
    is_sdlc = bool(session and getattr(session, "sdlc_slug", None))
    has_any_artifacts = any(v for v in artifacts.values())
    if (
        len(raw_response) < SHORT_OUTPUT_THRESHOLD
        and not is_sdlc
        and not has_any_artifacts
        and "?" not in raw_response
        and "```" not in raw_response
    ):
        short_violations = _validate_for_medium(raw_response, medium)
        if short_violations:
            logger.info(
                "Wire-format violation(s) detected in short-output reply — "
                "requesting self-draft via steering: %s",
                [v.rule for v in short_violations],
            )
            return MessageDraft(
                text="",
                needs_self_draft=True,
                artifacts=artifacts,
                violations=short_violations,
            )
        return MessageDraft(
            text=raw_response,
            artifacts=artifacts,
            violations=short_violations,
        )

    # Strip process narration before composition
    stripped_text = _strip_process_narration(raw_response)

    # Write full output file for very long responses (delivery still proceeds)
    full_output_file = None
    if len(raw_response) > FILE_ATTACH_THRESHOLD:
        try:
            full_output_file = _write_full_output_file(raw_response)
        except Exception as e:
            logger.warning(f"Failed to write full output file: {e}")

    # Apply deterministic composition on the agent's own text
    composed_text = _compose_structured_draft(stripped_text, session=session, is_completion=True)

    # Run the per-medium validator on the composed text
    violations = _validate_for_medium(composed_text, medium)

    # Detect empty promises — agent acknowledged feedback without evidence —
    # or a wire-format violation (markdown table, local file-path reference,
    # etc.) was flagged by the validator. Either condition promotes to
    # needs_self_draft=True so the agent rewrites via the self-draft
    # steering path instead of a violation shipping verbatim.
    is_empty_promise = _detect_empty_promise(stripped_text.lower())
    if is_empty_promise or violations:
        if is_empty_promise:
            logger.info("Empty promise detected — requesting self-draft via steering")
        else:
            logger.info(
                "Wire-format violation(s) detected — requesting self-draft via steering: %s",
                [v.rule for v in violations],
            )
        return MessageDraft(
            text="",
            full_output_file=full_output_file,
            needs_self_draft=True,
            artifacts=artifacts,
            violations=violations,
        )

    # Derive routing fields deterministically
    context_summary = _derive_context_summary(stripped_text)

    # Extract open questions; return None when none found (never "")
    expectations: str | None = None
    open_questions = _extract_open_questions(raw_response)
    if open_questions:
        expectations = "\n".join(f">> {q}" for q in open_questions)
        logger.info(
            f"Extracted {len(open_questions)} open questions from ## Open Questions section"
        )

    return MessageDraft(
        text=composed_text,
        full_output_file=full_output_file,
        needs_self_draft=False,
        artifacts=artifacts,
        context_summary=context_summary,
        expectations=expectations,
        violations=violations,
    )
