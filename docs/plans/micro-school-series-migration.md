# Micro-Schools Podcast Series Migration Plan

## Overview

Migrate the "Building a Micro-School" podcast series from the research repository into the Cuttlefish Private Podcast Feeds feature. This series provides evidence-based guidance for families and founders creating micro-school programs for children ages 4-9.

**Source Location:** `/Users/valorengels/src/research/podcast/episodes/building-a-micro-school/`

**Target:** Cuttlefish `apps/podcast/` (Private Podcast Feeds feature)

---

## Source Content Inventory

### Series Metadata
- **Series Title:** Building a Micro-School
- **Total Episodes:** 9 planned
- **Target Audience:** Parents, educators, and micro-school founders
- **Episode Duration:** ~30-35 minutes each

### Completed Episodes (3)

| Episode | Directory | Audio | Size | Chapters | Cover Art |
|---------|-----------|-------|------|----------|-----------|
| Ep 1: The Micro-School Kindergarten | `ep1-micro-school-kindergarten/` | `2026-01-02-micro-school-kindergarten.mp3` | 28 MB | 13 chapters | `cover.png` (1.1 MB) |
| Ep 2: Two-Hour Core Model | `ep2-two-hour-core-model/` | `ep2-two-hour-core-model.mp3` | 33 MB | 13 chapters | `cover.png` (1.0 MB) |
| Ep 3: Self-Direction Transition | `ep3-self-direction-transition/` | `2026-01-05-self-direction-transition.mp3` | 30 MB | 13 chapters | `cover.png` (1.2 MB) |

### Completed Episode Assets Per Episode
- **Audio file:** MP3 (~30 MB each)
- **Transcript:** JSON format with timestamps (ep1 only has this)
- **Chapters:** JSON and TXT formats with timestamps
- **Cover art:** PNG (~1 MB each, unique per episode)
- **Content plan:** Markdown with episode structure, talking points, NotebookLM guidance
- **Report:** Markdown with full research synthesis
- **Sources:** Markdown with citations
- **Research folder:** Supporting research documents
- **Logs folder:** Generation logs

### Planning Stage Episodes (6)

| Episode | Directory | Status |
|---------|-----------|--------|
| Ep 4: Technology as Infrastructure | `ep4-technology-infrastructure/` | `research-prompt.md` only |
| Ep 5: Soft Skills Curriculum | `ep5-soft-skills-curriculum/` | `research-prompt.md` only |
| Ep 6: Mentors & Apprenticeships | `ep6-mentors-apprenticeships/` | `research-prompt.md` only |
| Ep 7: Outdoors as Classroom | `ep7-outdoors-classroom/` | `research-prompt.md` only |
| Ep 8: Autonomy & Choice | `ep8-autonomy-choice/` | `research-prompt.md` only |
| Ep 9: Sustainability & Equity | `ep9-sustainability-equity/` | `research-prompt.md` only |

---

## Data Mapping to Private Podcast Feeds Model

### Podcast (Show) Level

| Source | Target Field | Notes |
|--------|--------------|-------|
| Series title | `Podcast.title` | "Building a Micro-School" |
| Series description | `Podcast.description` | Extract from index.html or content_plan |
| Author | `Podcast.author` | TBD |
| Language | `Podcast.language` | "en" |
| Category | `Podcast.category` | "Education" (iTunes category) |
| Cover art | `Podcast.artwork` | Use ep1 cover or create series-level art |
| Slug | `Podcast.slug` | "building-a-micro-school" |

### Episode Level

| Source File | Target Field | Notes |
|-------------|--------------|-------|
| `*.mp3` | `Episode.audio_file` | Upload to Cloudflare R2, link via Upload model |
| Filename date | `Episode.published_at` | Parse from filename (e.g., 2026-01-02) |
| Episode number | `Episode.episode_number` | Extract from directory name |
| `content_plan.md` title | `Episode.title` | Use episode title from content plan |
| `content_plan.md` summary | `Episode.description` | Plain text summary for RSS |
| `content_plan.md` | `Episode.content` | HTML show notes |
| MP3 duration | `Episode.duration_seconds` | Extract via mutagen |
| MP3 file size | `Episode.file_size_bytes` | From file stats |
| `cover.png` | Episode artwork | Per-episode artwork (enhancement) |

### Chapters (Enhancement - Not in Current Model)

The source includes timestamped chapters that could enhance the listening experience:

```json
{
  "startTime": 0,
  "title": "Introduction: The Evidence Gap"
}
```

**Options:**
1. Store chapters in `Episode.content` as HTML list
2. Add `EpisodeChapter` model (future enhancement)
3. Include in RSS as `<podcast:chapters>` (Podcasting 2.0 namespace)

### Transcripts (Enhancement - Not in Current Model)

EP1 includes a full JSON transcript with timestamps. Options:
1. Store as HTML in `Episode.content`
2. Add `EpisodeTranscript` model (future enhancement)
3. Include in RSS as `<podcast:transcript>` link

---

## Migration Steps

### Phase 0: Prerequisites
- [ ] Private Podcast Feeds feature must be implemented (Phase 1-2 minimum)
- [ ] Cloudflare R2 bucket configured (`CLOUDFLARE_R2_BUCKET_NAME` in .env.local)
- [ ] R2 S3-compatible credentials enabled (`CLOUDFLARE_R2_ACCESS_KEY`, `CLOUDFLARE_R2_SECRET_KEY`)
- [ ] Upload model supports audio files

### Phase 1: Create Podcast (Show)
1. Create `Podcast` instance for "Building a Micro-School"
2. Set metadata: title, description, author, category, language
3. Upload series artwork (use ep1 cover or create dedicated)
4. Generate slug: `building-a-micro-school`

