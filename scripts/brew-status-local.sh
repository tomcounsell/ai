# Valor's part of Brew Status's nightly job (yudame/brew-status): sourced by
# its nightly.sh as ~/.config/brew-status/local.sh, a link to this file
# (docs/rollout.md), so a change here reaches every Mac with its update.

# The shared app list and frozen list, in the iCloud vault.
VAULT_BREW="${HOME}/Desktop/Valor/brew"

# Casks not in the Brewfile (never installed fresh) that must also never be
# upgraded where they are installed: unused, or their installer needs sudo.
if [[ -f "$VAULT_BREW/brew-unmanaged.txt" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(echo "$line" | tr -d '[:space:]')"
    [[ -n "$line" ]] && FROZEN+=("$line")
  done < "$VAULT_BREW/brew-unmanaged.txt"
else
  log "WARNING: no vault frozen list at $VAULT_BREW/brew-unmanaged.txt"
fi

VALOR_PY="${HOME}/src/ai/.venv/bin/python"

before_upgrades() {
  # The Brewfile is a floor: install what is missing, never upgrade (the
  # upgrade loops do that) and never clean up.
  if [[ -f "$VAULT_BREW/Brewfile" ]]; then
    brew bundle install --file "$VAULT_BREW/Brewfile" --no-upgrade || log "bundle install returned nonzero"
  else
    log "WARNING: no vault Brewfile at $VAULT_BREW/Brewfile"
  fi
  PY_BEFORE=$(realpath "$VALOR_PY" 2>/dev/null || true)
}

# Valor's services run on Homebrew's Python, and an upgrade deletes the
# version they started on: their next import fails (2026-10-23, every turn
# failed for seven hours). When it moved, restart the running ones on it.
after_formulae() {
  [[ -n "$PY_BEFORE" && "$(realpath "$VALOR_PY" 2>/dev/null)" != "$PY_BEFORE" ]] || return 0
  local job
  for job in $(launchctl list | awk '$1 != "-" && $3 ~ /^com\.valor\./ {print $3}'); do
    launchctl kickstart -k "gui/$(id -u)/$job" && log "restarted $job on the upgraded Python"
  done
}

# Valor's turns are headless and load the yudame skills from the marketplace
# clone, so the marketplace and plugins are added here; nightly.sh updates them.
after_upgrades() {
  command -v claude >/dev/null 2>&1 || { log "WARNING: claude not on PATH; yudame plugins not checked"; return 0; }
  claude plugin marketplace list 2>/dev/null | grep -q "yudame/skills" \
    || claude plugin marketplace add yudame/skills || log "FAILED: marketplace add yudame/skills"
  local p
  for p in yudame@yudame yudame-dev@yudame; do
    claude plugin list 2>/dev/null | grep -q "❯ $p\$" || claude plugin install "$p" || log "FAILED: install $p"
  done
  local cs="${HOME}/.claude/settings.json"
  if command -v jq >/dev/null 2>&1 && [[ "$(jq -r '.extraKnownMarketplaces.yudame.autoUpdate // false' "$cs" 2>/dev/null)" != true ]]; then
    jq '.extraKnownMarketplaces.yudame = {source: {source: "github", repo: "yudame/skills"}, autoUpdate: true}' "$cs" > "$cs.new" \
      && mv "$cs.new" "$cs" && log "SETUP: yudame marketplace autoUpdate on"
  fi
}
