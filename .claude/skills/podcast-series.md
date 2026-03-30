> **DEPRECATED:** The "series" concept is replaced by the multi-feed model. Each podcast topic gets its own feed. This skill is kept for reference only.

# Podcast Series Planning

This skill covers planning and organizing multi-episode podcast series. For creating individual episodes, see `new-podcast-episode.md`.

## Design System Reference

**All design decisions must follow the locked design specification:**
- **Primary reference:** `docs/design/DESIGN-SPECIFICATION.md` - Exact measurements and non-negotiable patterns
- **Component library:** `docs/design/components/` - Production-ready HTML/CSS components
- **Key principles:**
  - Colors: Black (#000000) text, Salmon (#E8B4A8) accents only
  - Typography: Playfair Display (serif headlines), Inter (body), IBM Plex Mono (technical)
  - Spacing: 8px baseline grid (`--space-1` through `--space-12`)
  - Transitions: 200ms ease (LOCKED)
  - No pink/salmon hover on navigation - black underline only
  - Logo: Yellow "A" icon + "Yudame Research" text inline

When creating or updating series index pages, podcast player components, or any HTML/CSS, reference the component library first.

## When to Use Series Organization

**Use a series subdirectory when:**
- Creating 3+ episodes on a related topic (e.g., cardiovascular health, blockchain fundamentals)
- Planning a cohesive multi-part series with logical progression
- Want to group related episodes for better organization

**Use standalone episode structure when:**
- Creating one-off episodes on different topics
- Episode is not part of a planned series
- Topic doesn't fit into an existing series

## Series Directory Structure

```
apps/podcast/pending-episodes/
├── series-name/                    # Series subdirectory
│   ├── ep1-topic-slug/
│   │   ├── research/              # Research files organized by phase
│   │   │   ├── p1-brief.md
│   │   │   ├── p2-perplexity.md
│   │   │   ├── p2-grok.md
│   │   │   ├── p2-chatgpt.md
│   │   │   ├── p2-gemini.md
│   │   │   ├── p3-briefing.md
│   │   │   └── documents/          # PDFs, papers
│   │   ├── logs/                   # Process logs
│   │   │   ├── prompts.md
│   │   │   └── metadata.md
│   │   ├── tmp/                    # Temporary files (optional)
│   │   │   └── *_transcript.json
│   │   ├── cover.png
│   │   ├── report.md
│   │   ├── report.html             # For series index page
│   │   ├── transcript.html         # For series index page
│   │   ├── sources.md
│   │   ├── YYYY-MM-DD-series-name-episode-1-topic.mp3
│   │   └── YYYY-MM-DD-series-name-episode-1-topic_chapters.json
│   ├── ep2-topic-slug/
│   ├── ep3-topic-slug/
│   └── ep4-topic-slug/
└── YYYY-MM-DD-standalone-topic/    # Standalone episodes at root
```

## Series Naming Conventions

**Directory naming:**
- Series subdirectory: `series-name/` (lowercase, hyphenated, e.g., `cardiovascular-health/`)
- Episode subdirectory: `epX-topic-slug/` (e.g., `ep1-lifestyle/`, `ep2-vo2-max/`)

**Episode title format:**
```
Series Name: Ep. X, Topic
```

**Examples:**
- "Cardiovascular Health: Ep. 1, Lifestyle Foundations"
- "Cardiovascular Health: Ep. 2, VO2 Max"
- "Kindergarten First Principles: Ep. 1, The Developmental Imperative"
- "Kindergarten First Principles: Ep. 2, Play as Pedagogy"

**Audio file naming (remains date-based for chronological sorting):**
```
YYYY-MM-DD-series-name-episode-X-topic.mp3
```

## Planning a New Series

When planning a multi-episode series:

### 1. Series Planning Research

Before defining episodes, conduct a mini research phase to discover what the series should cover:

```
Research [broad topic area].

**Context:** Planning a podcast series for [target audience].

**Research questions:**
- What are the key subtopics or themes within this area?
- What logical progression would help listeners build understanding?
- What are the most important concepts to cover?
- What surprising or counterintuitive findings exist?

**Output:** Overview that would inform how to structure a multi-part series.
```

### 2. Define Series Structure

Based on research, create episode breakdown:

| Ep | Title | Core Territory |
|---|---|---|
| 1 | [Topic] | [Brief description of focus] |
| 2 | [Topic] | [Brief description of focus] |
| ... | ... | ... |

### 3. Create Episode Prompts

For each episode, write a high-level research prompt:
- Keep prompts broad and non-prescriptive
- Let deep research go where the evidence leads
- Focus on the core question, not predetermined structure

**Example:**
> Research [specific topic] from a [relevant discipline] perspective. Explore [key questions]. Investigate [areas of interest].

### 4. Create Series Directory Structure

```bash
mkdir -p ~/src/cuttlefish/apps/podcast/pending-episodes/series-name/ep1-topic-slug
```

### 5. Create Research Prompt Files

For each episode, create `research-prompt.md`:

```markdown
# Episode X: [Title]

## Research Prompt

[The high-level research prompt]

---

## Episode Details

- **Series:** [Series Name]
- **Episode:** X of N
- **Title:** [Full Episode Title]
- **Slug:** [topic-slug]

## To Start This Episode

\`\`\`
/podcast-episode [Research prompt text]
\`\`\`
```

## Example: Cardiovascular Health Series

```
apps/podcast/pending-episodes/cardiovascular-health/
├── ep1-lifestyle/
│   ├── prompts.md
│   ├── research-results.md
│   ├── sources.md
│   ├── report.md
│   ├── publish.md
│   ├── cover.png
│   ├── 2025-11-21-cardiovascular-health-episode-1-lifestyle.mp3
│   └── ...
├── ep2-vo2-max/
├── ep3-hrv/
└── ep4-supplementation/
```

## Feed.xml Entry for Series Episodes

```xml
<item>
  <title>Cardiovascular Health: Ep. 4, Supplementation</title>
  <description>Episode description... Full research report: https://research.bwforce.ai/apps/podcast/pending-episodes/cardiovascular-health/ep4-supplementation/report.md</description>
  <enclosure url="https://research.bwforce.ai/apps/podcast/pending-episodes/cardiovascular-health/ep4-supplementation/2025-11-21-cardiovascular-health-episode-4-supplementation.mp3"
             length="36144828"
             type="audio/mpeg"/>
  <guid>https://research.bwforce.ai/apps/podcast/pending-episodes/cardiovascular-health/ep4-supplementation/2025-11-21-cardiovascular-health-episode-4-supplementation.mp3</guid>
</item>
```

## Migrating Standalone Episodes to Series

If standalone episodes should become a series:

1. **Create series subdirectory:**
   ```bash
   mkdir -p ~/src/cuttlefish/apps/podcast/pending-episodes/series-name
   ```

2. **Move and rename episode directories:**
   ```bash
   mv apps/podcast/pending-episodes/YYYY-MM-DD-old-name apps/podcast/pending-episodes/series-name/ep1-topic-slug
   ```

3. **Update feed.xml:**
   - Change all episode paths to `episodes/series-name/epX-topic-slug/`
   - Normalize all titles to "Series Name: Ep. X, Topic" format

4. **Commit with descriptive message:**
   ```bash
   git add -A
   git commit -m "refactor: Organize [series name] episodes into series subdirectory"
   git push
   ```

## Cover Art for Series

For series episodes, use the branding script with series text:

```bash
cd ~/src/cuttlefish/apps/podcast/tools

python add_logo_watermark.py ../pending-episodes/series-name/epX-slug/cover.png \
  --position top-left \
  --brand "Yudame Research" \
  --series "Series Name" \
  --episode "Ep X - Topic" \
  --border 20 \
  --border-color "#FFC20E"
```

## Series Index Page

Each series should have an `index.html` landing page showcasing all episodes with embedded audio players.

**Location:** `apps/podcast/pending-episodes/series-name/index.html`

**URL:** `https://research.bwforce.ai/apps/podcast/pending-episodes/series-name/`

### Creating a Series Index Page

**IMPORTANT: Use the locked design system components.**

**Required references:**
1. `css/base.css` - Background patterns, series page layout, subscribe buttons (REQUIRED)
2. `docs/design/components/foundation.css` - Design tokens and base styles
3. `docs/design/components/navigation.css` - Header and nav styles
4. `docs/design/components/podcast-player.css` - Episode card styling
5. `docs/design/components/buttons.css` - Button base styles
6. Font Awesome CDN - Icons for subscribe buttons

**Reference implementation:**
- `apps/podcast/pending-episodes/algorithms-for-life/index.html` - Current template (use this as reference)

**Legacy series pages (may need updating):**
- `apps/podcast/pending-episodes/cardiovascular-health/index.html`
- `apps/podcast/pending-episodes/kindergarten-first-principles/index.html`

When creating new series pages, use the component library patterns, not legacy implementations.

### Page Structure

**Use the component library classes from `docs/design/components/`:**

```html
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>[Series Name] - Yudame Research</title>

    <!-- Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&family=Playfair+Display:wght@600;700&family=IBM+Plex+Mono:wght@400&display=swap" rel="stylesheet">

    <!-- Icons -->
    <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/font-awesome/6.5.1/css/all.min.css">

    <!-- Design System (REQUIRED) -->
    <link rel="stylesheet" href="../../../css/base.css">
    <link rel="stylesheet" href="../../../docs/design/components/foundation.css">
    <link rel="stylesheet" href="../../../docs/design/components/navigation.css">
    <link rel="stylesheet" href="../../../docs/design/components/podcast-player.css">
    <link rel="stylesheet" href="../../../docs/design/components/buttons.css">

    <!-- Series page styles are in css/base.css -->
</head>
<body class="bg-graph-paper-light paper-texture">
    <!-- Header -->
    <header class="header">
        <div class="header-container">
            <a href="../../../" class="header-logo" style="display: flex; align-items: center; gap: 12px; text-decoration: none;">
                <img src="../../../podcast/yudame-logo.png" alt="" style="height: 32px;">
                <span style="font-family: var(--font-serif); font-size: var(--text-lg); font-weight: var(--weight-semibold); color: var(--color-black);">Yudame Research</span>
            </a>
            <nav>
                <ul class="nav">
                    <li class="nav-item"><a href="../../../" class="nav-link">Home</a></li>
                    <li class="nav-item"><a href="../../../#podcast" class="nav-link is-active">Podcast</a></li>
                    <li class="nav-item"><a href="../../../#methodology" class="nav-link">Methodology</a></li>
                </ul>
            </nav>
        </div>
    </header>

    <main class="series-content">
        <div class="page-header">
            <h1 class="page-title">[Series Name]</h1>
            <p class="page-tagline">[Series description]</p>
        </div>

        <div class="subscribe-buttons">
            <a href="https://podcasts.apple.com/us/podcast/yudame-research-podcast/id1862329179" class="podcast-button" target="_blank" rel="noopener noreferrer" style="background: #9333ea;">
                <i class="fa-solid fa-podcast"></i> Apple Podcasts
            </a>
            <a href="https://open.spotify.com/show/32xUME8x4FN1DcNwBOrYfc" class="podcast-button" target="_blank" rel="noopener noreferrer" style="background: #1db954;">
                <i class="fa-brands fa-spotify"></i> Spotify
            </a>
            <a href="../../../podcast/subscribe.html" class="podcast-button" style="background: var(--color-gray-600);">
                <i class="fa-solid fa-rss"></i> Other
            </a>
        </div>

        <h2 class="section-title">Episodes</h2>

        <div class="episode-list">
            <!-- Episode card with cover image -->
            <div class="episode">
                <div class="episode-top">
                    <div class="episode-cover">
                        <img src="ep1-slug/cover.png" alt="Episode 1 cover art">
                    </div>
                    <div class="episode-content">
                        <div class="episode-header">
                            <span class="episode-number">Ep 1</span>
                            <span class="episode-title">[Title]</span>
                            <a href="ep1-slug/report.html" class="btn btn-primary btn-small">Full Report</a>
                        </div>
                        <audio controls preload="metadata">
                            <source src="ep1-slug/[audio-file].mp3" type="audio/mpeg">
                        </audio>
                    </div>
                </div>
                <div class="episode-summary">[1-sentence summary]</div>
                <details class="episode-details">
                    <summary>More</summary>
                    <div class="episode-full-description">[Full description]</div>
                </details>
            </div>
            <!-- More episodes... -->
        </div>
    </div>
</body>
</html>
```

### Episode Card Structure

**Standard episode card:**
```html
<div class="episode">
    <div class="episode-top">
        <div class="episode-cover">
            <img src="ep1-slug/cover.png" alt="Episode 1 cover art">
        </div>
        <div class="episode-content">
            <div class="episode-header">
                <span class="episode-number">Ep 1</span>
                <span class="episode-title">Topic</span>
                <a href="ep1-slug/report.html" class="btn btn-primary btn-small">Full Report</a>
            </div>
            <audio controls preload="metadata">
                <source src="ep1-slug/audio-file.mp3" type="audio/mpeg">
            </audio>
        </div>
    </div>
    <div class="episode-summary">Short description.</div>
    <details class="episode-details">
        <summary>More</summary>
        <div class="episode-full-description">Full description.</div>
    </details>
</div>
```

**Coming soon episode (no cover, no audio):**
```html
<div class="episode">
    <div class="episode-header">
        <span class="episode-number">Ep 1</span>
        <span class="episode-title">Topic</span>
    </div>
    <div class="episode-summary">Short description.</div>
    <details class="episode-details">
        <summary>More</summary>
        <div class="episode-full-description">Full description.</div>
    </details>
</div>
```

### Styling Conventions

**All styling MUST use the component library (`docs/design/components/`):**

- **Subscribe buttons:** Three inline buttons (Apple Podcasts purple, Spotify green, Other gray)
  - Apple Podcasts: `background: #9333ea`
  - Spotify: `background: #1db954`
  - Other: `background: var(--color-gray-600)` → links to `podcast/subscribe.html`
- **Episode cards:** Use `.episode` class from `podcast-player.css`
  - `.episode-top` wraps cover + content side by side
  - `.episode-cover` contains 120x120px cover image
  - `.episode-content` contains header + audio player
  - Summary and details are full-width below
- **Episode number badge:** Salmon background (#E8B4A8), black text, rounded pill
- **Episode title:** Playfair Display serif font
- **Full Report button:** `.btn .btn-primary .btn-small` - salmon background, links to report.html
- **Expandable details:** Native HTML `<details>` with `+` / `−` indicator (styled in `podcast-player.css`)
- **Audio players:** Use `preload="metadata"`, positioned below header in episode-content
- **Typography:** All text uses design system tokens (`--font-serif`, `--font-sans`, `--font-mono`)
- **Spacing:** Use CSS custom properties (`--space-1` through `--space-12`)
- **Colors:** Black (#000000) text, Salmon (#E8B4A8) accents ONLY

**IMPORTANT:** Do not create custom CSS. Use the locked component library styles.

### Updating When Episodes Are Published

When an episode goes live:

1. Add `.available` class to the episode div
2. Change `.episode-duration` text from "Coming soon" to actual duration (e.g., "36:13")
3. Add the episode-links div with report and sources links
4. Add the audio element with correct source path
5. Remove "Coming Soon" badge from h2 when all episodes are available

## Working on Series Episodes

Once a series is planned with `research-prompt.md` files in each episode folder:

1. Open the episode's `research-prompt.md`
2. Copy the `/podcast-episode` command
3. Run the command to start the standard episode workflow
4. The episode will be created within the series directory structure
5. Update the series `index.html` to mark the episode as available

## Reviewing and Finalizing Completed Series

After all episodes in a series have been published, you can review and polish the series at any time:

**When to review a series:**
- All episodes are published and live
- Want to ensure series index page is perfect
- Need to verify all episode links are working (report.html, transcript.html)
- Want to add final touches or improvements to the series presentation
- User requests to update or review a series

**Review checklist:**
1. Verify all episodes are marked as `available` in index.html
2. Confirm all episode durations are correct (not "Coming soon")
3. Check that all report.html and transcript.html links work
4. Ensure episode summaries and descriptions are compelling
5. Verify audio players are properly configured
6. Confirm back navigation links work correctly
7. Review overall visual presentation and consistency
8. Check that series description/tagline is accurate

**To initiate a series review:**
```
Review the [series-name] series at @apps/podcast/pending-episodes/[series-name]/
Update the index.html to ensure it's polished and all episodes are properly listed.
```

The series can be updated and improved at any time after completion.