### Phase 2: Migrate Completed Episodes (ep1-ep3)
For each completed episode:

1. **Upload audio to Cloudflare R2**
   - Source: `{episode_dir}/*.mp3`
   - Target: `podcasts/{podcast_id}/episodes/{episode_id}.mp3`
   - R2 URL: `https://pub-xxx.r2.dev/podcasts/{podcast_id}/episodes/{episode_id}.mp3`
   - Create `Upload` record

2. **Extract audio metadata**
   - Duration (seconds) via mutagen
   - File size (bytes)

3. **Create Episode record**
   - Title from `content_plan.md`
   - Description: Short summary
   - Content: Format show notes as HTML from content_plan
   - Episode number from directory name
   - Season number: 1
   - Episode type: "full"
   - Published date from filename

4. **Upload episode artwork**
   - Source: `{episode_dir}/cover.png`
   - Link to episode (if model supports per-episode artwork)

5. **Store chapters**
   - Parse `*_chapters.json`
   - Include in show notes HTML or RSS extension

### Phase 3: Create Draft Episodes (ep4-ep9)
For each planning-stage episode:

1. Create Episode record with status=draft
2. Title from `research-prompt.md`
3. Episode number from directory name
4. No audio file (draft state)
5. Content: Import research prompt as planning notes

### Phase 4: Configure Access
1. Create `PodcastSubscription` for intended audience
2. Generate feed URL
3. Test feed in podcast player

---

## Model Enhancements (Optional)

Based on source content, consider these enhancements to the Episode model:

### Per-Episode Artwork
```python
# Episode model addition
artwork = models.ForeignKey(
    Upload,
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="episode_artwork"
)
```

### Chapters Support
```python
# New model or JSON field
class EpisodeChapter(models.Model):
    episode = models.ForeignKey(Episode, on_delete=models.CASCADE)
    start_time_seconds = models.PositiveIntegerField()
    title = models.CharField(max_length=255)
    url = models.URLField(blank=True)  # Optional link
    image = models.ForeignKey(Upload, null=True, blank=True)
```

### Transcript Support
```python
# Episode model addition
transcript_file = models.ForeignKey(
    Upload,
    on_delete=models.SET_NULL,
    null=True,
    blank=True,
    related_name="episode_transcripts"
)
```

---

## Dependencies

### Required Before Migration
1. **Private Podcast Feeds - Phase 1**: Core models (Podcast, Episode, PodcastSubscription)
2. **Private Podcast Feeds - Phase 2**: Feed generation, audio delivery

### Required Packages
Already specified in Private Podcast Feeds plan:
- `feedgen` - RSS feed generation
- `mutagen` - Audio metadata extraction (duration)
- `python-magic` - MIME type detection

---

## Migration Script Outline

```python
# apps/podcast/management/commands/import_micro_school_series.py

import json
from pathlib import Path
from django.core.management.base import BaseCommand
from mutagen.mp3 import MP3

class Command(BaseCommand):
    help = "Import Building a Micro-School podcast series"

    SOURCE_DIR = Path("/Users/valorengels/src/research/podcast/episodes/building-a-micro-school")

    def handle(self, *args, **options):
        # 1. Create Podcast
        podcast = self.create_podcast()

        # 2. Import completed episodes
        for ep_dir in sorted(self.SOURCE_DIR.glob("ep[1-3]-*")):
            self.import_completed_episode(podcast, ep_dir)

        # 3. Create draft episodes
        for ep_dir in sorted(self.SOURCE_DIR.glob("ep[4-9]-*")):
            self.create_draft_episode(podcast, ep_dir)

    def create_podcast(self):
        # Create Podcast instance
        pass

    def import_completed_episode(self, podcast, ep_dir):
        # Find audio file
        mp3_files = list(ep_dir.glob("*.mp3"))
        if not mp3_files:
            return

        audio_path = mp3_files[0]

        # Extract duration
        audio = MP3(audio_path)
        duration_seconds = int(audio.info.length)

        # Parse chapters
        chapter_files = list(ep_dir.glob("*_chapters.json"))
        chapters = []
        if chapter_files:
            chapters = json.loads(chapter_files[0].read_text())

        # Create episode...
        pass

    def create_draft_episode(self, podcast, ep_dir):
        # Read research-prompt.md for title/description
        pass
```

---

## Testing Plan

1. **Feed validation**: Test RSS output with feed validators
2. **Podcast player compatibility**: Test with Apple Podcasts, Overcast, Pocket Casts
3. **Audio playback**: Verify R2 signed URL delivery works
4. **Chapter display**: If implemented, verify chapters appear in supporting players

---

## Success Criteria

1. All 3 completed episodes playable via private feed
2. Episode metadata (title, description, duration) displays correctly
3. Chapter markers functional (if implemented)
4. 6 draft episodes visible in admin for future production
5. Feed URL works in at least 3 major podcast players

---

## Timeline Estimate

| Phase | Effort | Dependencies |
|-------|--------|--------------|
| Phase 1: Create Podcast | 30 min | Private Podcast Feeds Phase 1 |
| Phase 2: Migrate Episodes | 2 hours | Private Podcast Feeds Phase 2 |
| Phase 3: Draft Episodes | 30 min | Phase 2 |
| Phase 4: Configure Access | 30 min | Phase 3 |
| **Total** | **~4 hours** | Requires Private Podcast Feeds |

---

## Open Questions

1. **Per-episode vs series artwork**: Should we add per-episode artwork support to the model?
2. **Chapter format**: Store in Episode.content HTML or add EpisodeChapter model?
3. **Transcript storage**: Add transcript support to model for ep1?
4. **Research materials**: Should report.md and sources.md be stored anywhere?
5. **Series index.html**: Migrate as static page or just use Episode list view?
