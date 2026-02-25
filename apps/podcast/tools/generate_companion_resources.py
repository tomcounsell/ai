#!/usr/bin/env python3
"""
Generate companion resources for podcast episodes (Wave 4, Task C3.1).

Creates actionable companion materials from episode content:
- One-page summary/cheat sheet (Markdown)
- Action checklist (Markdown)
- Framework extraction (Markdown for manual diagram creation)

Usage:
    python generate_companion_resources.py ../pending-episodes/2025-12-26-topic-slug/
    python generate_companion_resources.py ../pending-episodes/2025-12-26-topic-slug/ --summary-only
    python generate_companion_resources.py ../pending-episodes/2025-12-26-topic-slug/ --dry-run

Prerequisites:
    - report.md exists in episode directory
    - Optionally: content_plan.md for additional structure
    - ANTHROPIC_API_KEY in environment for AI summarization (optional)

The script will:
    1. Read report.md and extract key content
    2. Generate one-page summary with key points
    3. Extract actionable items into checklist
    4. Identify frameworks for diagram creation
    5. Save outputs to companion/ subdirectory
"""

import argparse
import re
import sys
from datetime import datetime
from pathlib import Path


def extract_key_sections(content: str) -> dict:
    """Extract key sections from report.md."""
    sections = {}

    # Find all ## headings and their content
    heading_pattern = r"^## (.+?)$"
    headings = list(re.finditer(heading_pattern, content, re.MULTILINE))

    for i, match in enumerate(headings):
        heading = match.group(1).strip()
        start = match.end()
        end = headings[i + 1].start() if i + 1 < len(headings) else len(content)
        section_content = content[start:end].strip()
        sections[heading] = section_content

    return sections


def extract_takeaways(content: str) -> list:
    """Extract explicit takeaways from content."""
    takeaways = []

    # Look for numbered lists, key points, takeaways sections
    patterns = [
        r"(?:Key )?[Tt]akeaway[s]?:?\s*\n((?:[-*\d.]+\s+.+\n?)+)",
        r"(?:Key )?[Pp]oint[s]?:?\s*\n((?:[-*\d.]+\s+.+\n?)+)",
        r"(?:In summary|To summarize)[,:]?\s*\n((?:[-*\d.]+\s+.+\n?)+)",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content, re.MULTILINE)
        for match in matches:
            items = re.findall(r"[-*\d.]+\s+(.+)", match)
            takeaways.extend(items)

    return takeaways[:10]  # Limit to top 10


def extract_action_items(content: str) -> list:
    """Extract actionable items from content."""
    actions = []

    # Look for actionable language
    action_patterns = [
        r"(?:should|must|need to|can|try to|consider|recommend)\s+(.+?)[.!]",
        r"(?:Step \d+|First|Second|Third|Finally)[:\s]+(.+?)[.!]",
        r"(?:Protocol|Framework|Process):\s*(.+?)[.!]",
    ]

    for pattern in action_patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        actions.extend(matches)

    # Deduplicate and clean
    seen = set()
    clean_actions = []
    for action in actions:
        action = action.strip()
        if action and len(action) > 10 and action.lower() not in seen:
            seen.add(action.lower())
            clean_actions.append(action)

    return clean_actions[:15]  # Limit to 15 items


