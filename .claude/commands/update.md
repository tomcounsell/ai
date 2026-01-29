# Update & Restart

Pull the latest changes from the remote repository and restart the bridge service.

## Steps

1. **Pull latest changes**
   ```bash
   cd /Users/valorengels/src/ai && git pull --ff-only
   ```
   - If the pull fails due to local changes, stash them first with `git stash`, pull, then `git stash pop`.
   - If the pull fails due to diverged branches, stop and inform the user. Do NOT force-pull.

2. **Install any new dependencies**
   ```bash
   cd /Users/valorengels/src/ai && .venv/bin/pip install -r requirements.txt --quiet
   ```
   - Only run this if `requirements.txt` was modified in the pulled changes (check `git diff HEAD@{1} --name-only` for `requirements.txt`).

3. **Restart the bridge service**
   ```bash
   /Users/valorengels/src/ai/scripts/valor-service.sh restart
   ```

4. **Verify the service is running**
   ```bash
   sleep 2 && /Users/valorengels/src/ai/scripts/valor-service.sh status
   ```

5. **Report results** to the user: what was pulled (summary of commits), whether dependencies were updated, and whether the service restarted successfully.
