# RSS Feed Specification for Yudame Research Podcast

## Overview

This document defines the RSS feed standards for the Yudame Research Podcast. The specification follows [podcast-standard.org](https://podcast-standard.org/podcast_standard/) best practices and ensures compatibility with major podcast platforms (Apple Podcasts, Spotify, Google Podcasts, etc.).

**Feed URL:** `https://research.bwforce.ai/podcast/feed.xml`

**Feed Type:** Episodic (newest episodes first)

---

## 1. Required Channel Elements

These elements MUST be present in the `<channel>` section:

### Core Metadata
- `<title>` - Yudame Research Podcast
- `<link>` - https://research.bwforce.ai/
- `<description>` - Brief podcast description (plain text)
- `<language>` - **en-us**

### Contact & Rights
- `<copyright>` - **© 2025 Yudame Inc. For research and educational use.**
- `<managingEditor>` - **valor@yudame.org (Valor Engels)**
- `<webMaster>` - **valor@yudame.org (Valor Engels)**
- `<lastBuildDate>` - Auto-update on each feed modification (RFC 2822 format)
- `<ttl>` - **1440** (podcast apps should check daily)

### iTunes Namespace
- `<itunes:author>` - **Valor Engels**
- `<itunes:summary>` - Longer description for iTunes
- `<itunes:owner>` - Owner contact for platform communication
  - `<itunes:name>` - **Valor Engels**
  - `<itunes:email>` - **valor@yudame.org**
- `<itunes:explicit>` - Content rating (**no**)
- `<itunes:category>` - Multiple categories supported:
  - **Science** (primary)
  - **Software**
  - **Education**
  - **Psychology**
- `<itunes:image>` - Channel cover art URL (see Image Specifications)
- `<itunes:type>` - **episodic** (newest episodes first)

### XML Namespaces
```xml
xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
xmlns:content="http://purl.org/rss/1.0/modules/content/"
xmlns:podcast="https://podcastindex.org/namespace/1.0"
xmlns:research="https://research.bwforce.ai/namespace/1.0"
```

---

## 2. Required Episode Elements

These elements MUST be present in each `<item>`:

### Core Episode Data
- `<title>` - Episode title (follow series format if applicable)
- `<description>` - Plain text episode description with source URLs
- `<content:encoded>` - **HTML-formatted show notes with clickable source links**
  - Use for verified sources to enable click-through in podcast apps
  - Format: `<![CDATA[<p>Description...</p><p><strong>Key Sources:</strong></p><ul><li><a href="url">Source Name</a></li></ul>]]>`
- `<pubDate>` - Publication date in RFC 2822 format
- `<enclosure>` - Audio file reference
  - `url` - Direct link to MP3 file (https://research.bwforce.ai/podcast/episodes/...)
  - `length` - File size in bytes (exact)
  - `type` - **audio/mpeg**
- `<guid>` - **Episode URL** (ensures uniqueness even with same-day publishes)
  - Set `isPermaLink="true"` (default)
  - Format: `https://research.bwforce.ai/podcast/episodes/YYYY-MM-DD-slug/YYYY-MM-DD-slug.mp3`
- `<author>` - **valor@yudame.org (Valor Engels)**

### iTunes Episode Data
- `<itunes:author>` - **Valor Engels**
- `<itunes:duration>` - Episode length in HH:MM:SS format
- `<itunes:explicit>` - **no**
- `<itunes:image>` - Episode-specific cover art (optional but recommended)
- `<itunes:keywords>` - Comma-separated keywords for discoverability
- `<itunes:episodeType>` - **full** (or `trailer`/`bonus` as appropriate)

---

## 3. Recommended Elements

**Note:** Many of these are now REQUIRED in our specification (marked with ✓ REQUIRED).

### Channel Level
- ✓ REQUIRED: `<copyright>` - **© 2025 Yudame Inc. For research and educational use.**
- ✓ REQUIRED: `<lastBuildDate>` - Auto-update on each feed modification
- ✓ REQUIRED: `<ttl>` - **1440** (check daily)
- Optional: `<generator>` - Feed generation tool/attribution

### Episode Level
- ✓ REQUIRED: `<itunes:keywords>` - Comma-separated keywords for discoverability
- ✓ REQUIRED: `<content:encoded>` - Rich HTML show notes with clickable source links
- ✓ REQUIRED: `<itunes:episodeType>` - Episode type: `full`, `trailer`, or `bonus`
- ✓ REQUIRED: `<author>` - **valor@yudame.org (Valor Engels)**

**For Series Episodes (BOTH approaches for maximum compatibility):**
- ✓ REQUIRED: `<itunes:season>` - Season number (for player compatibility)
- ✓ REQUIRED: `<itunes:episode>` - Episode number within season (for player compatibility)
- ✓ REQUIRED: `<research:series>` - Series name string (for internal tracking)

---

## 4. Advanced/Optional Features

### Accessibility (Future Enhancement)
- `<podcast:transcript>` - Link to transcript file
  - **Status:** We generate `_transcript.json` files but don't currently link in feed
  - **Formats:** `application/json`, `text/html`, `text/plain`, `application/srt`
  - **Benefits:** Improves accessibility, SEO, and searchability
  - **Priority:** Medium (easy to implement, moderate value for small team)

### Enhanced Episode Data (Future Enhancement)
- `<podcast:chapters>` - Link to external chapters JSON file
  - **Status:** We generate `_chapters.json` but chapters are embedded in MP3
  - **Benefits:** Additional player compatibility for chapter navigation
  - **Priority:** Low (chapters already work via MP3 metadata)

### Extended Show Notes (Low Priority)
- `<podcast:person>` - Guest/host information with roles
  - **Use case:** Multi-host episodes or guest interviews
  - **Priority:** Low (single-host podcast currently)
- `<podcast:location>` - Geographic context for episodes
  - **Priority:** Very low (not relevant to research content)

---

## 5. Formatting Standards

### Dates
- **Format:** RFC 2822
- **Example:** `Thu, 05 Dec 2025 12:00:00 GMT`
- **Tool:** Use `date -u +"%a, %d %b %Y %H:%M:%S GMT"` for consistent formatting

### Duration
- **Preferred:** `HH:MM:SS` or `MM:SS`
- **Alternative:** Total seconds (integer)
- **Example:** `44:05` for 44 minutes, 5 seconds

### File Sizes
- **Unit:** Bytes (integer)
- **Example:** `42336057` for ~40.4 MB file

### GUIDs
- **Chosen approach:** Use episode audio file URL as GUID
- **Rationale:** Unique even when publishing multiple episodes same day (due to unique slug)
- **Permanence:** URLs should never change once published
- **Format:** `https://research.bwforce.ai/podcast/episodes/YYYY-MM-DD-slug/YYYY-MM-DD-slug.mp3`
- **Attribute:** `isPermaLink="true"` (default, can be omitted)

### HTML Encoding
- Escape special characters in descriptions:
  - `&` → `&amp;`
  - `<` → `&lt;`
  - `>` → `&gt;`
  - `"` → `&quot;`

---

## 6. Series Episode Handling

### Current Series Format
Episodes organized in series subdirectories:
```
podcast/episodes/series-name/epN-topic-slug/
```

### Series Metadata (Dual Approach)

**For maximum compatibility and internal tracking, use BOTH:**

#### iTunes Tags (Broad Player Support)
- `<itunes:season>` - Series number (e.g., `1` for Kindergarten series)
- `<itunes:episode>` - Episode number within series (e.g., `6`)
- `<itunes:episodeType>` - Usually `full`

**Benefits:**
- Apple Podcasts, Spotify, and most players display "Season 1, Episode 6"
- Automatic ordering within series
- User-friendly navigation

#### Custom Namespace (Internal Tracking)
- `<research:series>` - Series name string (e.g., `Kindergarten, from First Principles`)

**Benefits:**
- Explicit series name for database queries
- Internal research organization
- Future API/search functionality

### Example XML
```xml
<item>
  <title>Kindergarten, from First Principles: Ep. 6, Frameworks and the Prepared Environment</title>

  <!-- iTunes compatibility -->
  <itunes:season>1</itunes:season>
  <itunes:episode>6</itunes:episode>
  <itunes:episodeType>full</itunes:episodeType>

  <!-- Internal tracking -->
  <research:series>Kindergarten, from First Principles</research:series>

  <!-- ... other elements ... -->
</item>
```

### Title Format
- **Series episodes:** `Series Name: Ep. N, Topic`
- **Standalone episodes:** `Topic Title`
- Example: `Kindergarten, from First Principles: Ep. 6, Frameworks and the Prepared Environment`

### Episode Ordering
- Feed type is `episodic` (newest first by pubDate)
- Within a series, use consistent pubDate spacing to maintain order
- Podcast apps will use season/episode numbers for in-app series organization

---

## 7. Image Specifications

### Channel Cover Art
- **File:** `podcast/yudame-research-podcast.jpg`
- **Minimum:** 1400×1400 px (Apple Podcasts requirement)
- **Recommended:** 3000×3000 px
- **Format:** JPEG or PNG, RGB color space
- **Aspect Ratio:** 1:1 (square)
- **Current:** 1024×1024 px (below minimum, acceptable but not optimal)

**Brand Guidelines:**
- Primary accent: Yellow (#f5d563) - Yudame brand color
- Background: Blue/gold gradient with design elements
- Typography: Inter font family, bold for "YUDAME RESEARCH"
- Style: Minimalist, contemporary, sophisticated modernism
- Logo: Yellow triangular "A" mark
- Includes: Waveforms, microphone icon, circuit patterns
- URL: research.bwforce.ai displayed at bottom

### Episode Cover Art
- **Location:** Episode directory (`cover.png`)
- **Size:** Same as channel art (1400×1400 minimum)
- **Branding:** Apply consistent podcast branding overlay
  - Yudame logo watermark (from `podcast/yudame-logo.png`)
  - Series/episode text (if applicable)
  - Maintain yellow accent color (#f5d563)
  - Clean, readable typography
  - Negative space for elegance

### Image URLs
- Use versioning parameter for cache busting: `cover.png?v=1`
- Increment version if image is replaced
- Channel art URL: `https://research.bwforce.ai/podcast/yudame-research-podcast.jpg`
- Episode art URL format: `https://research.bwforce.ai/podcast/episodes/[path]/cover.png?v=1`

---

## 8. Quality Checklist

Before publishing an episode, verify:

**Channel Level:**
- [ ] Feed validates as proper XML
- [ ] All required channel elements present
- [ ] Copyright notice up to date
- [ ] lastBuildDate reflects current update

**Episode Level:**
- [ ] Title follows naming convention (series format if applicable)
- [ ] `<description>` includes report link and source URLs (plain text)
- [ ] `<content:encoded>` has HTML-formatted clickable source links
- [ ] pubDate uses correct year and RFC 2822 format
- [ ] Audio file size matches actual file size in bytes (verify with `ls -l`)
- [ ] Duration matches audio file length (verify with `ffmpeg -i`)
- [ ] GUID uses episode audio file URL
- [ ] `<author>` is valor@yudame.org (Valor Engels)
- [ ] Keywords include key topics and frameworks
- [ ] Episode image has proper Yudame branding (if used)
- [ ] For series: `<itunes:season>`, `<itunes:episode>`, and `<research:series>` all present

**Audio File:**
- [ ] MP3 format, 128kbps bitrate
- [ ] Chapters embedded using FFmpeg metadata
- [ ] File size under 100MB (target ~30-45MB)
- [ ] Duration reasonable for content (target 30-45 min)

**Accessibility:**
- [ ] Transcript generated and saved as JSON
- [ ] Chapters provide clear topic segmentation (10-15 chapters)
- [ ] Source links validated and accessible

---

## 9. Future Enhancements Under Consideration

**Priority:** Easy publishing > Maximum compatibility > Enhanced features

### High Priority (Maximum Compatibility)
- [ ] ✓ IMPLEMENTED: HTML show notes via `<content:encoded>`
- [ ] ✓ IMPLEMENTED: Clickable source links in podcast apps
- [ ] ✓ IMPLEMENTED: Series metadata (iTunes + custom namespace)

### Medium Priority (Accessibility & SEO)
- [ ] Transcript linking via `<podcast:transcript>`
  - Already generating JSON transcripts, just need to link them
  - Format: `<podcast:transcript url="..." type="application/json" />`
- [ ] External chapters via `<podcast:chapters>`
  - Already generating chapters JSON, just need to link
  - Provides fallback for players that don't read MP3 metadata

### Low Priority (Advanced Features)
- [ ] Value for value (Bitcoin/Lightning) support
- [ ] Podcast funding links
- [ ] Episode location metadata
- [ ] Embedded images or diagrams in show notes
- [ ] Timestamp links to chapters (requires player support)
- [ ] Guest/host metadata using `<podcast:person>`

### Discovery Optimization (Low Priority)
- [ ] More granular iTunes subcategories
- [ ] Cross-promotion between series
- [ ] Enhanced keyword strategy

---

## 10. Validation & Testing

### Feed Validators
- [Cast Feed Validator](https://castfeedvalidator.com/)
- [Podbase Feed Validator](https://podba.se/validate/)
- Apple Podcasts Connect validation

### Manual Checks
- Load feed in major podcast apps (Apple Podcasts, Spotify, Overcast)
- Verify episode ordering and dates
- Test audio playback and chapter navigation
- Confirm images display correctly
- Validate source links are clickable

---

## Appendix A: Cover Art Generation Prompt

**Task:** Create a high-resolution podcast cover art for the Yudame Research Podcast.

**Specifications:**
- **Dimensions:** 3000×3000 px (square, 1:1 aspect ratio)
- **Format:** PNG or JPEG, RGB color space
- **File size:** Optimize for web (under 500KB if possible)

**Design Requirements:**
- **Primary Brand Color:** Yellow (#f5d563) - use as strategic accent
- **Background:** White or warm gray (#fafafa)
- **Typography:** Inter font family (or similar modern sans-serif)
- **Style:** Minimalist, contemporary, sophisticated modernism
- **Brand Identity:** Incorporate Yudame logo/wordmark

**Design Direction:**
- Clean, negative space design emphasizing elegance
- Modern and approachable for educational/research content
- Suitable for display at small sizes (podcast app icons) and large sizes (featured placements)
- Should convey: intelligence, curiosity, research rigor, approachability

**Content Elements:**
- Podcast title: "Yudame Research Podcast" (or shorter variation if space constrained)
- Subtitle (optional): "First Principles Research for Early Learning"
- Yudame logo or brand mark
- Visual metaphor suggestions:
  - Abstract geometric patterns (low opacity)
  - Minimal iconography representing learning/research/discovery
  - Clean typography-focused design
  - Avoid: literal microphones, children's illustrations, overly academic aesthetics

**Technical Notes:**
- Ensure text is legible at 100×100 px (smallest podcast app display)
- Use sufficient contrast for accessibility
- Avoid fine details that disappear when scaled down
- Test at multiple sizes: 3000×3000, 1400×1400, 600×600, 100×100

**Deliverables:**
1. Final 3000×3000 px PNG/JPEG for podcast feed
2. Optional: 1400×1400 px optimized version for faster loading

---

## References

- **Primary Standard:** [podcast-standard.org](https://podcast-standard.org/podcast_standard/)
- **Apple Podcasts:** [Podcasts Connect Specifications](https://help.apple.com/itc/podcasts_connect/)
- **Podcast Namespace:** [Podcasting 2.0](https://github.com/Podcastindex-org/podcast-namespace)
- **RSS 2.0 Specification:** [RSS Advisory Board](https://www.rssboard.org/rss-specification)
- **Yudame Brand:** [yudame.org](https://yudame.org)