def extract_frameworks(content: str) -> list:
    """Extract named frameworks and models from content."""
    frameworks = []

    # Look for framework indicators
    patterns = [
        r"(?:the )?([A-Z][A-Za-z]+(?:\s+[A-Z][A-Za-z]+)*)\s+(?:framework|model|method|approach|protocol|system)",
        r'(?:framework|model|method|approach|protocol|system)\s+(?:called|named|known as)\s+["\']?([^"\',.]+)',
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        frameworks.extend(matches)

    # Deduplicate
    seen = set()
    clean_frameworks = []
    for fw in frameworks:
        fw = fw.strip()
        if fw and len(fw) > 2 and fw.lower() not in seen:
            seen.add(fw.lower())
            clean_frameworks.append(fw)

    return clean_frameworks[:5]  # Limit to 5


def extract_statistics(content: str) -> list:
    """Extract key statistics and numbers from content."""
    stats = []

    # Look for statistics with context
    patterns = [
        r"(\d+(?:\.\d+)?%[^.]*\.)",
        r"(\$[\d,]+(?:\.\d+)?[^.]*\.)",
        r"(\d+(?:\.\d+)?x[^.]*\.)",
        r"(studies? (?:show|found|reveal)[^.]*\.)",
    ]

    for pattern in patterns:
        matches = re.findall(pattern, content, re.IGNORECASE)
        stats.extend(matches)

    return stats[:10]  # Limit to 10


def generate_summary(
    episode_title: str, sections: dict, takeaways: list, stats: list, frameworks: list
) -> str:
    """Generate one-page summary markdown."""
    lines = []

    lines.append(f"# {episode_title}: One-Page Summary")
    lines.append("")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d')}*")
    lines.append("")

    # Core Message
    lines.append("## Core Message")
    lines.append("")
    if "Introduction" in sections:
        intro = sections["Introduction"][:500]
        lines.append(intro.split("\n\n")[0])
    elif "Overview" in sections:
        overview = sections["Overview"][:500]
        lines.append(overview.split("\n\n")[0])
    else:
        lines.append("[Add 1-2 sentence summary of the episode's main thesis]")
    lines.append("")

    # Key Takeaways
    lines.append("## Key Takeaways")
    lines.append("")
    if takeaways:
        for i, takeaway in enumerate(takeaways[:5], 1):
            lines.append(f"{i}. {takeaway}")
    else:
        lines.append("1. [Key takeaway 1]")
        lines.append("2. [Key takeaway 2]")
        lines.append("3. [Key takeaway 3]")
    lines.append("")

    # Key Statistics
    if stats:
        lines.append("## Key Statistics")
        lines.append("")
        for stat in stats[:5]:
            stat = stat.strip()
            if stat:
                lines.append(f"- {stat}")
        lines.append("")

    # Frameworks
    if frameworks:
        lines.append("## Frameworks & Models")
        lines.append("")
        for fw in frameworks:
            lines.append(f"- **{fw}**: [Brief description]")
        lines.append("")

    # Quick Reference
    lines.append("## Quick Reference")
    lines.append("")
    lines.append("| Concept | Definition |")
    lines.append("|---------|------------|")
    lines.append("| [Term 1] | [Definition] |")
    lines.append("| [Term 2] | [Definition] |")
    lines.append("| [Term 3] | [Definition] |")
    lines.append("")

    # Action Items
    lines.append("## Immediate Actions")
    lines.append("")
    lines.append("1. **Today**: [First action to take]")
    lines.append("2. **This Week**: [Second action to take]")
    lines.append("3. **This Month**: [Third action to take]")
    lines.append("")

    lines.append("---")
    lines.append("")
    lines.append("*For full research and citations, see the complete report.*")

    return "\n".join(lines)


def generate_checklist(episode_title: str, actions: list, takeaways: list) -> str:
    """Generate action checklist markdown."""
    lines = []

    lines.append(f"# {episode_title}: Action Checklist")
    lines.append("")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d')}*")
    lines.append("")

    lines.append("## Implementation Checklist")
    lines.append("")

    if actions:
        for action in actions[:10]:
            lines.append(f"- [ ] {action}")
    else:
        lines.append("- [ ] [Action item 1]")
        lines.append("- [ ] [Action item 2]")
        lines.append("- [ ] [Action item 3]")
    lines.append("")

    lines.append("## Key Points to Remember")
    lines.append("")
    if takeaways:
        for takeaway in takeaways[:5]:
            lines.append(f"- [ ] Understood: {takeaway}")
    else:
        lines.append("- [ ] Understood: [Key point 1]")
        lines.append("- [ ] Understood: [Key point 2]")
    lines.append("")

    lines.append("## Progress Tracking")
    lines.append("")
    lines.append("| Date | Action Taken | Result |")
    lines.append("|------|--------------|--------|")
    lines.append("| | | |")
    lines.append("| | | |")
    lines.append("| | | |")
    lines.append("")

    lines.append("## Notes")
    lines.append("")
    lines.append("[Your notes here]")

    return "\n".join(lines)


def generate_framework_doc(episode_title: str, frameworks: list, content: str) -> str:
    """Generate framework documentation for diagram creation."""
    lines = []

    lines.append(f"# {episode_title}: Frameworks for Visualization")
    lines.append("")
    lines.append(f"*Generated: {datetime.now().strftime('%Y-%m-%d')}*")
    lines.append("")
    lines.append("Use this document to create visual diagrams of key frameworks.")
    lines.append("")

    if frameworks:
        for fw in frameworks:
            lines.append(f"## {fw}")
            lines.append("")

            # Try to find context about this framework in the content
            pattern = rf"{re.escape(fw)}[^.]*\.[^.]*\."
            matches = re.findall(pattern, content, re.IGNORECASE)
            if matches:
                lines.append(matches[0])
            else:
                lines.append("[Description of framework]")
            lines.append("")

            lines.append("### Components")
            lines.append("")
            lines.append("1. [Component 1]")
            lines.append("2. [Component 2]")
            lines.append("3. [Component 3]")
            lines.append("")

            lines.append("### Diagram Notes")
            lines.append("")
            lines.append("- Type: [Flowchart / Matrix / Cycle / Hierarchy]")
            lines.append("- Key relationships: [How components connect]")
            lines.append("- Visual emphasis: [What to highlight]")
            lines.append("")
    else:
        lines.append("## [Framework Name]")
        lines.append("")
        lines.append("[No frameworks automatically detected. Add manually.]")
        lines.append("")
        lines.append("### Components")
        lines.append("")
        lines.append("1. [Component 1]")
        lines.append("2. [Component 2]")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(
        description="Generate companion resources for podcast episodes",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "episode_dir", help="Path to episode directory containing report.md"
    )
    parser.add_argument(
        "--summary-only", action="store_true", help="Generate only the one-page summary"
    )
    parser.add_argument(
        "--dry-run", "-n", action="store_true", help="Preview without writing files"
    )
    parser.add_argument(
        "--output-dir",
        default="companion",
        help="Output subdirectory name (default: companion)",
    )

    args = parser.parse_args()

    # Resolve paths
    episode_dir = Path(args.episode_dir).resolve()

    if not episode_dir.exists():
        print(f"Error: Episode directory not found: {episode_dir}", file=sys.stderr)
        return 1

    # Find report.md
    report_path = episode_dir / "report.md"
    if not report_path.exists():
        print(f"Error: report.md not found in {episode_dir}", file=sys.stderr)
        return 1

    # Read content
    content = report_path.read_text()

    # Extract episode title from first heading
    title_match = re.search(r"^# (.+)$", content, re.MULTILINE)
    episode_title = title_match.group(1) if title_match else episode_dir.name

    print(f"Episode: {episode_title}")
    print(f"Source: {report_path}")

    # Extract content
    sections = extract_key_sections(content)
    takeaways = extract_takeaways(content)
    actions = extract_action_items(content)
    frameworks = extract_frameworks(content)
    stats = extract_statistics(content)

    print(
        f"Found: {len(sections)} sections, {len(takeaways)} takeaways, {len(actions)} actions, {len(frameworks)} frameworks"
    )

    # Generate resources
    summary = generate_summary(episode_title, sections, takeaways, stats, frameworks)

    if args.dry_run:
        print("\n=== ONE-PAGE SUMMARY ===\n")
        print(summary)
        if not args.summary_only:
            checklist = generate_checklist(episode_title, actions, takeaways)
            framework_doc = generate_framework_doc(episode_title, frameworks, content)
            print("\n=== ACTION CHECKLIST ===\n")
            print(checklist)
            print("\n=== FRAMEWORK DOC ===\n")
            print(framework_doc)
        print("\n=== DRY RUN: No files written ===")
        return 0

    # Create output directory
    output_dir = episode_dir / args.output_dir
    output_dir.mkdir(exist_ok=True)

    # Get slug from directory name
    slug = episode_dir.name

    # Write summary
    summary_path = output_dir / f"{slug}-summary.md"
    summary_path.write_text(summary)
    print(f"Created: {summary_path}")

    if not args.summary_only:
        # Write checklist
        checklist = generate_checklist(episode_title, actions, takeaways)
        checklist_path = output_dir / f"{slug}-checklist.md"
        checklist_path.write_text(checklist)
        print(f"Created: {checklist_path}")

        # Write framework doc
        framework_doc = generate_framework_doc(episode_title, frameworks, content)
        framework_path = output_dir / f"{slug}-frameworks.md"
        framework_path.write_text(framework_doc)
        print(f"Created: {framework_path}")

    print(f"\nCompanion resources created in: {output_dir}")
    print("Review and refine the generated content before publishing.")

    return 0


if __name__ == "__main__":
    sys.exit(main())
