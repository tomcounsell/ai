# Sourced by Claude Code ahead of each Bash command of a turn whose client
# has its own 1Password service account (`CLAUDE_ENV_FILE`, set by
# `core/__main__.py` `_client_env`). It runs after the shell's startup files,
# so the client's token outlives a ~/.zshenv that sources the vault `.env`.
export OP_SERVICE_ACCOUNT_TOKEN="$VALOR_CLIENT_OP_TOKEN"
