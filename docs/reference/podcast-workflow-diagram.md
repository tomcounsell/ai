# Podcast Episode Workflow -- DB-Backed Flow

> **Business context:** See [Podcasting](~/work-vault/Cuttlefish/Podcasting.md) in the work vault for product overview and key integrations.

## Data Flow Diagram

```mermaid
flowchart TD
    S((Start)) --> s1[setup_episode]

    subgraph P1["Phase 1: Setup"]
        s1 -->|"creates"| a1[p1-brief artifact]
        s1 -->|"creates"| wf[EpisodeWorkflow record]
        s1 -->|"sets"| status1["Episode.status = in_progress"]
    end

    a1 --> s2[run_perplexity_research]

    subgraph P2["Phase 2: Perplexity Research"]
        s2 -->|"creates"| a2[p2-perplexity artifact]
    end

    a2 --> s3[discover_questions]

    subgraph P3["Phase 3: Question Discovery"]
        s3 -->|"reads"| a2
        s3 -->|"creates"| a3[question-discovery artifact]
    end

    a3 --> s4_fork[Targeted Research]

    subgraph P4["Phase 4: Targeted Research (parallel)"]
        s4_fork --> s4a[run_gpt_researcher]
        s4_fork --> s4b[run_gemini_research]
        s4_fork --> s4c["add_manual_research (human)"]
        s4_fork --> s4d[run_mirofish_research]
        s4_fork --> s4e[run_grok_research]
        s4a -->|"creates"| a4a[p2-chatgpt artifact]
        s4b -->|"creates"| a4b[p2-gemini artifact]
        s4c -->|"creates"| a4c["p2-{source} artifact"]
        s4d -->|"creates"| a4d[p2-mirofish artifact]
        s4e -->|"creates"| a4e[p2-grok artifact]
    end

    a4a --> s5_pre["create_research_digest (per source)"]
    a4b --> s5_pre
    a4c --> s5_pre
    a4d --> s5_pre
    a4e --> s5_pre

    subgraph P5["Phase 5: Cross-Validation"]
        s5_pre -->|"creates"| a5d["digest-* artifacts"]
        a5d --> s5[cross_validate]
        s5 -->|"reads all p2-*"| s5
        s5 -->|"creates"| a5[cross-validation artifact]
    end

    a5 --> s6[write_briefing]

    subgraph P6["Phase 6: Master Briefing"]
        s6 -->|"reads"| a5
        s6 -->|"reads"| a5d
        s6 -->|"creates"| a6[p3-briefing artifact]
        a6 --> gate1{"GATE: wave_1\n(200+ words)"}
    end

    gate1 -->|"pass"| s7[synthesize_report]

    subgraph P7["Phase 7: Synthesis"]
        s7 -->|"reads"| a6
        s7 -->|"reads all p2-*"| s7
        s7 -->|"writes"| report["Episode.report_text"]
        s7 -->|"writes"| sources["Episode.sources_text"]
    end

    report --> s8[plan_episode_content]

    subgraph P8["Phase 8: Episode Planning"]
        s8 -->|"reads"| report
        s8 -->|"reads"| a6
        s8 -->|"creates"| a8[content_plan artifact]
        a8 --> gate2{"GATE: wave_2\n(plan exists)"}
    end

    gate2 -->|"pass"| s9[generate_audio]

    subgraph P9["Phase 9: Audio Generation (5-30 min)"]
        s9 -->|"reads"| report
        s9 -->|"reads"| a6
        s9 -->|"reads"| a8
        s9 -->|"NotebookLM API"| nlm["Create notebook + upload + generate"]
        nlm -->|"store_file"| audio_url["Episode.audio_url"]
        nlm -->|"writes"| audio_size["Episode.audio_file_size_bytes"]
    end

    audio_url --> s10a[transcribe_audio]

    subgraph P10["Phase 10: Audio Processing"]
        s10a -->|"Whisper API"| transcript["Episode.transcript"]
        transcript --> s10b[generate_episode_chapters]
        s10b -->|"writes"| chapters["Episode.chapters"]
    end

    chapters --> s11_fork[Publishing Assets]

    subgraph P11["Phase 11: Publishing (parallel)"]
        s11_fork --> s11a[generate_cover_art]
        s11_fork --> s11b[write_episode_metadata]
        s11_fork --> s11c[generate_companions]
        s11a -->|"creates"| a11a[cover-art artifact]
        s11a -->|"writes"| cover["Episode.cover_image_url"]
        s11b -->|"creates"| a11b[metadata artifact]
        s11b -->|"writes"| desc["Episode.description"]
        s11b -->|"writes"| notes["Episode.show_notes"]
        s11c -->|"creates"| a11c["companion-* artifacts"]
        s11c -->|"writes"| comp["Episode.companion_resources"]
    end

    a11b --> s12[publish_episode]
    a11c --> s12

    subgraph P12["Phase 12: Publish"]
        s12 -->|"sets"| status2["Episode.status = complete"]
        s12 -->|"sets"| pub["Episode.published_at = now"]
    end

    pub --> E((Done))
```

