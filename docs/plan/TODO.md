# Development TODO

## Architecture Issues

### Dependency Coupling
- [ ] **Rethink _handle_missed_messages dependency on notion scout for Anthropic client access** 
  - Location: `integrations/telegram/handlers.py:228-231`
  - Issue: The Telegram message handler borrows the Anthropic client from notion_scout to generate catchup responses
  - Problem: This creates unnecessary coupling between Telegram integration and Notion integration
  - Better approach: Either inject Anthropic client directly or use valor agent system for missed message summaries

## Planned Features

## Technical Debt

## Known Issues