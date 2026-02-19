# `/do-design-review` Skill

The `/do-design-review` skill evaluates a live web UI against 10 premium design criteria and produces a structured quality report with severity ratings. It is the review-time companion to `/frontend-design`.

## Why It Exists

Shipping a functional UI and shipping a polished UI are different things. Code review catches logic errors; design review catches the subtler failures — inconsistent spacing, poor contrast, unclear interaction affordances, or a layout that technically works but feels cheap. `/do-design-review` systematizes that judgment: it visits each page with a headless browser, captures a screenshot, and evaluates the result across 10 dimensions that define premium web UI quality. The output is actionable, not vague — each dimension gets a rating and a specific explanation of what to fix.

## When to Use It

- After `/frontend-design` builds or iterates on a component and you want an independent quality check
- Before opening a PR for any UI-facing change
- When auditing an existing page that has not been reviewed before
- Anytime you want a reproducible, structured design audit rather than a manual eyeball pass

## Usage

```
/do-design-review <url>
/do-design-review <url> --pages <path1>,<path2>,...
```

Examples:

```
/do-design-review http://localhost:8000
/do-design-review https://staging.example.com --pages /,/about,/pricing
```

Without `--pages`, the skill discovers pages automatically by following navigation links from the start URL (up to 6 pages). With `--pages`, only the specified paths are screenshotted.

## Evaluation Dimensions

Each page is scored across 10 dimensions:

| # | Dimension | What Is Evaluated |
|---|-----------|-------------------|
| 1 | **Visual Hierarchy** | Does the layout guide the eye to what matters most? |
| 2 | **Typography** | Font pairing, size scale, weight contrast, readability |
| 3 | **Color & Contrast** | Palette cohesion, sufficient contrast, intentional accent use |
| 4 | **Spacing & Alignment** | Consistent spacing system, grid alignment, breathing room |
| 5 | **Visual Details** | Imagery quality, aspect ratios, decorative elements |
| 6 | **Micro-interactions** | Hover states, transitions, feedback cues |
| 7 | **Consistency** | Repeated patterns, component reuse, visual rhythm |
| 8 | **Trust Signals** | Professional polish, attention to detail, credibility |
| 9 | **Mobile Responsiveness** | Layout integrity across breakpoints (inferred in v1) |
| 10 | **AI Slop Check** | Does it look templated/generic? Would someone ask "which AI made this?" |

## Rating Scale

| Rating | Meaning |
|--------|---------|
| ✅ **Premium** | Exceptional — meets or exceeds the standard for this dimension |
| ⚠️ **Acceptable** | Passes the bar — functional but with room to improve (not a compliment) |
| ❌ **Needs work** | Clear issue — should be addressed before shipping |

## Output Format

For each URL reviewed:

- Evaluation table: all 10 dimensions with rating and specific finding
- **Top 3 Improvements**: prioritized, actionable fixes with specific guidance
- Overall Assessment: 1-2 sentence summary of design quality and direction

## Relationship to Other Skills

| Skill | Role |
|-------|------|
| `/frontend-design` | **Build** — Generates UI from a spec with bold aesthetic direction |
| `/do-design-review` | **Review** — Audits the finished UI against 10 structured criteria |
| `/do-pr-review` | **Code Review** — Evaluates the PR diff, tests, and implementation |

Use `/frontend-design` to build. Use `/do-design-review` to verify before shipping.
