---
name: authenticity-pass
description: "Check a social post draft for human signal (a metric, a constraint, an opinion) before Valor posts it, and say how to add what is missing. Use when drafting or about to publish a LinkedIn or X post, or on any draft for an external audience."
argument-hint: "[draft text or file path]"
---

# Authenticity pass

Read the draft, look for three markers of human signal, and return what you found and how to add what is missing. This is craft for the writer, not a gate: Valor posts as himself, on his own accounts, and decides. Style and AI-writing tells are de-slop's job; this pass asks one thing: does the draft carry signal that someone with real experience wrote it?

## The three markers

**Metric.** A specific, defensible number. "8s → 600ms", "412 restarts in a week", "200 to 3,000 followers in 4 months". Not "significantly faster", "many times", "huge growth".

**Constraint.** An admitted limit, failure or tradeoff. "broke in prod until we added X", "only works if Y", "we tried Z first and it failed", "costs 3× the naive approach". Not pure success framing or "works great".

**Opinion.** A point of view someone could argue against. "most teams underestimate this cost", "I'd do this differently now", "the real bottleneck is Y, not Z". Not neutral description or "both approaches have merit".

A long-form post (over 280 characters) is strongest with all three. A short post or a reply needs at least a metric or a constraint.

## What to return

For each marker: the exact phrase from the draft that carries it, or that it is missing. For each missing one, one concrete suggestion drawn from the source material: not "add a metric", but "the 412 restarts from the incident would carry the metric". If the source has no real number, failure or view to draw on, say so. A post with no angle is better not posted than given a made-up one.

## Why

Platforms measurably push down AI-generic posts: pure-AI posts earn 30-40% fewer LinkedIn impressions (360Brew practitioner data), and X suppresses low-variation automated posting. The three markers are the smallest footprint that tells "AI helped write this" from "AI wrote this instead of someone" (Yudame Research, AI content marketing report, May 2026).
