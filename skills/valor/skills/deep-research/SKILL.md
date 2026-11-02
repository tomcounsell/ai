---
name: deep-research
description: "Deep research on one question with Yudame's Cuttlefish research tools: six deep-research services, claims cross-checked and rebutted, written up as a long report with sources and a confidence on every claim and a short summary on top. Use for 'research this', 'deep research', 'what does the evidence say', or a review that needs several independent sources; not for a quick lookup."
allowed-tools: Bash Read Write
user-invocable: true
---

# Research

Goal: a question that deserves depth comes back as a full report the caller can act on from its summary and defend from its body, without the caller ever handling the research tools.

## Deep or quick

- **Quick** (one fact, a page, a current price, a definition): the harness's own web search, or the web agent where one exists. Seconds, near free.
- **Deep** (a decision, a review, a "what does the evidence say"): this skill. It takes 25-45 minutes and costs a few dollars of research and model credit, stated in the report.

## How a deep run goes

Where the harness has subagents, start the researcher agent with the question, any material for context, and this skill's directory; it returns the summary and the report's path. Otherwise (Codex, Pi) do what it does:

1. Put the caller's material in a file (a Google Sheet read with `gws sheets`, a document as text, notes). It is treated as ground truth: a claim it contradicts resolves toward it.
2. `python3 <this skill's dir>/run.py "<question>" --context FILE --title "<short title>"` returns at once with the run directory. Frame the question to say what to dig into: each book or idea the material relies on, and what it leaves out.
3. Check `<dir>/status.json` in short calls (`sleep 120; cat ...`) until `done` or `failed`; never one long call. On `failed`, its `error` says why: fall back to quick research and say the deep run failed.
4. Read `<dir>/report.md`.

The runner calls Cuttlefish's own research code from a Cuttlefish checkout it keeps up to date, so the research improves as Cuttlefish does. It runs Perplexity first, finds the open questions, sends targeted prompts to ChatGPT, Gemini, Grok, Claude and OpenRouter's Llama in parallel, then digests each, classifies which questions stayed open, cross-checks every claim across sources, runs a rebuttal pass on the weak claims the answer leans on, and writes Cuttlefish's master briefing. A tool that fails or times out is skipped and named; the others carry on.

## The report

`report.md`, in this order: **Summary** (the answer and the claims it rests on, each with confidence and sources), **Findings by topic** (evidence, study quality, sample size), **Every claim** (confirmed by several sources, single-source, contested, with rebuttal outcomes), **Where the evidence is thin**, **Practical steps**, **Counterpoints**, **Sources**, and **Method** (tools run and skipped, time, cost in research tools, analysis steps and total). `raw.json` beside it holds every tool's full text.

Confidence is Cuttlefish's: high when 3+ independent sources agree, medium for 2 or one strong study, low for one source.

When it goes to a person, it is a document: run the de-slop skill on it first when the reader is outside Yudame, then print it as an A4 or Letter PDF by the present skill's PDF recipe (Document form). To make the HTML, `pandoc report.md -f markdown+lists_without_preceding_blankline -s -c print.css --embed-resources -o report.html`: the flag keeps lists that follow a line directly as lists. In a chat, give the summary and send the PDF.

## A podcast episode

Made only on Tom's word, given with the topic or in his answer once the report is back; the research run never waits for that decision. Two podcasts: `yudame-research`, the public Yudame Research podcast, and `yudame-staff`, the unlisted Yudame Staff podcast for private topics (a client, a person, internal business).

`python3 run.py episode <dir> --podcast <slug> [--title TEXT]` returns at once with `<dir>/episode.json` and makes the episode in the background through Cuttlefish's own pipeline: it waits for the report, creates the episode with the report attached as its research (no new web research), runs Cuttlefish's `/research-workflow`, its local audio worker for the audio (NotebookLM), and its server's transcription, assets and publishing. It reads Cuttlefish's production settings from Render with Valor's `RENDER_API_KEY`. Before starting it, refresh NotebookLM's login from the Chrome session: `mcp__byob__browser_get_cookies` for `https://notebooklm.google.com/` to a file, then `python3 ~/.cache/yudame/cuttlefish/scripts/notebooklm_refresh_auth.py < FILE`.

Check `episode.json` in short calls (`sleep 300; cat ...`) for up to 4 hours. `state` ends `published`, with `url` the episode's page, or `failed`, with `error` naming the step: the audio queue held other episodes, NotebookLM's login expired, Cuttlefish paused, or the time ran out.

## Setup

- Read access to `yudame/cuttlefish` through `gh` (the runner clones it to `~/.cache/yudame/cuttlefish`, or uses `CUTTLEFISH_DIR`), and `uv`. An episode also needs `RENDER_API_KEY` (from the environment, else `op://m-valor/RENDER_API_KEY/credential`) and `claude`.
- The keys: `PERPLEXITY_API_KEY`, `OPENAI_API_KEY`, `GEMINI_API_KEY`, `GROK_API_KEY`, `ANTHROPIC_API_KEY` (required: it runs every analysis step), `OPENROUTER_API_KEY`. Each is read with `op read` from the references in `keys.env` (Valor's vault), and the environment's is used only when that read fails. A missing key skips its tool and the report says so.
