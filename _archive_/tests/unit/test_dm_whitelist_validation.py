"""Tests for bridge.config_validation: dm whitelist, groups, email, bots, roster."""

from __future__ import annotations

import json

import pytest

from bridge.config_validation import (
    ConfigValidationError,
    summarize_ownership,
    validate_bot_live_flags,
    validate_dm_whitelist,
    validate_email_routing,
    validate_machine_roster,
    validate_projects_config,
    validate_telegram_bots,
    validate_telegram_groups,
)
from config.machine import normalize_machine_name


def _make_config(whitelist: list, projects: dict | None = None) -> dict:
    return {
        "projects": projects or {},
        "dms": {"whitelist": whitelist},
    }


def test_empty_whitelist_passes():
    validate_dm_whitelist(_make_config([]))


def test_no_dms_section_passes():
    validate_dm_whitelist({"projects": {}})


def test_valid_single_machine_per_project_passes():
    cfg = _make_config(
        whitelist=[
            {"id": 1, "name": "Alice", "project": "valor"},
            {"id": 2, "name": "Bob", "project": "popoto"},
        ],
        projects={
            "valor": {"machine": "Cowboy"},
            "popoto": {"machine": "Cowboy"},
        },
    )
    validate_dm_whitelist(cfg)


def test_two_contacts_on_different_machines_passes():
    cfg = _make_config(
        whitelist=[
            {"id": 1, "name": "Alice", "project": "valor"},
            {"id": 2, "name": "Charlie", "project": "psyoptimal"},
        ],
        projects={
            "valor": {"machine": "Cowboy"},
            "psyoptimal": {"machine": "Captain"},
        },
    )
    validate_dm_whitelist(cfg)


def test_same_contact_two_projects_same_machine_passes():
    """A contact appearing twice but resolving to the same machine is allowed."""
    cfg = _make_config(
        whitelist=[
            {"id": 1, "name": "Alice", "project": "valor"},
            {"id": 1, "name": "Alice", "project": "popoto"},
        ],
        projects={
            "valor": {"machine": "Cowboy"},
            "popoto": {"machine": "Cowboy"},
        },
    )
    validate_dm_whitelist(cfg)


