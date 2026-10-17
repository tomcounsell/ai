# tools

Non-core components and vendor-dependent tooling.

## Scope

- Anything the core can run without: vendor API clients, ingestion, search.
- Each tool declares its effect class and reaches the world through the broker. The judgement legs are the exception: `read`-class clients the judgement port calls directly from the kernel process, metered and recorded on the task.
- A tool is extracted on a demonstrated second need, never on a first. A tool unused for ninety days is deleted by default.

Governed by [docs/architecture.md](../docs/architecture.md) (effect classes and the broker) and [docs/tech-stack.md](../docs/tech-stack.md) (the broker's performers, in [docs/performers.md](../docs/performers.md)).

## Performers

- `push_branch.py`: `push_branch` (`act`) pushes one commit of a task's workspace to one branch of its origin, never with force, when requested; never to the task's target branch. `merge` (`act`) is the kernel's push of a passed candidate onto the target branch, performed only when the merge predicate holds; no turn is offered it. `push_branch` pushes to the task's `push_url` (else the origin URL recorded at start) and never carries a credential. `merge` is built from the task's Brief, pushes to the origin URL and target branch recorded there at start, from the kernel mirror with the GitHub token, whose own reach bounds which repositories it can write ([docs/machine.md](../docs/machine.md), Keychain). A performer's `usage` line is what a turn's Brief lists; `refuse` is checked at request and again before the intent.

## Workspace tools

Installed into every turn's `bin/` (`core/workspace.py` `WORKSPACE_TOOLS`, before each turn).

- `look` (`read`): a page's screenshot and DOM ([docs/browser.md](../docs/browser.md)).
- `valor_read.py`, as `valor-telegram` and `valor-slack` (`read`): `read [CHAT] [--search TEXT]` reads or searches a chat's history through its running bridge, with Valor's own account. Standard library and Python 3.9, the turn's `python3`. No send: sends stay broker effects.

## Voice

`voice.py`, the launchd job `com.valor.voice` (`python -m tools.voice run | fetch | --plist`), on `127.0.0.1:settings.voice_port` (8714). One process holds Parakeet for every bridge on the Mac, since it peaks near 1.7 GB resident.

- `POST /transcribe {path}` -> `{text}`: Parakeet TDT 0.6B v3 (`settings.asr_model`, the model Handy runs) through onnx-asr, int8 on the CPU, with Silero VAD so any length is cut into segments; ffmpeg decodes any audio first. The bridges call it through `core.bridge.transcribe` for every `audio/*` attachment, waiting at most `TRANSCRIBE_TIMEOUT_S` (600 s).

The engine is the function `listen`: another model is a setting (`VALOR_ASR_MODEL`), another engine is one function.

Speech out is the yudame present skill's `tts.py`, which a turn runs itself: Kokoro v1.0, the old system's engine and voice. Its files sit in `settings.tts_model_dir` (`~/.cache/kokoro-onnx`) and its voice is `settings.tts_voice` (`am_michael`); the kernel puts both in every turn's env (`KOKORO_MODELS_DIR`, `VALOR_TTS_VOICE`), so another voice is the setting `VALOR_TTS_VOICE`. `fetch` downloads Parakeet into the Hugging Face cache and Kokoro's two files from the kokoro-onnx release.

## Judgement legs

Adapters behind `core/judgement.py`'s `JudgementPort` ([docs/judgement-layer.md](../docs/judgement-layer.md)). Each is built by the composition root with its endpoint, pinned model, timeout, and key; makes one POST per call with no retry; and decodes any response into probabilities per label or a failure with a fixed sentence, so no provider text or key reaches a row or an exception.

- `jev.py`: the primary, TypeSafe's Jev, pinned `jev-1.13.0`.
- `open_weight.py`: the fallback, `qwen/qwen3-235b-a22b-2507` on Parasail at fp8 through OpenRouter, with provider fallback off and a strict JSON schema.

## Imports

- May import: `core/`.
- Imported by: `core/` through the broker and, for the judgement legs, the composition root (`core/__main__.py`); `harnesses/`, `routines/`, and `tests/`. Tools do not import each other's internals.

## Effect classes

- Every tool declares one class per operation: `read`, `propose`, or `act`.
- `read`: queries and fetches with no effect.
- `propose`: workspace writes, branches, drafts, anything that can be withdrawn.
- `act`: merge, send, pay, deploy. Performed by the broker when requested inside the task's ceiling, and reported to Tom.
- A tool that cannot say which class an operation is does not ship.

## Not here

**Governance is restrained by structure, not sentiment.** Before any check, gate, hook, validator, review round, approval step, isolation, sandbox rule, denial, allowlist, refusal, capability removal, or verification layer is added, the change names the mission item it serves and the incident that already happened without it; missing either, it is not added. A bug fix never adds a guard; it fixes the code. Adding governance needs Tom's grant, one per instance, written in the plan file that adds it. Every guard is ledgered with the incident it prevents, the mission item it serves, and a ninety-day expiry; a guard that has not fired by expiry is listed for Tom, and deleted unless he keeps it. The reviewer asks whether the diff adds governance; a yes with no grant in the plan file is a refused merge. The same paragraph, in the same words, sits at the top of `CLAUDE.md`, in the persona rendered into every turn, and in the Not-here section of every directory README. No restraint skill, no hook that blocks hooks, no governance dashboard: each is the disease presenting as the cure.

- Anything the kernel depends on to enforce authority. That is `core/`.
- Comms channels. Those are `bridges/`.
- Harness wrappers. Those are `harnesses/`.
- Validators, linters, or checks that police agent behavior.
- Speculative tools written for a first need.
