---
name: podcast-feed-validator
description: Validate podcast RSS feed against specification standards. Checks channel metadata, episode elements, XML structure, and file metadata accuracy. Use after updating feed.xml with a new episode to ensure compliance with RSS spec. References docs/RSS-specification.md for requirements.
---

# Podcast Feed Validator

You are validating a podcast RSS feed against the Yudame RSS specification standards defined in `docs/RSS-specification.md`.

## Input Parameters

You should receive:
- **Feed path:** `podcast/feed.xml`
- **Specification path:** `docs/RSS-specification.md`
- **Optional: Episode to validate** - If specified, validate only the most recent episode. If not specified, validate the entire feed.

## Validation Process

### 1. Read the Specification

Read `docs/RSS-specification.md` to understand:
- Section 1: Required Channel Elements
- Section 2: Required Episode Elements
- Section 3: Recommended Elements
- Section 8: Quality Checklist

### 2. Read the Feed

Read `podcast/feed.xml` to analyze:
- Channel-level metadata
- Episode entries (focus on most recent if specified)
- XML structure and namespaces

### 3. Validate Channel-Level Elements

**Check against Section 1 requirements:**

✓ Core Metadata:
- [ ] `<title>` present
- [ ] `<link>` present and valid URL
- [ ] `<description>` present
- [ ] `<language>` = "en-us"

✓ Contact & Rights:
- [ ] `<copyright>` = "© 2025 Yudame Inc. For research and educational use."
- [ ] `<managingEditor>` = "valor@yuda.me (Valor Engels)"
- [ ] `<webMaster>` = "valor@yuda.me (Valor Engels)"
- [ ] `<lastBuildDate>` present in RFC 2822 format
- [ ] `<ttl>` = "1440"

✓ iTunes Metadata:
- [ ] `<itunes:author>` = "Valor Engels"
- [ ] `<itunes:summary>` present
- [ ] `<itunes:owner>` with name and email (valor@yuda.me)
- [ ] `<itunes:explicit>` = "no"
- [ ] `<itunes:category>` includes: Science, Education, Technology
- [ ] `<itunes:image>` present with valid URL
- [ ] `<itunes:type>` = "episodic"

✓ Namespaces:
- [ ] xmlns:itunes declared
- [ ] xmlns:content declared
- [ ] xmlns:podcast declared
- [ ] xmlns:research declared

### 4. Validate Episode Elements

**For each episode (or most recent if specified), check against Section 2 & 3:**

✓ Core Episode Data:
- [ ] `<title>` present and follows naming convention
- [ ] `<description>` present (plain text with report link)
- [ ] `<content:encoded>` present with HTML CDATA format
  - [ ] Contains clickable source links
  - [ ] Properly formatted with `<![CDATA[...]]>`
- [ ] `<author>` = "valor@yuda.me (Valor Engels)"
- [ ] `<pubDate>` in RFC 2822 format
- [ ] `<enclosure>` with url, length (bytes), type="audio/mpeg"
- [ ] `<guid>` uses episode audio file URL

✓ iTunes Episode Data:
- [ ] `<itunes:author>` = "Valor Engels"
- [ ] `<itunes:duration>` in HH:MM:SS or MM:SS format
- [ ] `<itunes:explicit>` = "no"
- [ ] `<itunes:episodeType>` = "full" (or trailer/bonus)
- [ ] `<itunes:keywords>` present
- [ ] `<itunes:image>` present (optional but recommended)

✓ Series Metadata (if episode is part of a series):
- [ ] `<itunes:season>` present with number
- [ ] `<itunes:episode>` present with number
- [ ] `<research:series>` present with series name

### 5. Validate File Metadata Accuracy

**If validating a specific episode:**
- [ ] Check that file size in `<enclosure length>` matches actual file
  - Use `ls -l apps/podcast/pending-episodes/path/file.mp3 | awk '{print $5}'`
- [ ] Check that duration matches actual audio duration
  - Compare against `_transcript.json` or use `ffmpeg -i file.mp3 2>&1 | grep Duration`

### 6. Validate XML Structure

- [ ] Run `xmllint --noout podcast/feed.xml` to check for XML errors
- [ ] Verify no malformed tags or unclosed elements
- [ ] Check that CDATA sections are properly formatted

### 7. Content Quality Checks

**For the episode description and content:encoded:**
- [ ] Report link included and accessible
- [ ] Source links are actual URLs (not placeholder text)
- [ ] HTML in content:encoded is valid (no unclosed tags)
- [ ] No emoji unless explicitly requested by user
- [ ] Special characters properly escaped (&amp; for &, etc.)

## Output Format

Provide a validation report in this format:

```markdown
# RSS Feed Validation Report

## Feed: podcast/feed.xml
**Validation Date:** YYYY-MM-DD HH:MM:SS

## Channel-Level Validation

### ✅ Passed
- [List elements that passed validation]

### ❌ Failed
- [List elements that failed with specific issues]

### ⚠️ Warnings
- [List recommendations or optional elements missing]

## Episode Validation: [Episode Title]

### ✅ Passed
- [List episode elements that passed]

### ❌ Failed
- [List episode elements that failed with specific issues]

### ⚠️ Warnings
- [List recommendations]

## File Metadata Verification

- **Expected file size:** [bytes from feed]
- **Actual file size:** [bytes from file system]
- **Match:** ✅ Yes / ❌ No

- **Expected duration:** [duration from feed]
- **Actual duration:** [duration from audio file]
- **Match:** ✅ Yes / ❌ No

## XML Structure

- **XML validation:** ✅ Valid / ❌ Invalid
- **Issues found:** [List any XML parsing errors]

## Overall Status

**Feed Status:** ✅ VALID / ⚠️ VALID WITH WARNINGS / ❌ INVALID

**Issues to address:** [Count]
**Warnings:** [Count]

## Action Items

- [ ] [List specific fixes needed]
- [ ] [List recommendations]
```

## Error Handling

If validation fails:
- Provide specific line numbers or element paths where possible
- Suggest corrections based on RSS-specification.md
- Reference the specific section of the specification that defines the requirement

## Success Criteria

A feed is considered **VALID** when:
1. All required channel elements are present and correct
2. All required episode elements are present and correct
3. XML structure is well-formed
4. File metadata matches actual files (if checked)

A feed gets **WARNINGS** when:
1. Optional recommended elements are missing
2. File metadata couldn't be verified
3. Minor formatting inconsistencies exist

A feed is **INVALID** when:
1. Required elements are missing
2. XML is malformed
3. File sizes or durations are significantly wrong
4. Contact information is incorrect
