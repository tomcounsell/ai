# PM Audio Briefing

A daily per-project voice brief delivered to Telegram. Each project listed in
`projects.json` with a `pm_briefing.enabled: true` block fires at the
configured local schedule slot. The audio transcript is **numbers-free**; a
written follow-up containing issue/PR numbers and links is sent immediately
after the voice note.

## Why this exists

PMs scan dashboards, GitHub issue lists, and yesterday's merges every morning.
A 30-second voice note is lower-friction at the start of the day -- listen on
the commute, decide on the bus. The construction logic for the brief already
lives in the `/do-debrief` skill; the missing piece was a scheduler that fires
once per day, per project, into the right Telegram group.

A single global reflection won't work because **different projects care about
different content angles**. Research projects care about new findings,
product projects care about user-facing PRs and bug burn-down, internal
tooling cares about reliability. The reflection accepts per-project
preferences for what to surface vs. drop, and that preference set is
hand-edited in `projects.json`.

## Configuration

Add a `pm_briefing` block to a project in `~/Desktop/Valor/projects.json`
(iCloud-synced). The schema:

```json
"pm_briefing": {
  "enabled": true,
  "schedule": "08:30",
  "timezone": "America/Los_Angeles",
  "target_groups": ["PM: My Project"],
  "angles": {
    "include": ["merges", "open-bugs", "upvote-queue"],
    "exclude": ["plan-only-commits", "lockfile-bumps"]
  },
  "skip_when_empty": false,
  "fallback_message": "Nothing shipped yesterday — three things queued.",
  "voice": "am_michael"
}
```

| Field | Required | Description |
|-------|----------|-------------|
| `enabled` | yes | Set `true` to opt in. Absent or `false` → skipped silently. |
| `schedule` | yes | `HH:MM` (24-hour) in the project's local timezone. |
| `timezone` | yes | IANA tz name (e.g. `America/Los_Angeles`, `UTC`, `Europe/Berlin`). |
| `target_groups` | yes | List of group names. Each name is looked up in `projects.<key>.telegram.groups` to resolve `chat_id`. |
| `angles.include` | yes | Categories to collect. v1 supports `merges`, `open-bugs`, `upvote-queue`. Unknown categories are log-warned and skipped. |
| `angles.exclude` | no | Substrings to filter OUT after collection (case-insensitive substring match against subject/title). |
| `skip_when_empty` | no (default `false`) | If `true`, no message is sent on empty days. If `false`, `fallback_message` is sent as the audio. |
| `fallback_message` | no | Audio text used when collection produced zero items and `skip_when_empty: false`. |
| `voice` | no | Optional voice name passed to `tools.tts.synthesize()`. Defaults to the synthesizer's canonical voice (`am_michael`). |

**Field-name note:** the briefing uses `target_groups` (a list), not `groups`,
to avoid colliding with `projects.<key>.telegram.groups` which is a dict.

## How it runs

The reflection registry (`config/reflections.yaml`) declares one entry,
`pm-audio-briefing`, with `interval: 300` (5 minutes) and
`timeout: 1500` (25 minutes). Every scheduler tick, the callable
(`reflections.pm_audio_briefing.run`) iterates `load_local_projects()` and
runs the per-project pipeline:

1. **Machine ownership filter** -- `scutil --get ComputerName` is captured
   once per process. Projects whose `machine` field doesn't match are
   skipped silently. This is enforced inside the callable because
   `reflections/utils.py:load_local_projects()` filters by working-directory
   existence only, NOT by machine ownership.
2. **Schedule-slot filter** -- `now_in_project_tz` is computed; the slot
   matches if `schedule_hour * 60 + schedule_minute <= now_abs <
   schedule_hour * 60 + schedule_minute + 5`. This handles cross-hour slots
   (e.g. `schedule="00:58"` matches `current="01:02"`).
3. **Idempotency check** -- a per-project `Reflection` record named
   `pm-audio-briefing-{project_key}` tracks last run. Skips if
   `last_run_date == today_in_project_tz AND last_status == "success"`.
   On `last_status == "error"`, retry is allowed.
