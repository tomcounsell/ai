| Term | Means | Not |
| --- | --- | --- |
| Run | One run of the nightly job, from its start line to its done line | job run, update |
| Brew Nightly | The job itself, `com.valor.brew-nightly` | brew job, cron |
| Upgraded | A formula or cask the run moved to a new version | updated |
| Failed | An upgrade the script logged as `FAILED:` | errored |
| Skipped | A cask the run left alone: frozen, or a reason given | ignored |
| Frozen | A cask on the frozen list; skipped on purpose, not a warning | pinned |
| Run Now | Start the job now through launchctl | trigger, kick |
| Open Log | Open the job's log file | view log |
