---
name: podcast-cover-art
description: Generate podcast cover art with AI and apply branding. Uses Gemini via OpenRouter for image generation with light cream backgrounds, then adds Yudame Research logo and series/episode text using Playfair Display typography. Requires OPENROUTER_API_KEY.
---

# Podcast Cover Art Generation

**Skill name:** `podcast-cover-art`

Generate episode cover art using AI and apply podcast branding in one command.

---

## Quick Start

```bash
cd ~/src/cuttlefish/apps/podcast/tools
python cover_art.py ../pending-episodes/YYYY-MM-DD-slug/
```

This:
- Auto-detects title and series from `content_plan.md`
- Generates AI cover art from `report.md` using Gemini via OpenRouter
- Applies podcast branding (logo, series/episode text)
- Saves to `cover.png` in episode directory

---

## Usage

```bash
# Full generation + branding (default)
python cover_art.py ../pending-episodes/your-episode/

# With explicit series name
python cover_art.py ../pending-episodes/your-episode/ --series "Series Name"

# Custom episode text for overlay
python cover_art.py ../pending-episodes/your-episode/ --episode-text "Ep 5 - Topic"

# Skip AI generation, only apply branding to existing cover.png
python cover_art.py ../pending-episodes/your-episode/ --skip-generate

# Quiet mode
python cover_art.py ../pending-episodes/your-episode/ --quiet
```

---

## Requirements

- `report.md` must exist (used to generate AI image prompt)
- `content_plan.md` should exist (for auto-detecting title/series)
- `OPENROUTER_API_KEY` in environment (for Gemini image generation)
- Playfair Display fonts installed

### First-Time Setup

```bash
# Verify fonts installed
cd ~/src/cuttlefish/apps/podcast/tools
uv run python add_logo_watermark.py --check-fonts

# If missing, install:
mkdir -p ~/Library/Fonts && cd ~/Library/Fonts
curl -L -o playfair.zip "https://gwfh.mranftl.com/api/fonts/playfair-display?download=zip&subsets=latin&variants=600,italic"
unzip -o playfair.zip
```

---

## Output

- `cover.png` - Final branded cover art (1024x1024, ~1MB)
- `logs/cover_generation_*.txt` - Generation log
- `logs/cover_generation_*.json` - Prompt metadata
- `logs/branding_log_*.txt` - Branding log

---

## Cover Art Specifications

| Property | Value |
|----------|-------|
| Dimensions | 1024x1024px |
| Background | Light cream (#F5F1E8) |
| Accents | Black (#000000), Salmon (#E8B4A8) |
| Typography | Playfair Display |
| Branding | Yudame logo + series/episode text |
| Format | PNG, ~500KB-1MB |

---

## Workflow Integration

Cover art runs during the **publishing phase** (Phase 12) alongside feed.xml updates:

```
Phase 12: Publishing
├── Generate cover art (can run in parallel with metadata)
├── Create logs/metadata.md
├── Update feed.xml
└── Validate with podcast-feed-validator
```

This allows cover art to generate while other publishing tasks proceed.
