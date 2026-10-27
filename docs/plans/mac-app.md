# Mac-app agent

Task 8b86d911c550. Tom, 2026-10-27 (Telegram 11736, 11738): a dedicated
subagent with BAMA's skills (github.com/daveebbelaar/bama, MIT, 21 skills
for native macOS apps in Swift/SwiftUI without the Xcode GUI), kept out of
normal work's context. Done when Tom asks for the first app (window tiler,
or a UI for `com.valor.brew-nightly`) and the agent builds it (rule 5).

## Measured before planning (2026-10-27, the Cowboy, Claude Code 2.1.309)

- A probe plugin: a SKILL.md outside the plugin's `skills/` directory is
  listed to no session. The agent `skills:` preload does not load a skill
  marked `disable-model-invocation` (whether named bare or qualified by the
  plugin), and any skill it can load is listed to every session. So the
  skills are read by path. `${CLAUDE_PLUGIN_ROOT}` expands in a plugin
  agent's prompt to the plugin's absolute path. Codex and Pi also read only
  `<plugin>/skills` (`harnesses/codex.py`, `harnesses/pi.py`).
- Toolchain: macOS 26.6.2, Xcode 26.2 selected (`xcode-select -p`), Swift
  6.2.3, `actool` and `notarytool` present, first launch done. BAMA needs
  macOS 26 and Swift 6.2, so both are met. Missing: the standalone Command
  Line Tools in `/Library/Developer/CommandLineTools` are older than macOS
  26 (newest SDK 15.2; brew-nightly warns every night). BAMA builds through
  Xcode's toolchain, so this does not block it. The fix needs an admin
  password: Software Update, or remove that folder. The turn has no sudo.
- Signing: no code-signing identity, no provisioning profiles, and no Xcode
  developer account on this Mac. No Apple item in the vaults m-valor,
  Valor env or Yudame Secrets. Valor's Apple Account on this Mac is
  valor.engels@pm.me. Apple's mail of 2025-12-17 says valor@yuda.me was
  added to an Apple Account as an extra address, presumably that one, so an
  invite sent to valor@yuda.me reaches Valor. Apple (developer.apple.com/help/account): only
  the Account Holder creates Developer ID certificates; Account Holder or
  Admin creates team API keys (needed for notarization); a Developer can
  create its own development certificates.

## What is built

1. **Vendored BAMA** at `skills/valor/bama/`: upstream `skills/`, `specs/`
   and `LICENSE` copied verbatim from commit
   9b986714f248036cdf3bdf458b93cd4b67fe120f (2026-10-26). Upstream's plugin
   manifest, `.claude/`, `AGENTS.md`, banners and `link-skills.sh` stay
   out. It sits beside the plugin's `skills/`, not inside it, so no
   harness lists it.
2. **Agent `skills/valor/agents/mac-app.md`** (`valor:mac-app`). Its
   description: start it to build, change, or package a native Mac app. Tools and model are inherited. Its prompt says
   where the skills are (`${CLAUDE_PLUGIN_ROOT}/bama/skills/<stage>/<name>/SKILL.md`),
   lists the 21 skills one line each, and gives three reading rules: start at `start/bama`; "Call
   the Skill tool with X" means read X's SKILL.md; `${CLAUDE_SKILL_DIR}`
   means that skill's directory. It gives `bama/specs/` as the source of
   build plans (`window-snapper.md` for the tiler). It covers signing (the
   keychain's identity through BAMA's `SIGN_IDENTITY`; Developer ID and
   notary key from 1Password once Tom's invite lands), skips BAMA's Glaido
   advert, and says the web is reached through `valor:web`. The SubagentStart hook already gives it the Brief.
3. **Docs**: `skills/README.md` (Sources: the agent. Imports: BAMA,
   vendored at the pin, MIT. Not here: unchanged for yudame and project
   skills) and `docs/harnesses.md` Skill rendering, a paragraph on the
   agent and why the skills are read by path.

No guard, hook or check is added. The web guard is unchanged.

## Out of scope

Building either app. Installing BAMA as a plugin. Updating the CLT (needs
admin). Checking Valor's other Macs. Auto-updating the vendored copy: a
later re-vendor is a task, re-copying at a new pin.

## Tests

`tests/test_orchestration.py`: the agent's frontmatter names `mac-app`,
and its description names building, changing and packaging. Every skill
path the prompt names exists under `skills/valor/bama/skills`, and every
vendored SKILL.md is named (21). No harness-listed directory holds one:
`skills/valor/skills/` and every `settings.plugin_dirs` `skills/` hold no
BAMA skill name. `LICENSE` is the MIT text.
Live, recorded in the delivery: (a) a scratch `claude -p` with the real
plugin dirs lists its skills, and none is BAMA's; (b) `valor:mac-app`,
asked how to start a new project, names `new-mac-app` and resolves an
existing `new-app.sh` path; (c) `new-app.sh` scaffolds a throwaway app in
`$TMPDIR`, which `swift build` compiles. Full suite under 5 minutes.
Build finding: SwiftPM's manifest sandbox cannot start under the turn's
`sandbox-exec`, so the prompt says to pass `--disable-sandbox`.

## Delivered to Tom with the work

The invite: App Store Connect, Users and Access, People, +: Valor Engels,
valor@yuda.me, role **Admin** (Certificates, Identifiers & Profiles by
default; lets Valor make its development certificate and a team notary
key), plus "Access to Cloud Managed Developer ID Certificate" if offered.
Apple says any email can carry the invite; Valor accepts it signed in as
valor.engels@pm.me, the Apple Account on this Mac (build: whether
valor@yuda.me is a sign-in address there could not be read, since screen
capture returns black in a turn). Cloud signing runs only through Xcode's
archive flow, and BAMA signs with `codesign`, so the .p12 stays the route. The Account Holder creates a Developer ID Application certificate
and saves the .p12 and its password to m-valor as "Apple Developer ID
Application". Also needed: Valor's Apple Account sign-in in m-valor, which
the vaults lack. Then the app questions below, each with its default.

Tiler: (1) one Mac moving between desks, or one per desk (default: one
laptop); (2) layouts learned, not described: arrange windows once at each
desk and press "Save for this desk", keyed by the connected displays
(default); (3) snap on display change and app launch, plus a "Snap now"
menu item (default); (4) apps with several windows: first window to its
place, the rest left alone (default); (5) halves/thirds shortcuts out of v1
(default); (6) name "Desks" (default).
The tiler needs Accessibility permission on Tom's Mac, which macOS ties
to the signature, so it waits on the Developer ID certificate.
Brew UI: (1) a menu-bar icon (green/amber/red) opening last run: time,
upgrades with versions, failures, warnings, next run (default); (2) each
Mac shows its own and the other Macs' last run, read from a status file
the script writes to `~/Desktop/Valor/brew/` (default); (3) read-only plus
"Run now" (default); (4) no notifications (default); (5) first app: this
one, since it needs no Developer ID (default).