4. **Atomic SETNX lock** -- `pm-briefing-lock:{project_key}:{today_iso}`
   with TTL 90000s (25h, spans DST). The Reflection record is the durable
   cross-restart signal; the SETNX is the within-tick atomicity primitive.
5. **Pipeline:** `collector.collect → builder.build → delivery.send`.

## Lock-release policy

The lock is released ONLY on pre-side-effect failures. Once delivery has
enqueued the first Redis-outbox payload (the voice-note), the lock is held
until natural 25h TTL expiry to prevent duplicate voice notes on worker
crash mid-delivery.

| Failure point | Lock action | Retry today? |
|---------------|-------------|--------------|
| `collector.collect()` raises | release | yes (next 5-min tick) |
| `builder.build()` raises | release | yes (next 5-min tick) |
| `delivery.send()` raises **before** first `r.rpush` | release | yes |
| `delivery.send()` raises **after** first `r.rpush` (worker crash mid-delivery) | held | no, until tomorrow |
| Clean success | TTL expiry at 25h | no, lock blocks today's re-runs |

Worst case: PM gets the voice note but no written follow-up (because the
voice payload landed in Redis before the worker died and the relay drained
it independently). Acceptable v1 tradeoff -- the voice note is the primary
brief; the follow-up is a backstop.

## "No numbers in audio" guard

The audio transcript must contain zero issue/PR numbers. TTS reads "1195"
as either "one thousand one hundred ninety-five" or "one-one-nine-five" --
both waste listener attention, and the number isn't actionable in audio.

Three layers enforce this:

- **Layer 1 — Pass A prompt** explicitly forbids reciting issue numbers,
  PR numbers, hash-prefixed identifiers, AND bare 4+ digit integers.
  It also forbids forward-looking commitments ("we will", "I'll push",
  etc.) since auto-confirm removes the human safety check, and requires
  the first sentence to be a decision/heads-up, not setup.
- **Layer 2 — prefixed-form regex** `\b(?:issue|pr|#)\s*\d{2,}\b` (case
  insensitive). Catches "issue 1197" and "PR 1197" but NOT "#1197"
  (because `#` is not a word char).
- **Layer 3 — bare 4+ digit regex** `\b\d{4,}\b`. Catches bare `1197`
  alone AND the numeric portion of `#1197`. Allows `$500`, `250 users`,
  `v3.5.2`.

If either Layer 2 or Layer 3 matches the transcript after Pass A, the run
raises `BriefingNumbersDetectedError`, the per-project `Reflection` record
is marked `error`, and **no audio is synthesized and no follow-up is
sent**.

## TTS-failure contract

`tools.tts.synthesize()` does NOT raise on failure -- per
`tools/tts/__init__.py`, it returns a dict with `error` populated:

```python
{"error": "backend unavailable", "path": None, "duration": 0.0,
 "backend": "cloud", "voice": "am_michael", "format": "opus"}
```

When `delivery.send()` sees a truthy `error`, it:
1. enqueues a single text-only `"Daily briefing failed: TTS unavailable"`
   payload per `target_groups` group;
2. raises `BriefingTtsFailedError(result["error"])` so the caller marks
   the per-project `Reflection` record `last_status = "error"`;
3. does NOT enqueue the written follow-up.

PMs see the failure in-channel (one noisy failure beats a silent one) AND
the dashboard surfaces the error.

## DRY_RUN env hatch

Set `DRY_RUN=1` and the callable runs `collect → build` but skips
`delivery.send()`. The would-be transcript and follow-up are written to
`logs/reflections/pm-audio-briefing-<project_key>-<date>.txt` for
inspection. The lock is released on dry-run completion so subsequent
test runs can fire on the same day.

```bash
DRY_RUN=1 python -c "import asyncio; from reflections.pm_audio_briefing import run; print(asyncio.run(run()))"
```

## Categories (v1)

v1 ships exactly 3 collectible categories:

| Category | Source | Notes |
|----------|--------|-------|
| `merges` | `git log --merges --since=yesterday` in `working_directory` | PR number parsed from common merge subject formats. |
| `open-bugs` | `gh issue list --state open --label bug` | Requires `github.org` + `github.repo` in the project. |
| `upvote-queue` | `gh issue list --state open --label upvote` | "Queued for today" lane. |

