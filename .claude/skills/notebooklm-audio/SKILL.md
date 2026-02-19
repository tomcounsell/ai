---
name: notebooklm-audio
description: "Manual NotebookLM web interface workflow. This is the fallback approach when the automated local_audio_worker is not available. The NotebookLM Enterprise API is NOT being used."
user-invocable: false
---

# NotebookLM Audio Generation (Manual Fallback)

**Status:** Manual fallback workflow for podcast audio generation.

**Primary Method:** The automated pipeline uses `local_audio_worker` with `notebooklm-mcp-cli`.

**Use This When:** The local audio worker is unavailable and you need to generate audio manually.

---

## When to Use This Skill

Use this skill when:
- NotebookLM Enterprise API is unavailable (no paid subscription)
- API automation fails and fallback is needed
- User explicitly requests manual workflow

---

## Step 1: Generate the Prompt

**CRITICAL:** Always use the script. Never fabricate or modify the prompt.

```bash
cd ~/src/cuttlefish/apps/podcast/tools
python notebooklm_prompt.py ../pending-episodes/EPISODE_PATH/ --copy
```

The script:
- Auto-detects episode title and series name from content_plan.md
- Verifies all 5 required files exist
- Outputs the correct prompt with proper branding
- Copies to clipboard with `--copy` flag (macOS)

**Required files (5 total):**
```
episode-directory/
├── research/p1-brief.md      # Research brief
├── research/p3-briefing.md   # Master briefing
├── report.md                 # Narrative synthesis
├── sources.md                # Validated sources
└── content_plan.md           # Episode structure guide
```

---

## Step 2: Show User the Script Output

Run the script and display its **complete output** to the user. The output includes:
- Episode and series info (auto-detected)
- File checklist with status (✓ or ✗ MISSING)
- The ready-to-paste prompt
- Settings reminder
- NotebookLM link

Example output:
```
============================================================
NOTEBOOKLM MANUAL AUDIO GENERATION
============================================================

Episode: Strategic Selection
Series: Algorithms for Life
Directory: ../pending-episodes/algorithms-for-life/ep2-strategic-selection

📁 Files to Upload (5/5 ready):
  ✓ p1-brief.md
  ✓ report.md
  ✓ p3-briefing.md
  ✓ sources.md
  ✓ content_plan.md

============================================================
📋 NOTEBOOKLM PROMPT (copy-paste ready):
============================================================

Create a two-host podcast episode on: Strategic Selection from our Algorithms for Life series
...

============================================================

⚙️  Settings: Format: Deep Dive | Length: Long

🔗 Open: https://notebooklm.google.com/

✓ Prompt copied to clipboard!
```

---

## Step 3: User Completes Manual Workflow

Guide user through these steps:

1. **Go to** https://notebooklm.google.com/
2. **Create new notebook**
3. **Upload all 5 source files** (shown in the checklist)
4. **Click "Audio Overview" → "Customize"**
5. **Paste the prompt** (already on clipboard from `--copy`)
6. **Settings:** Deep Dive format, Long length
7. **Generate and download audio** (~10-15 minutes)
8. **Save audio file** to episode directory

---

## Step 4: Process Audio

After download, use the `podcast-audio-processing` skill:
- Convert to mp3 if needed
- Transcribe with local Whisper
- Generate chapter markers
- Embed chapters into mp3

---

## Prompt Template Reference

The prompt is defined in `apps/podcast/tools/notebooklm_prompt.py` (single source of truth).

Key elements:
- **References content_plan.md** for structure, hooks, key terms
- **Brand intro:** "Welcome to Yuda Me Research from our [Series] series by Valor Engels..."
- **Brand outro:** "research dot yuda dot me - that's Y-U-D-A dot M-E"
- **Style:** Define terms, cite specifics, distinguish correlation/causation
- **Avoids:** Undefined jargon, fabricated examples, over-hedging

**DO NOT:**
- Duplicate the template elsewhere
- Manually substitute placeholders
- Add episode-specific content arcs (content_plan.md handles this)

---

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Script shows missing files | Complete earlier phases first |
| Can't auto-detect title/series | Use `--title` and `--series` flags |
| Clipboard copy fails | Manually copy from terminal output |
| Audio too short | Check all 5 files uploaded, use Long setting |
