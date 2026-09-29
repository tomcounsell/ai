# Session Capture

Turn the repeatable process performed in this conversation into a reusable skill. Get the
user's confirmation first, then save it. If a description was given, it is: "$ARGUMENTS".

Done when: the user has approved the SKILL.md, it is saved where they chose, and you have told
them its path, how to invoke it (`/{{skill-name}} [arguments]`), and that they can edit it
directly.

## Analyze the session first

Before asking anything, work out from the conversation:
- the process that was performed;
- its inputs;
- what "finished" looked like (an artifact or state, e.g. "PR open with CI green", not
  "wrote code");
- the tools and permissions it used;
- where the user corrected or steered you. Those corrections become the skill's constraints.

## Interview

Ask every question through AskUserQuestion. The user always gets a free-form "Other" option, so
do not add your own "needs tweaking" choice. Ask only what the session leaves unclear, and keep
it short for simple processes. Cover:
- the name, the description, the goal, and the done-criteria you propose;
- arguments, if the skill needs any;
- inline or `context: fork`. Fork suits self-contained runs; inline suits runs the user steers
  mid-process.
- where to save it: project-only, a cross-repo skill in this repo, or personal
  `~/.claude/skills/`. Follow the repo's skill-context file if it declares placement rules.
- for stages that are not obvious: what each one produces for later stages, how to tell it is
  done, whether the user must confirm first (always for merge, send, delete, and other
  irreversible steps), and whether it can run in parallel;
- hard constraints, and trigger phrases.

## Write, confirm, save

Build the file from [WORKFLOW_TEMPLATE.md](WORKFLOW_TEMPLATE.md) and the body standard in
[SKILL.md](SKILL.md) ("Writing the body", "Model and effort"). Put the tool permission patterns
the session used into `allowed-tools`, which pre-approves them. Show the complete SKILL.md in a
fenced code block, then ask "Does this SKILL.md look good to save?" via AskUserQuestion. Save
only after they approve.
