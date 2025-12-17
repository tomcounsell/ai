#!/usr/bin/env python3
"""Convert consolidated markdown documentation to PDF."""

import markdown
from weasyprint import HTML, CSS
from pathlib import Path

# Read markdown content
md_path = Path(__file__).parent / "CONSOLIDATED_DOCUMENTATION.md"
md_content = md_path.read_text()

# Convert markdown to HTML
html_content = markdown.markdown(
    md_content,
    extensions=['tables', 'fenced_code', 'codehilite', 'toc']
)

# Wrap in full HTML with styling
full_html = f"""
<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Valor AI System - Complete Documentation</title>
    <style>
        @page {{
            size: letter;
            margin: 1in;
            @top-center {{
                content: "Valor AI System Documentation";
                font-size: 9pt;
                color: #666;
            }}
            @bottom-center {{
                content: "Page " counter(page) " of " counter(pages);
                font-size: 9pt;
                color: #666;
            }}
        }}

        body {{
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
            font-size: 11pt;
            line-height: 1.6;
            color: #333;
            max-width: 100%;
        }}

        h1 {{
            font-size: 24pt;
            color: #1a1a2e;
            border-bottom: 3px solid #4361ee;
            padding-bottom: 10px;
            margin-top: 30px;
            page-break-after: avoid;
        }}

        h2 {{
            font-size: 18pt;
            color: #16213e;
            border-bottom: 1px solid #ddd;
            padding-bottom: 5px;
            margin-top: 25px;
            page-break-after: avoid;
        }}

        h3 {{
            font-size: 14pt;
            color: #1f4068;
            margin-top: 20px;
            page-break-after: avoid;
        }}

        h4 {{
            font-size: 12pt;
            color: #162447;
            margin-top: 15px;
            page-break-after: avoid;
        }}

        code {{
            font-family: "SF Mono", Monaco, "Courier New", monospace;
            font-size: 9pt;
            background-color: #f4f4f5;
            padding: 2px 6px;
            border-radius: 3px;
            color: #e74c3c;
        }}

        pre {{
            background-color: #1e1e1e;
            color: #d4d4d4;
            padding: 15px;
            border-radius: 5px;
            overflow-x: auto;
            font-size: 9pt;
            line-height: 1.4;
            page-break-inside: avoid;
        }}

        pre code {{
            background-color: transparent;
            padding: 0;
            color: inherit;
        }}

        table {{
            width: 100%;
            border-collapse: collapse;
            margin: 15px 0;
            font-size: 10pt;
            page-break-inside: avoid;
        }}

        th, td {{
            border: 1px solid #ddd;
            padding: 10px;
            text-align: left;
        }}

        th {{
            background-color: #4361ee;
            color: white;
            font-weight: bold;
        }}

        tr:nth-child(even) {{
            background-color: #f9f9f9;
        }}

        blockquote {{
            border-left: 4px solid #4361ee;
            margin: 15px 0;
            padding: 10px 20px;
            background-color: #f8f9fa;
            font-style: italic;
        }}

        hr {{
            border: none;
            border-top: 2px solid #4361ee;
            margin: 30px 0;
        }}

        ul, ol {{
            margin: 10px 0;
            padding-left: 25px;
        }}

        li {{
            margin: 5px 0;
        }}

        a {{
            color: #4361ee;
            text-decoration: none;
        }}

        /* First page styling */
        h1:first-of-type {{
            font-size: 36pt;
            text-align: center;
            border-bottom: none;
            margin-top: 100px;
        }}

        /* Avoid page breaks in the middle of sections */
        h1, h2, h3, h4 {{
            page-break-after: avoid;
        }}

        p, li {{
            orphans: 3;
            widows: 3;
        }}

        /* Table of contents styling */
        #table-of-contents + ol {{
            column-count: 2;
            column-gap: 30px;
        }}
    </style>
</head>
<body>
{html_content}
</body>
</html>
"""

# Convert to PDF
output_path = Path(__file__).parent / "CONSOLIDATED_DOCUMENTATION.pdf"
HTML(string=full_html).write_pdf(output_path)

print(f"PDF created: {output_path}")
print(f"Size: {output_path.stat().st_size / 1024:.1f} KB")
