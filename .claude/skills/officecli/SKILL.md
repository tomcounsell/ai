---
name: officecli
description: "Use when creating, reading, editing, or inspecting Office documents (.docx, .xlsx, .pptx). Triggered by requests involving Word, Excel, or PowerPoint files."
allowed-tools: Read, Write, Edit, Bash
user-invocable: false
---

# OfficeCLI - Office Document Operations

AI-friendly CLI for .docx, .xlsx, .pptx. Single binary at ~/.local/bin/officecli, no dependencies, no Office installation needed.

## Strategy

**L1 (read) -> L2 (DOM edit) -> L3 (raw XML)**. Always prefer higher layers. Add --json for structured output.

## Help System

When unsure about property names, value formats, or command syntax, ALWAYS run help instead of guessing.

Three-layer navigation:
```bash
officecli pptx set              # All settable elements and their properties
officecli pptx set shape        # Shape properties in detail
officecli pptx set shape.fill   # Specific property format and examples
```

Replace pptx with docx or xlsx. Commands: view, get, query, set, add, raw.

## Core Commands

### Create Files
```bash
officecli create report.docx
officecli create data.xlsx
officecli create slides.pptx
```

### Read and Inspect (L1)
```bash
officecli view <file> outline        # Document structure
officecli view <file> stats          # Statistics (pages, words, shapes)
officecli view <file> issues         # Formatting/content/structure problems
officecli view <file> text           # Plain text extraction
officecli get <file> <path> --json   # Structured node data
officecli get <file> <path> --depth N  # Expand children N levels
officecli query <file> <selector>    # CSS-like query
officecli validate <file>            # OpenXML schema validation
```

For large documents, ALWAYS use --max-lines N or --start N --end N to limit output.

### Modify (L2 DOM)
```bash
officecli set <file> <path> --prop key=value [--prop ...]
officecli add <file> <parent> --type <type> [--prop ...]
officecli add <file> <parent> --from <path>    # Clone existing element
officecli remove <file> <path>
officecli move <file> <path> [--to <parent>] [--index N]
officecli swap <file> <path1> <path2>
```

### Batch Operations
```bash
echo '[{"command":"set","path":"/Sheet1/A1","props":{"value":"Name","bold":"true"}}]' | officecli batch data.xlsx --json
```

### Resident Mode (3+ commands on same file)
```bash
officecli open report.docx    # Keep in memory for fast access
officecli set report.docx ... # No file I/O overhead
officecli close report.docx   # Save and release
```

## Word (.docx) Examples
```bash
officecli add report.docx /body --type paragraph --prop text="Executive Summary" --prop style=Heading1
officecli set report.docx '/body/p[1]/r[1]' --prop font=Arial --prop size=12pt --prop bold=true
officecli add report.docx /body --type table --prop rows=3 --prop cols=4
```

Element types: paragraph (para), run, table, row (tr), cell (td), image (picture/img), header, footer, section, bookmark, comment, footnote, endnote, chart, equation, hyperlink, style, toc, watermark, break (pagebreak)

## Excel (.xlsx) Examples
```bash
officecli set data.xlsx /Sheet1/A1 --prop value="Name" --prop bold=true
officecli set data.xlsx /Sheet1/B2 --prop value=95
officecli add data.xlsx / --type sheet --prop name="Summary"
officecli add data.xlsx /Sheet1 --type chart --prop type=bar --prop range=A1:D10
```

Element types: sheet, row, cell, chart, image (picture), comment, table (listobject), namedrange, pivottable, sparkline, validation, autofilter, shape, textbox

## PowerPoint (.pptx) Examples
```bash
officecli add slides.pptx / --type slide --prop title="Q4 Report" --prop background=1A1A2E
officecli add slides.pptx /slide[1] --type shape --prop text="Revenue grew 25%" --prop x=2cm --prop y=5cm --prop font=Arial --prop size=24 --prop color=FFFFFF
officecli set slides.pptx /slide[1] --prop transition=fade --prop advanceTime=3000
```

Element types: slide, shape (textbox), picture (image/img), chart, table, row (tr), connector (line), group, video (audio/media), equation (formula/math), notes, paragraph (para), run

## Value Formats

| Type | Format | Examples |
|------|--------|---------|
| Colors | Hex, named, RGB, theme | FF0000, red, rgb(255,0,0), accent1..accent6 |
| Spacing | Unit-qualified | 12pt, 0.5cm, 1.5x, 150% |
| Dimensions | EMU or suffixed | 914400, 2.54cm, 1in, 72pt, 96px |

## Query Selectors

CSS-like selectors for finding elements:
```bash
officecli query report.docx 'paragraph[style=Normal] > run[font!=Arial]'
officecli query slides.pptx 'shape[fill=FF0000]'
officecli query data.xlsx 'cell:has(formula)'
```

Supports: [attr=value], [attr!=value], [attr~=text], [attr>=value], [attr<=value], :contains("text"), :empty, :has(formula), :no-alt