def test_same_contact_two_projects_different_machines_fails():
    cfg = _make_config(
        whitelist=[
            {"id": 1, "name": "Alice", "project": "valor"},
            {"id": 1, "name": "Alice", "project": "psyoptimal"},
        ],
        projects={
            "valor": {"machine": "Cowboy"},
            "psyoptimal": {"machine": "Captain"},
        },
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    msg = str(exc_info.value)
    assert "id=1" in msg
    assert "Cowboy" in msg
    assert "Captain" in msg


def test_missing_project_field_fails():
    cfg = _make_config(
        whitelist=[{"id": 1, "name": "Alice"}],
        projects={"valor": {"machine": "Cowboy"}},
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    assert "no 'project' field" in str(exc_info.value)


def test_unknown_project_reference_fails():
    cfg = _make_config(
        whitelist=[{"id": 1, "name": "Alice", "project": "ghost"}],
        projects={"valor": {"machine": "Cowboy"}},
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    assert "unknown project 'ghost'" in str(exc_info.value)


def test_project_missing_machine_field_fails():
    cfg = _make_config(
        whitelist=[{"id": 1, "name": "Alice", "project": "valor"}],
        projects={"valor": {}},
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    assert "has no 'machine' field" in str(exc_info.value)


def test_non_integer_id_fails():
    cfg = _make_config(
        whitelist=[{"id": "not-a-number", "name": "Alice", "project": "valor"}],
        projects={"valor": {"machine": "Cowboy"}},
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    assert "non-integer id" in str(exc_info.value)


def test_entry_without_id_is_skipped_silently():
    """Entries with no id field are not whitelist entries — likely doc/comment shapes."""
    cfg = _make_config(
        whitelist=[
            {"name": "comment", "note": "this is just a doc"},
            {"id": 1, "name": "Alice", "project": "valor"},
        ],
        projects={"valor": {"machine": "Cowboy"}},
    )
    validate_dm_whitelist(cfg)


def test_multiple_errors_aggregated():
    cfg = _make_config(
        whitelist=[
            {"id": 1, "name": "Alice"},  # missing project
            {"id": 2, "name": "Bob", "project": "ghost"},  # unknown project
            {"id": 3, "name": "Charlie", "project": "valor"},
            {"id": 3, "name": "Charlie2", "project": "psyoptimal"},  # dup → 2 machines
        ],
        projects={
            "valor": {"machine": "Cowboy"},
            "psyoptimal": {"machine": "Captain"},
        },
    )
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_dm_whitelist(cfg)
    msg = str(exc_info.value)
    assert "id=1" in msg
    assert "ghost" in msg
    assert "id=3" in msg


def test_live_projects_json_passes_validation():
    """The actual config in ~/Desktop/Valor/projects.json must validate."""
    import os

    path = os.path.expanduser("~/Desktop/Valor/projects.json")
    if not os.path.exists(path):
        pytest.skip("Live config not present on this machine")
    with open(path) as f:
        cfg = json.load(f)
    validate_projects_config(cfg)


# ── Telegram groups ────────────────────────────────────────────────────


def test_groups_unique_per_machine_passes():
    cfg = {
        "projects": {
            "valor": {
                "machine": "Cowboy",
                "telegram": {"groups": {"Dev: Valor": {}, "PM: Valor": {}}},
            },
            "popoto": {
                "machine": "Cowboy",
                "telegram": {"groups": {"Dev: Popoto": {}}},
            },
            "psy": {
                "machine": "Captain",
                "telegram": {"groups": {"Dev: Psy": {}}},
            },
        }
    }
    validate_telegram_groups(cfg)


def test_same_group_two_machines_fails():
    cfg = {
        "projects": {
            "a": {"machine": "Cowboy", "telegram": {"groups": {"Shared Room": {}}}},
            "b": {"machine": "Captain", "telegram": {"groups": {"Shared Room": {}}}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_telegram_groups(cfg)
    msg = str(exc_info.value)
    assert "shared room" in msg.lower()
    assert "Cowboy" in msg
    assert "Captain" in msg


def test_group_case_insensitive_collision_fails():
    cfg = {
        "projects": {
            "a": {"machine": "Cowboy", "telegram": {"groups": {"Dev: Foo": {}}}},
            "b": {"machine": "Captain", "telegram": {"groups": {"dev: foo": {}}}},
        }
    }
    with pytest.raises(ConfigValidationError):
        validate_telegram_groups(cfg)


def test_groups_without_machine_fails():
    cfg = {"projects": {"a": {"telegram": {"groups": {"X": {}}}}}}
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_telegram_groups(cfg)
    assert "no 'machine' field" in str(exc_info.value)


# ── Email routing ──────────────────────────────────────────────────────


def test_email_unique_per_machine_passes():
    cfg = {
        "projects": {
            "a": {
                "machine": "Bald",
                "email": {"contacts": ["alice@x.com"], "domains": ["x.com"]},
            },
            "b": {
                "machine": "Captain",
                "email": {"domains": ["y.com"]},
            },
        }
    }
    validate_email_routing(cfg)


def test_same_email_contact_two_machines_fails():
    cfg = {
        "projects": {
            "a": {"machine": "Bald", "email": {"contacts": ["alice@x.com"]}},
            "b": {"machine": "Captain", "email": {"contacts": ["alice@x.com"]}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_email_routing(cfg)
    assert "alice@x.com" in str(exc_info.value)


def test_same_email_domain_two_machines_fails():
    cfg = {
        "projects": {
            "a": {"machine": "Bald", "email": {"domains": ["shared.com"]}},
            "b": {"machine": "Captain", "email": {"domains": ["shared.com"]}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_email_routing(cfg)
    assert "shared.com" in str(exc_info.value)


def test_explicit_contact_overlaps_other_domain_wildcard_fails():
    """An explicit contact on machine A whose domain is claimed by machine B."""
    cfg = {
        "projects": {
            "a": {"machine": "Bald", "email": {"contacts": ["alice@psy.com"]}},
            "b": {"machine": "Captain", "email": {"domains": ["psy.com"]}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_email_routing(cfg)
    msg = str(exc_info.value)
    assert "alice@psy.com" in msg
    assert "psy.com" in msg


def test_email_case_insensitive():
    cfg = {
        "projects": {
            "a": {"machine": "Bald", "email": {"contacts": ["Alice@X.com"]}},
            "b": {"machine": "Captain", "email": {"contacts": ["alice@x.com"]}},
        }
    }
    with pytest.raises(ConfigValidationError):
        validate_email_routing(cfg)


def test_domain_wildcard_prefix_normalized():
    """`*.foo.com`, `@foo.com`, and `foo.com` all collapse to the same key."""
    cfg = {
        "projects": {
            "a": {"machine": "Bald", "email": {"domains": ["*.foo.com"]}},
            "b": {"machine": "Captain", "email": {"domains": ["@foo.com"]}},
        }
    }
    with pytest.raises(ConfigValidationError):
        validate_email_routing(cfg)


def test_email_routing_without_machine_fails():
    cfg = {"projects": {"a": {"email": {"contacts": ["x@y.com"]}}}}
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_email_routing(cfg)
    assert "no 'machine' field" in str(exc_info.value)


# ── Aggregate validator ────────────────────────────────────────────────


def test_validate_projects_config_aggregates_all_errors():
    cfg = {
        "projects": {
            "a": {
                "machine": "Cowboy",
                "telegram": {"groups": {"Shared": {}}},
                "email": {"contacts": ["x@y.com"]},
            },
            "b": {
                "machine": "Captain",
                "telegram": {"groups": {"Shared": {}}},
                "email": {"contacts": ["x@y.com"]},
            },
        },
        "dms": {
            "whitelist": [
                {"id": 1, "name": "Z", "project": "a"},
                {"id": 1, "name": "Z2", "project": "b"},
            ]
        },
    }
    with pytest.raises(ConfigValidationError) as exc_info:
        validate_projects_config(cfg)
    msg = str(exc_info.value).lower()
    # All three categories of error appear in the aggregated message
    assert "id=1" in msg
    assert "shared" in msg
    assert "x@y.com" in msg


# ---------------------------------------------------------------------------
# telegram.bots[] registry validation (issue #1574)
# ---------------------------------------------------------------------------


def test_no_bots_section_passes():
    validate_telegram_bots({"projects": {"a": {"machine": "Cowboy"}}})


def test_valid_single_bot_passes():
    cfg = {
        "projects": {
            "valor": {
                "machine": "Cowboy",
                "telegram": {"bots": [{"id": 8837490628, "username": "b"}]},
            }
        }
    }
    validate_telegram_bots(cfg)


def test_bot_without_machine_fails():
    cfg = {
        "projects": {
            "valor": {"telegram": {"bots": [{"id": 1}]}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_telegram_bots(cfg)
    assert "machine" in str(exc.value)


def test_bot_non_integer_id_fails():
    cfg = {
        "projects": {
            "valor": {"machine": "Cowboy", "telegram": {"bots": [{"id": "nope"}]}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_telegram_bots(cfg)
    assert "non-integer" in str(exc.value)


def test_bot_entry_without_id_fails():
    cfg = {
        "projects": {
            "valor": {"machine": "Cowboy", "telegram": {"bots": [{"username": "b"}]}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_telegram_bots(cfg)
    assert "missing 'id'" in str(exc.value)


def test_same_bot_two_machines_fails():
    cfg = {
        "projects": {
            "a": {"machine": "Cowboy", "telegram": {"bots": [{"id": 99}]}},
            "b": {"machine": "Captain", "telegram": {"bots": [{"id": 99}]}},
        }
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_telegram_bots(cfg)
    msg = str(exc.value)
    assert "id=99" in msg
    assert "multiple machines" in msg


def test_bot_id_also_in_dm_whitelist_fails():
    """The mutual-exclusion invariant: a registered bot must not also resolve
    a project via the DM whitelist, or its no-reply replies would spawn
    runaway sessions (loop hazard #1574)."""
    cfg = {
        "projects": {
            "valor": {"machine": "Cowboy", "telegram": {"bots": [{"id": 555}]}},
        },
        "dms": {"whitelist": [{"id": 555, "name": "Oops", "project": "valor"}]},
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_telegram_bots(cfg)
    msg = str(exc.value)
    assert "id=555" in msg
    assert "dms.whitelist" in msg


def test_validate_projects_config_runs_bots():
    """The aggregated suite includes the bots validator."""
    cfg = {
        "projects": {
            "valor": {"machine": "Cowboy", "telegram": {"bots": [{"id": 7}]}},
        },
        "dms": {"whitelist": [{"id": 7, "name": "X", "project": "valor"}]},
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_projects_config(cfg)
    assert "id=7" in str(exc.value)


# ---------------------------------------------------------------------------
# validate_bot_live_flags — live User.bot probe (issue #1574, criterion 4)
# ---------------------------------------------------------------------------


class _Entity:
    """Minimal stand-in for a resolved Telegram entity exposing the .bot flag."""

    def __init__(self, bot: bool):
        self.bot = bot


def _bot_cfg(bot_id: int = 8837490628) -> dict:
    return {
        "projects": {
            "valor": {
                "machine": "Cowboy",
                "telegram": {"bots": [{"id": bot_id, "username": "b"}]},
            }
        }
    }


@pytest.mark.asyncio
async def test_live_flag_bot_true_passes():
    """A registered id resolving to User.bot=True returns empty quarantine and no detail."""

    async def resolver(bot_id: int):
        assert bot_id == 8837490628
        return _Entity(bot=True)

    quarantine_ids, detail = await validate_bot_live_flags(_bot_cfg(), resolver)
    assert quarantine_ids == set()
    assert detail is None


@pytest.mark.asyncio
async def test_live_flag_human_account_surfaces_mismatch():
    """A confirmed NON-bot id appears in quarantine_ids and detail describes the mismatch."""

    async def resolver(bot_id: int):
        return _Entity(bot=False)

    quarantine_ids, detail = await validate_bot_live_flags(_bot_cfg(), resolver)
    assert 8837490628 in quarantine_ids
    assert detail is not None
    assert "id=8837490628" in detail
    assert "NON-bot" in detail


@pytest.mark.asyncio
async def test_live_flag_unresolvable_id_surfaces_error():
    """An unresolvable id (resolver raises) is NOT quarantined; probe failure noted in detail."""

    async def resolver(bot_id: int):
        raise ValueError("Cannot find any entity corresponding to that id")

    quarantine_ids, detail = await validate_bot_live_flags(_bot_cfg(), resolver)
    # Critical invariant: probe failure must NOT quarantine the id.
    assert 8837490628 not in quarantine_ids
    assert quarantine_ids == set()
    # But it should appear in the detail as a "could not probe" note.
    assert detail is not None
    assert "id=8837490628" in detail
    assert "could not probe" in detail


@pytest.mark.asyncio
async def test_live_flag_no_bots_makes_no_calls():
    """With no registered bots, the resolver is never invoked; returns (set(), None)."""
    calls = []

    async def resolver(bot_id: int):
        calls.append(bot_id)
        return _Entity(bot=True)

    quarantine_ids, detail = await validate_bot_live_flags(
        {"projects": {"a": {"machine": "Cowboy"}}}, resolver
    )
    assert calls == []
    assert quarantine_ids == set()
    assert detail is None


@pytest.mark.asyncio
async def test_live_flag_deduplicates_repeated_ids():
    """A bot id registered under two projects is probed once; valid bot returns empty quarantine."""
    cfg = {
        "projects": {
            "a": {"machine": "Cowboy", "telegram": {"bots": [{"id": 42}]}},
            "b": {"machine": "Cowboy", "telegram": {"bots": [{"id": 42}]}},
        }
    }
    calls = []

    async def resolver(bot_id: int):
        calls.append(bot_id)
        return _Entity(bot=True)

    quarantine_ids, detail = await validate_bot_live_flags(cfg, resolver)
    assert calls == [42]
    assert quarantine_ids == set()
    assert detail is None


@pytest.mark.asyncio
async def test_live_flag_probe_failure_not_quarantined():
    """A resolver that raises for one id leaves it out of quarantine_ids (critique concern #1).

    Only CONFIRMED non-bot ids (User.bot=False) go into quarantine_ids.
    Probe failures (resolver raised) are conservatively left registered.
    """
    failing_id = 9999
    good_bot_id = 1111
    cfg = {
        "projects": {
            "proj_a": {
                "machine": "Cowboy",
                "telegram": {"bots": [{"id": failing_id, "username": "maybe_bot"}]},
            },
            "proj_b": {
                "machine": "Cowboy",
                "telegram": {"bots": [{"id": good_bot_id, "username": "real_bot"}]},
            },
        }
    }

    async def resolver(bot_id: int):
        if bot_id == failing_id:
            raise TimeoutError("Telegram lookup timed out")
        return _Entity(bot=True)  # good_bot_id is a real bot

    quarantine_ids, detail = await validate_bot_live_flags(cfg, resolver)

    # The failing id must NOT be quarantined — we couldn't confirm it's human.
    assert failing_id not in quarantine_ids
    # The good bot id must also not be quarantined — it's a valid bot.
    assert good_bot_id not in quarantine_ids
    assert quarantine_ids == set()
    # The failing id should appear in detail as "could not probe".
    assert detail is not None
    assert str(failing_id) in detail
    assert "could not probe" in detail


# ---------------------------------------------------------------------------
# validate_machine_roster — owner existence (issue #2541)
# ---------------------------------------------------------------------------


def test_no_roster_skips_the_existence_check():
    """Without a declared roster the check is skipped, not guessed at.

    One host cannot see the fleet, and deriving a roster from the config's own
    owner set would only ever confirm itself. The operator learns the roster is
    missing from the ownership summary instead.
    """
    cfg = {"projects": {"ghost": {"machine": "A Machine That Never Existed"}}}
    validate_machine_roster(cfg)
    assert "no 'machines' roster declared" in summarize_ownership(cfg, "Valor the Cowboy")


def test_owner_not_in_roster_fails():
    """The #2541 defect: a project naming a decommissioned host must not pass."""
    cfg = {
        "machines": ["Valor the Cowboy", "Valor the Captain"],
        "projects": {
            "valor": {"machine": "Valor the Cowboy"},
            "psyoptimal": {"machine": "Tom's MacBook Pro"},
        },
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_machine_roster(cfg)
    msg = str(exc.value)
    assert "psyoptimal" in msg
    assert "Tom's MacBook Pro" in msg
    assert "unowned" in msg
    assert "valor" not in msg.split("owned by")[0]


def test_owner_in_roster_passes():
    """Negative control: a roster-resolvable owner is accepted."""
    cfg = {
        "machines": {"Valor the Cowboy": "primary bridge host"},
        "projects": {"valor": {"machine": "Valor the Cowboy"}},
    }
    validate_machine_roster(cfg)


def test_roster_match_is_apostrophe_insensitive():
    """A curly-vs-straight apostrophe must not unown a live host.

    macOS returns U+2019 from ``scutil --get ComputerName`` for a default name;
    the same name hand-typed into projects.json carries U+0027.
    """
    cfg = {
        "machines": ["Tom’s MacBook Air"],
        "projects": {"valor": {"machine": "Tom's MacBook Air"}},
    }
    validate_machine_roster(cfg)
    assert normalize_machine_name("Tom’s MacBook Air") == normalize_machine_name(
        "Tom's MacBook Air"
    )


def test_blank_machine_is_not_a_roster_violation():
    """A missing ``machine`` is the per-shape validators' error, not the roster's."""
    cfg = {
        "machines": ["Valor the Cowboy"],
        "projects": {"orphan": {"machine": ""}, "none": {}},
    }
    validate_machine_roster(cfg)


def test_parked_project_is_not_a_roster_violation():
    """``unassigned`` is a deliberate park, not a host that went missing.

    Parked projects keep their identifiers so they can be handed to a machine
    later. Failing them would make declaring a roster the more destructive
    option — the operator would have to delete real config to get a green run.
    """
    cfg = {
        "machines": ["Valor the Cowboy"],
        "projects": {
            "valor": {"machine": "Valor the Cowboy"},
            "gato": {"machine": "unassigned"},
            "satsol": {"machine": "Unassigned"},
        },
    }
    validate_machine_roster(cfg)


def test_parked_projects_stay_visible_in_the_ownership_summary():
    """Exempt from the hard failure, but never hidden — parked is still unowned."""
    cfg = {
        "machines": ["Valor the Cowboy"],
        "projects": {
            "valor": {"machine": "Valor the Cowboy"},
            "gato": {"machine": "unassigned"},
        },
    }
    assert "unassigned (1)" in summarize_ownership(cfg, "Valor the Cowboy")


def test_validate_projects_config_runs_the_roster_check():
    """The aggregated suite includes the roster validator."""
    cfg = {
        "machines": ["Valor the Cowboy"],
        "projects": {"ghost": {"machine": "Nobody's Laptop"}},
        "dms": {"whitelist": []},
    }
    with pytest.raises(ConfigValidationError) as exc:
        validate_projects_config(cfg)
    assert "Nobody's Laptop" in str(exc.value)


# ---------------------------------------------------------------------------
# summarize_ownership — the report /update prints every run (issue #2541)
# ---------------------------------------------------------------------------


def test_summary_names_this_hosts_projects():
    cfg = {
        "machines": ["Valor the Cowboy", "Valor the Captain"],
        "projects": {
            "valor": {"machine": "Valor the Cowboy"},
            "popoto": {"machine": "Valor the Cowboy"},
            "psyoptimal": {"machine": "Valor the Captain"},
        },
    }
    summary = summarize_ownership(cfg, "Valor the Cowboy")
    assert "claims 2 project(s): popoto, valor" in summary
    assert "other owners in config" in summary
    assert "machines roster" in summary


def test_summary_calls_out_a_host_that_claims_nothing():
    cfg = {"projects": {"valor": {"machine": "Valor the Captain"}}}
    summary = summarize_ownership(cfg, "Valor the Cowboy")
    assert "claims NO projects" in summary


def test_summary_calls_out_roster_hosts_owning_nothing():
    cfg = {
        "machines": ["Valor the Cowboy", "Valor the Bald"],
        "projects": {"valor": {"machine": "Valor the Cowboy"}},
    }
    summary = summarize_ownership(cfg, "Valor the Cowboy")
    assert "roster hosts owning nothing: Valor the Bald" in summary


def test_summary_handles_unresolved_computer_name():
    """``get_machine_name()`` returns "" on failure; the report must still render."""
    cfg = {"projects": {"valor": {"machine": "Valor the Cowboy"}}}
    summary = summarize_ownership(cfg, "")
    assert "unresolved ComputerName" in summary
    assert "claims NO projects" in summary
