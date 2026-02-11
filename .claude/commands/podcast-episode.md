Help me create a new podcast episode following the workflow in `.claude/skills/new-podcast-episode.md`.

**Episode topic:** {{cmd_args}}

Follow the complete workflow which includes:
1. Setup episode structure and files
2. Create differentiated deep research prompts for parallel execution across 4-5 tools (Perplexity, Grok, ChatGPT, Gemini, Claude)
3. Attempt Chrome automation to submit prompts to each research tool
4. Cross-validate research findings when results are complete
5. Create master research briefing organized by topic
6. Provide Opus 4.5 synthesis prompt for narrative creation
7. Generate cover art and NotebookLM audio (user handles these)
8. Process audio (transcribe, chapters, embed)
9. Create publishing metadata and update feed.xml

**IMPORTANT:**
- Create concise, copy-paste-ready research prompts (3 lines each, single newlines only) optimized for each tool's strengths
- DO NOT use any seed research-prompt.md as the actual deep research prompts - create NEW prompts
- When we reach the NotebookLM prompt phase, use the STANDARD TEMPLATE from the skill file without modification
- The NotebookLM prompt defines quality guidelines only - it should NOT be customized with specific content arcs or story prescriptions
