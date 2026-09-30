---
name: do-presentation
description: "Create or revise a presentation from research, notes, or code, using audience-appropriate structure and verified visual output."
---

# Do Presentation

Resolve purpose, audience, and format. Use the installed presentations skill for PowerPoint or Google Slides; preserve Marp when requested or established in this repo. A generic request for a doc still means Markdown, not a deck.
Research actual source material and organize the story around the audience's decision or understanding. Choose an explanatory, workshop, or reporting structure as appropriate instead of forcing a fixed slide count. Make each slide carry one point, use meaningful diagrams and examples, and place sources near supported claims.
For Marp use [content guidance](CONTENT_GUIDE.md) and [the theme](THEME.md), adapting to the user's brand. Keep statement slides short, concept slides readable, and tables compact. Split crowded slides rather than shrinking type. Use $mermaid-render when a diagram needs an exported image.
Run $de-slop on the completed text, export through the selected format's workflow, and render/inspect every slide for clipping, overflow, missing assets, and contrast. For requested narration use $do-voice-recording; for video use an actually available documented renderer and verify the video. Deliver editable sources and requested exports with an honest validation summary.