## Workflow State Machine

```mermaid
stateDiagram-v2
    [*] --> not_started
    not_started --> running : setup_episode()
    running --> running : advance_step()
    running --> paused_for_human : pause_for_human()
    running --> paused_at_gate : check_quality_gate() fails
    running --> failed : fail_step()
    running --> complete : advance_step("Publish")
    paused_for_human --> running : resume_workflow()
    paused_at_gate --> running : resume_workflow()
    failed --> running : resume_workflow()
    complete --> [*]
```

## Database Writes Per Phase

| Phase | Artifacts Created | Episode Fields Written | Workflow Transition |
|-------|-------------------|----------------------|---------------------|
| 1 Setup | `p1-brief` | `status` | pending -> running |
| 2 Perplexity | `p2-perplexity` | -- | advance to step 2 |
| 3 Question Discovery | `question-discovery` | -- | advance to step 3 |
| 4 Targeted Research | `p2-chatgpt`, `p2-gemini`, `p2-grok`, `p2-mirofish`, `p2-{source}`, `digest-*` | -- | advance to step 4 |
| 5 Cross-Validation | `cross-validation` | -- | advance to step 5 |
| 6 Master Briefing | `p3-briefing` | -- | advance to step 6 + wave_1 gate |
| 7 Synthesis | -- | `report_text`, `sources_text` | advance to step 7 |
| 8 Episode Planning | `content_plan` | -- | advance to step 8 + wave_2 gate |
| 9 Audio Generation | -- | `audio_url`, `audio_file_size_bytes` | advance to step 9 |
| 10 Audio Processing | -- | `transcript`, `chapters` | advance to step 10 |
| 11 Publishing | `metadata`, `companion-summary`, `companion-checklist`, `companion-frameworks` | `description`, `show_notes`, `companion_resources` | advance to step 11 |
| 12 Publish | -- | `status`, `published_at` | running -> complete |

## Quality Gates

| Gate | Location | Check | Blocks |
|------|----------|-------|--------|
| `wave_1` | After Phase 6 | `p3-briefing` exists with 200+ words | Phase 7 (Synthesis) |
| `wave_2` | After Phase 8 | `content_plan` artifact exists | Phase 9 (Audio Generation) |

## Human-in-the-Loop Pause Points

| Trigger | Reason | Resumes When |
|---------|--------|-------------|
| Phase 3 | Perplexity research skipped or failed; manual paste panel shown | User pastes Perplexity result via `PastePerplexityResearchView`; Question Discovery re-enqueued |
| Phase 4 | Automated research complete; review and optionally add manual/Grok research or retry failed sources | Per-source retry via `RetryResearchSourceView`, or `resume_workflow()` |
| Phase 6 | Wave 1 quality gate failure | Human reviews briefing, triggers retry |
| Phase 8 | Wave 2 quality gate failure | Human reviews plan, triggers retry |
| Phase 11 | Cover art not automated | Cover art manually uploaded |
| Any phase | Unrecoverable error | Human investigates and calls `resume_workflow()` |

## Critical Path

```
Setup -> Perplexity (30-120s) -> Question Discovery -> Targeted Research (6-20 min)
-> Cross-Validation -> Master Briefing -> Synthesis -> Episode Planning
-> Audio Generation (5-30 min) -> Transcription (5-10 min) -> Chapters
-> Metadata + Companions -> Publish
```

**Estimated minimum wall-clock time:** 45-75 minutes (dominated by API wait times for research tools, NotebookLM audio generation, and Whisper transcription).

## Parallel Execution Opportunities

**Phase 4 -- Research tools:** GPT-Researcher, Gemini, Together, Claude, Grok, and MiroFish run concurrently. Fan-in uses threshold-based advancement: all tasks must resolve (non-empty content) and at least one must succeed. Failed sources write `[FAILED: error]` artifacts and can be retried individually.

**Phase 5 -- Research digests:** One `create_research_digest` call per `p2-*` artifact, all independent.

**Phase 11 -- Publishing assets:** Cover art, metadata, and companion resources generate independently.
