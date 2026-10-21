---
description: Update this Mac's Valor to the tip of its branch and restart what the change needs
allowed-tools: Bash(.venv/bin/python -m core update:*)
---

Run, from the repository root:

```bash
.venv/bin/python -m core update --wait
```

It asks the resident kernel to roll this Mac to its branch's tip, the same
request `/update` in Valor's engineering room and the 03:00 update routine
make, and waits up to 15 minutes for it to end or fail.

Tell the user what it printed: the commit it moved from and to (or that it
was already up to date) and each service restarted, or the step that
failed and why. Exit status 1 with "still open" means the kernel is holding
the restart until running jobs end, or no kernel is running
(`launchctl print gui/$(id -u)/com.valor.kernel`); the request stays open
and rolls when the kernel can.