Adding a new category is one entry in `_COLLECTORS` plus one test --
unknown categories in `angles.include` are log-warned and skipped, never
raised.

Per-project rate-limit math: at most 3 `gh` calls per project per morning,
per machine. With 5 projects on one machine, that's 15 `gh` calls/morning
-- well below the 5000/hr authenticated limit.

## Per-project dashboard rendering

The `pm-audio-briefing-{project_key}` records don't have registry entries;
they're rendered by extending `ui/data/reflections.py` with prefix-merge
logic. A module-level tuple `_PREFIX_EXPANDED_REFLECTIONS = ("pm-audio-briefing",)`
controls which prefixes get per-project expansion. Per-project rows reuse
the parent registry entry's group classification, description, and
interval, but show their own live status.

Adding another prefix-expanded reflection is one tuple entry. If a second
prefix-expanded reflection ever ships, promote the tuple to a YAML-backed
list at that time.

## Failure-mode matrix

| Failure | Detection | Behavior |
|---------|-----------|----------|
| `pm_briefing.enabled: false` | callable | skip silently, no Reflection update |
| Wrong machine | callable | skip silently |
| Outside slot | callable | skip silently (will retry on next 5-min tick) |
| Already succeeded today | callable | skip silently |
| Empty signals + `skip_when_empty: true` | builder | record success-with-noop; lock released |
| Empty signals + `skip_when_empty: false` | builder | send `fallback_message` as audio |
| Unknown category in `angles.include` | collector | log warn, skip the category |
| `git log` / `gh` failure | collector | log warn, return `[]` for that category |
| Pass A "no numbers" violation | builder | raise `BriefingNumbersDetectedError`; record error; no audio synthesized; no follow-up enqueued |
| TTS returns `error` field | delivery | enqueue failure-notice per group; raise `BriefingTtsFailedError`; no follow-up enqueued |
| Pre-side-effect crash | callable | release lock; mark error; retry allowed today |
| Post-side-effect crash | callable | hold lock; mark error; resume tomorrow |
| Missing `chat_id` for a target group | delivery | log warn, skip that group only |

## Operating notes

- **Why interval=300 not interval=86400:** the registry entry is a coarse
  eligibility gate, not a precise scheduler. The per-project schedule
  slot inside the callable is the real once-per-day gate. High `run_count`
  is expected on the parent registry record -- per-project records show
  the actual once-per-day cadence.
- **Stuck-reset window:** the scheduler's stuck-reset at `2 × interval =
  600s` is independent of `timeout: 1500`. If a run takes longer than 600s
  (rare), the parent registry record may briefly show `error` while the
  run is still in flight. Per-project SETNX locks prevent duplicate work
  even if the next tick re-fires after a stuck-reset. v2 enhancement:
  split `run()` into a fast-returning fanout + per-project tasks tracked
  outside the registry.
- **5-minute slot tolerance:** worker hibernations of up to 5 minutes are
  tolerated. Longer outages within the slot will miss that day's brief
  silently. v2 enhancement: detect missed slots and post a delayed brief.

## v2 candidates (out of scope for this work)

- `/do-briefing-feedback` natural-language feedback skill that rewrites
  `angles.include` / `exclude`.
- React-`👍`-to-publish gate (vs. v1 auto-confirm).
- Resume-from-followup on worker restart (split Reflection state into
  `audio_sent_at` + `followup_sent_at` phases).
- Dashboard UI for editing `pm_briefing` config (vs. v1 hand-edit).
- Multi-persona briefings (Dev brief, Teammate brief).

## See also

- `reflections/pm_audio_briefing/` -- the package source.
- [`reflections.md`](./reflections.md) -- registered-callables index.
- [`tts.md`](./tts.md) -- the dual-backend TTS that powers the audio.
- [`telegram-messaging.md`](./telegram-messaging.md) -- the Redis-outbox
  payload pattern the delivery layer replicates.
- `.claude/skills/do-debrief/SKILL.md` -- the user-invocable sibling and
  source-of-truth for the construction rules.
