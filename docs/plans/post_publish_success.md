# Post-Publish Success Page (#132)

**Issue**: #132
**Stage**: Publish workflow completion

## Goal

Show celebration screen at workflow step 12 when episode is published (`status='complete'`).

## Solution

Modify `_workflow_step_content.html`:
- Detect when `episode.status == 'complete'` at `current_step == 12`
- Replace phase content with success block containing:
  - "Episode published!" message
  - View Episode link → episode detail page
  - RSS feed link
  - Create Another Episode link → podcast detail
  - Spotify/Apple links (if `podcast.spotify_url` or `podcast.apple_podcasts_url` exist)

## Files

- `apps/public/templates/podcast/_workflow_step_content.html`

## Testing

Manual: Complete episode workflow to step 12, verify success page shows.
