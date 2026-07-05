# Phase 5: Optional Surfaces — BYOB, Computer-Use, Generation Model

Load this after the Telegram login and service install phases (Steps 8.5-8.6).

## Step 8.5: Optional BYOB + Computer-Use Install (macOS only)

These two surfaces are operator-opt-in. Skip on non-macOS hosts.

### BYOB (real-Chrome control)

BYOB lets the agent read and act on the user's already-logged-in Chrome via MCP tools (`byob_navigate`, `byob_click`, etc.) -- no `state.json` files in the repo, no per-session re-auth.

```bash
# 1. Install bun if not already present
command -v bun >/dev/null || curl -fsSL https://bun.sh/install | bash

# 2. Clone BYOB to ~/.byob/ and check out the pinned commit
PIN=$(python3 -c "import json; print(json.load(open('config/byob_pin.json'))['commit'])")
if [ ! -d ~/.byob ]; then
  git clone https://github.com/wxtsky/byob ~/.byob
fi
git -C ~/.byob fetch
git -C ~/.byob checkout "$PIN"

# 3. Build + register the native messaging host
cd ~/.byob && bun install && bun run setup
cd ~/src/ai

# 4. Register the BYOB MCP server in ~/.claude.json (idempotent, self-healing)
python -c "from scripts.update import mcp_byob; r = mcp_byob.verify_byob_mcp(write=True); print(r.message)"
```

After install, the user must:
1. Open Chrome → `chrome://extensions` → toggle **Developer mode** ON (top-right) → click **Load unpacked** (top-left) → select `~/.byob/packages/extension/output/chrome-mv3/` (the BYOB extension cannot be auto-installed; this is an operator click-through).
2. **Quit Chrome completely** (`⌘Q` on macOS — closing windows is not enough). Reopen Chrome. Chrome only re-reads the Native Messaging config on full restart.

Verify with BYOB's own diagnostic — this is authoritative across BYOB versions and tells you exactly what's wrong if anything's off:

```bash
cd ~/.byob && bun run doctor
```

Expected output (all green checkmarks):
- ✓ Native Messaging manifest registered
- ✓ Launcher script present
- ✓ Bridge process: pid N, deviceId UUID, uptime Ns
- ✓ IPC socket: `~/.byob/bridges/<deviceId>.sock`

If any line is red, the message points at the exact fix. The most common case is "no live bridge — extension never connected" which means the user hasn't loaded the extension yet, or loaded it into a different Chrome profile than the one being tested.

Note: the IPC socket path is **per-device** (UUID-keyed under `~/.byob/bridges/`), not a fixed `~/.byob/run/byob.sock`. The MCP server discovers the socket at startup; callers should never hardcode the path.

### Computer-Use (bcu, native macOS app control)

bcu drives Slack, Notes, Telegram Desktop, etc. via the macOS Accessibility API without moving the user's cursor. **Prompt the user before installing**:

> Do you want to enable computer-use (lets the agent drive native macOS apps -- Slack, Notes, etc. -- without moving your cursor)?

On **yes**:
```bash
# Write the opt-in sentinel
mkdir -p ~/.config/valor && touch ~/.config/valor/computer-use-enabled

# Resolve the pinned bcu release
TAG=$(python3 -c "import json; print(json.load(open('config/bcu_pin.json'))['release_tag'])")

# Download + verify SHA + install -- /update handles this on every run too,
# so the SETUP-time fetch is just bootstrap. See scripts/update/run.py.
echo "bcu pinned tag: $TAG"
echo "Run: python scripts/update/run.py --full to fetch + install + permission-prompt."
```

After install, the user must grant **two** permissions in System Settings:
- Privacy & Security -> Accessibility -> add `BackgroundComputerUse.app`
- Privacy & Security -> Screen Recording -> add `BackgroundComputerUse.app`

These permissions cannot be granted programmatically.

On **no**: skip everything. Don't write the sentinel; `/update` will leave bcu alone.

## Step 8.6: Generation Model Selection (RAM-based)

Free-text generation (memory titles, the test AI judge, knowledge-doc
summarization) runs on a larger `gemma4:31b` model. Classification (bridge
routing, memory-audit, email triage) runs on the resident `granite4.1:3b` and
needs no choice here. Pick the generation variant from this machine's RAM:

- **RAM ≥ `MIN_LOCAL_GEN_RAM_GB` (48 GB)** → local Apple-Silicon MLX variant
  `gemma4:31b-mlx` (the ~18-20 GB MLX 32B coexists with granite + nomic-embed + OS).
- **RAM < 48 GB** → Ollama Cloud variant `gemma4:31b-cloud` (a lightweight hosted
  pointer that fits any machine, including a 16 GB host).

Write the choice to `~/.zshenv` — **machine-local**, NOT the iCloud-synced
`~/Desktop/Valor/.env` (the vault `.env` would propagate one machine's variant to
every other machine via iCloud and break per-machine semantics). The write is
grep-before-append idempotent:

```bash
RAM_GB=$(( $(sysctl -n hw.memsize) / 1024 / 1024 / 1024 ))
if [ "$RAM_GB" -ge 48 ]; then
  GEN_MODEL="gemma4:31b-mlx"
else
  GEN_MODEL="gemma4:31b-cloud"
fi
LINE="export MODELS__OLLAMA_GENERATION_MODEL=$GEN_MODEL"
grep -qxF "$LINE" ~/.zshenv 2>/dev/null || echo "$LINE" >> ~/.zshenv
echo "Generation model: $GEN_MODEL (RAM=${RAM_GB}GB)"
```

Then ensure the chosen tag (the RAM guard inside `ensure_generation_model()`
re-checks and degrades a misconfigured mlx tag to a soft warning — it never pulls
18 GB on a small host):

```bash
python -c "from config.models import ensure_generation_model; ok,d=ensure_generation_model('$GEN_MODEL'); print(('OK' if ok else 'WARN'), d)"
```

**Cloud-signin warning:** when `GEN_MODEL` ends in `:cloud`, the machine must be
signed in to Ollama Cloud (`ollama list` shows a `:cloud` entry). If not, warn the
user to run `ollama signin` — generation is fail-soft, so this does not block setup.

The launchd worker does not read the shell, so `scripts/install_worker.sh` parses
`MODELS__*` lines from `~/.zshenv` and injects them into the plist
`EnvironmentVariables` block — no extra action needed here.
