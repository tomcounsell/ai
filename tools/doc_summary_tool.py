"""Documentation reading and summarization tool for large documents."""

import re
import os
from pathlib import Path
from typing import List, Dict, Optional, Any, Union
from pydantic import BaseModel, Field
from urllib.parse import urlparse
import subprocess
import tempfile


class DocumentSection(BaseModel):
    """Individual document section."""
    title: str
    content: str
    level: int = Field(description="Heading level (1-6)")
    word_count: int
    key_points: List[str] = Field(default_factory=list)


class DocumentSummary(BaseModel):
    """Complete document summary."""
    title: str
    total_words: int
    total_sections: int
    summary: str
    key_insights: List[str]
    sections: List[DocumentSection]
    reading_time_minutes: int
    document_type: str
    main_topics: List[str]


class SummaryConfig(BaseModel):
    """Configuration for document summarization."""
    max_section_words: int = Field(default=500, description="Max words per section summary")
    include_code_blocks: bool = Field(default=True, description="Include code examples in summary")
    extract_key_points: bool = Field(default=True, description="Extract bullet points from each section")
    focus_topics: Optional[List[str]] = Field(default=None, description="Specific topics to focus on")
    summary_style: str = Field(default="comprehensive", description="Style: 'brief', 'comprehensive', 'technical'")


def summarize_document(
    document_path: str,
    config: Optional[SummaryConfig] = None
) -> DocumentSummary:
    """
    Read and summarize large documents (markdown, text, code files).
    
    Automatically detects document type and creates structured summaries
    with section-by-section analysis, key insights, and main topics.
    """
    if config is None:
        config = SummaryConfig()
    
    # Read document content
    content = _read_document(document_path)
    
    # Parse document structure
    sections = _parse_document_structure(content, document_path)
    
    # Generate summaries for each section
    summarized_sections = []
    for section in sections:
        summarized_section = _summarize_section(section, config)
        summarized_sections.append(summarized_section)
    
    # Generate overall document summary
    document_title = _extract_document_title(content, document_path)
    total_words = sum(section.word_count for section in summarized_sections)
    
    overall_summary = _generate_overall_summary(summarized_sections, config)
    key_insights = _extract_key_insights(summarized_sections, config)
    main_topics = _extract_main_topics(summarized_sections, config)
    
    reading_time = max(1, total_words // 200)  # Assume 200 WPM reading speed
    doc_type = _detect_document_type(document_path, content)
    
    return DocumentSummary(
        title=document_title,
        total_words=total_words,
        total_sections=len(summarized_sections),
        summary=overall_summary,
        key_insights=key_insights,
        sections=summarized_sections,
        reading_time_minutes=reading_time,
        document_type=doc_type,
        main_topics=main_topics
    )


def summarize_url_document(url: str, config: Optional[SummaryConfig] = None) -> DocumentSummary:
    """Summarize document from URL (supports GitHub, GitLab, docs sites)."""
    # Download content using curl or similar
    try:
        with tempfile.NamedTemporaryFile(mode='w+', suffix='.md', delete=False) as f:
            temp_path = f.name
        
        # Use curl to download the content
        result = subprocess.run(
            ["curl", "-s", "-L", url],
            capture_output=True,
            text=True,
            timeout=30
        )
        
        if result.returncode == 0:
            with open(temp_path, 'w') as f:
                f.write(result.stdout)
            
            summary = summarize_document(temp_path, config)
            summary.title = f"{summary.title} (from {urlparse(url).netloc})"
            return summary
        else:
            raise Exception(f"Failed to download: {result.stderr}")
            
    except Exception as e:
        # Return error summary
        return DocumentSummary(
            title=f"Error: {url}",
            total_words=0,
            total_sections=0,
            summary=f"Failed to process URL: {str(e)}",
            key_insights=[f"Error accessing {url}"],
            sections=[],
            reading_time_minutes=0,
            document_type="error",
            main_topics=[]
        )
    finally:
        if 'temp_path' in locals():
            try:
                os.unlink(temp_path)
            except:
                pass


def batch_summarize_docs(
    doc_paths: List[str],
    config: Optional[SummaryConfig] = None
) -> Dict[str, DocumentSummary]:
    """Summarize multiple documents in batch."""
    summaries = {}
    
    for doc_path in doc_paths:
        try:
            summary = summarize_document(doc_path, config)
            summaries[doc_path] = summary
        except Exception as e:
            # Create error summary for failed documents
            summaries[doc_path] = DocumentSummary(
                title=f"Error: {Path(doc_path).name}",
                total_words=0,
                total_sections=0,
                summary=f"Failed to process: {str(e)}",
                key_insights=[],
                sections=[],
                reading_time_minutes=0,
                document_type="error",
                main_topics=[]
            )
    
    return summaries


def _read_document(document_path: str) -> str:
    """Read document content from file."""
    path = Path(document_path)
    
    if not path.exists():
        raise FileNotFoundError(f"Document not found: {document_path}")
    
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return f.read()
    except UnicodeDecodeError:
        # Try with different encodings
        for encoding in ['latin-1', 'cp1252', 'ascii']:
            try:
                with open(path, 'r', encoding=encoding) as f:
                    return f.read()
            except UnicodeDecodeError:
                continue
        
        raise Exception(f"Could not decode file with any supported encoding: {document_path}")


def _parse_document_structure(content: str, document_path: str) -> List[DocumentSection]:
    """Parse document into sections based on headings."""
    path = Path(document_path)
    file_extension = path.suffix.lower()
    
    if file_extension in ['.md', '.markdown']:
        return _parse_markdown_sections(content)
    elif file_extension in ['.rst']:
        return _parse_rst_sections(content)
    elif file_extension in ['.py', '.js', '.ts', '.java', '.cpp', '.c', '.go', '.rs']:
        return _parse_code_sections(content, file_extension)
    else:
        return _parse_text_sections(content)


def _parse_markdown_sections(content: str) -> List[DocumentSection]:
    """Parse markdown document into sections."""
    sections = []
    lines = content.split('\n')
    current_section = None
    current_content = []
    
    for line in lines:
        # Check for markdown headings
        heading_match = re.match(r'^(#{1,6})\s+(.+)$', line)
        
        if heading_match:
            # Save previous section if exists
            if current_section:
                section_content = '\n'.join(current_content).strip()
                if section_content:
                    sections.append(DocumentSection(
                        title=current_section['title'],
                        content=section_content,
                        level=current_section['level'],
                        word_count=len(section_content.split()),
                        key_points=_extract_bullet_points(section_content)
                    ))
            
            # Start new section
            level = len(heading_match.group(1))
            title = heading_match.group(2).strip()
            current_section = {'title': title, 'level': level}
            current_content = []
        else:
            if current_section:
                current_content.append(line)
    
    # Add final section
    if current_section and current_content:
        section_content = '\n'.join(current_content).strip()
        if section_content:
            sections.append(DocumentSection(
                title=current_section['title'],
                content=section_content,
                level=current_section['level'],
                word_count=len(section_content.split()),
                key_points=_extract_bullet_points(section_content)
            ))
    
    return sections


def _parse_rst_sections(content: str) -> List[DocumentSection]:
    """Parse reStructuredText document into sections."""
    # Basic RST parsing - looks for underlined titles
    sections = []
    lines = content.split('\n')
    
    for i, line in enumerate(lines):
        if i < len(lines) - 1:
            next_line = lines[i + 1]
            # Check for RST heading patterns (title followed by underline)
            if next_line and len(set(next_line)) == 1 and next_line[0] in '=-~^"\'`':
                # This is a heading
                title = line.strip()
                level = {'=': 1, '-': 2, '~': 3, '^': 4, '"': 5, "'": 6, '`': 7}.get(next_line[0], 1)
                
                # Find content until next heading
                content_lines = []
                for j in range(i + 2, len(lines)):
                    if j < len(lines) - 1 and lines[j + 1] and len(set(lines[j + 1])) == 1:
                        break
                    content_lines.append(lines[j])
                
                section_content = '\n'.join(content_lines).strip()
                if section_content:
                    sections.append(DocumentSection(
                        title=title,
                        content=section_content,
                        level=level,
                        word_count=len(section_content.split()),
                        key_points=_extract_bullet_points(section_content)
                    ))
    
    return sections


def _parse_code_sections(content: str, file_extension: str) -> List[DocumentSection]:
    """Parse code files into logical sections (classes, functions, etc.)."""
    sections = []
    
    if file_extension == '.py':
        sections.extend(_parse_python_sections(content))
    else:
        # Generic code parsing - split by major constructs
        sections.extend(_parse_generic_code_sections(content))
    
    return sections


def _parse_python_sections(content: str) -> List[DocumentSection]:
    """Parse Python code into classes and functions."""
    sections = []
    lines = content.split('\n')
    current_section = None
    current_content = []
    
    for line in lines:
        # Look for class or function definitions
        class_match = re.match(r'^class\s+(\w+)', line)
        func_match = re.match(r'^def\s+(\w+)', line)
        
        if class_match or func_match:
            # Save previous section
            if current_section and current_content:
                section_content = '\n'.join(current_content).strip()
                sections.append(DocumentSection(
                    title=current_section,
                    content=section_content,
                    level=1 if class_match else 2,
                    word_count=len(section_content.split()),
                    key_points=_extract_code_comments(section_content)
                ))
            
            # Start new section
            current_section = class_match.group(1) if class_match else func_match.group(1)
            current_content = [line]
        else:
            if current_section:
                current_content.append(line)
    
    # Add final section
    if current_section and current_content:
        section_content = '\n'.join(current_content).strip()
        sections.append(DocumentSection(
            title=current_section,
            content=section_content,
            level=1,
            word_count=len(section_content.split()),
            key_points=_extract_code_comments(section_content)
        ))
    
    return sections


def _parse_generic_code_sections(content: str) -> List[DocumentSection]:
    """Generic code parsing for non-Python files."""
    # Simple approach: split by significant comment blocks
    sections = []
    lines = content.split('\n')
    current_section = "Main Code"
    current_content = []
    
    for line in lines:
        # Look for comment headers (lines with multiple comment chars)
        if re.match(r'^\s*[#/\*]{3,}.*[#/\*]{3,}', line):
            # This looks like a section header comment
            if current_content:
                section_content = '\n'.join(current_content).strip()
                sections.append(DocumentSection(
                    title=current_section,
                    content=section_content,
                    level=1,
                    word_count=len(section_content.split()),
                    key_points=[]
                ))
            
            # Extract title from comment
            title = re.sub(r'[#/\*\s]', '', line).strip() or "Code Section"
            current_section = title
            current_content = []
        else:
            current_content.append(line)
    
    # Add final section
    if current_content:
        section_content = '\n'.join(current_content).strip()
        sections.append(DocumentSection(
            title=current_section,
            content=section_content,
            level=1,
            word_count=len(section_content.split()),
            key_points=[]
        ))
    
    return sections


def _parse_text_sections(content: str) -> List[DocumentSection]:
    """Parse plain text into paragraphs or logical sections."""
    # Split by double newlines (paragraphs)
    paragraphs = re.split(r'\n\s*\n', content)
    sections = []
    
    for i, paragraph in enumerate(paragraphs):
        paragraph = paragraph.strip()
        if paragraph:
            # Use first line as title, or generic title
            lines = paragraph.split('\n')
            title = lines[0][:50] + "..." if len(lines[0]) > 50 else lines[0]
            if not title:
                title = f"Section {i+1}"
            
            sections.append(DocumentSection(
                title=title,
                content=paragraph,
                level=1,
                word_count=len(paragraph.split()),
                key_points=_extract_bullet_points(paragraph)
            ))
    
    return sections


def _summarize_section(section: DocumentSection, config: SummaryConfig) -> DocumentSection:
    """Create concise summary of a document section."""
    if section.word_count <= config.max_section_words:
        return section
    
    # Simple summarization - take first and last sentences plus key points
    sentences = re.split(r'[.!?]+', section.content)
    sentences = [s.strip() for s in sentences if s.strip()]
    
    if len(sentences) <= 3:
        return section
    
    # Take first 2 and last sentence, plus key points
    summary_sentences = sentences[:2] + sentences[-1:]
    summary_content = '. '.join(summary_sentences) + '.'
    
    # Add key points if they exist
    if section.key_points:
        summary_content += '\n\nKey points:\n' + '\n'.join(f"• {point}" for point in section.key_points[:3])
    
    return DocumentSection(
        title=section.title,
        content=summary_content,
        level=section.level,
        word_count=len(summary_content.split()),
        key_points=section.key_points
    )


def _extract_bullet_points(content: str) -> List[str]:
    """Extract bullet points and numbered lists from content."""
    points = []
    lines = content.split('\n')
    
    for line in lines:
        # Look for bullet points or numbered lists
        bullet_match = re.match(r'^\s*[•\-\*]\s+(.+)', line)
        number_match = re.match(r'^\s*\d+\.\s+(.+)', line)
        
        if bullet_match:
            points.append(bullet_match.group(1).strip())
        elif number_match:
            points.append(number_match.group(1).strip())
    
    return points


def _extract_code_comments(content: str) -> List[str]:
    """Extract meaningful comments from code."""
    comments = []
    lines = content.split('\n')
    
    for line in lines:
        # Look for substantial comments (not just # or //)
        comment_match = re.match(r'^\s*[#/\*]+\s*(.{10,})', line)
        if comment_match:
            comment_text = comment_match.group(1).strip()
            if comment_text and not comment_text.startswith(('TODO', 'FIXME', 'XXX')):
                comments.append(comment_text)
    
    return comments


def _extract_document_title(content: str, document_path: str) -> str:
    """Extract document title from content or filename."""
    # Try to find first heading
    lines = content.split('\n')
    for line in lines[:10]:  # Check first 10 lines
        heading_match = re.match(r'^#{1,2}\s+(.+)$', line)
        if heading_match:
            return heading_match.group(1).strip()
    
    # Use filename as fallback
    return Path(document_path).stem.replace('_', ' ').replace('-', ' ').title()


def _generate_overall_summary(sections: List[DocumentSection], config: SummaryConfig) -> str:
    """Generate overall document summary."""
    if not sections:
        return "Empty document"
    
    if config.summary_style == "brief":
        return f"Document with {len(sections)} sections covering: {', '.join(s.title for s in sections[:3])}{'...' if len(sections) > 3 else ''}"
    
    # Extract key themes and create comprehensive summary
    main_themes = [section.title for section in sections if section.level <= 2]
    
    summary = f"This document contains {len(sections)} sections. "
    
    if main_themes:
        summary += f"Main topics include: {', '.join(main_themes[:5])}. "
    
    # Add insight about document structure
    if any(s.key_points for s in sections):
        total_points = sum(len(s.key_points) for s in sections)
        summary += f"Contains {total_points} key points and actionable insights. "
    
    return summary


def _extract_key_insights(sections: List[DocumentSection], config: SummaryConfig) -> List[str]:
    """Extract key insights from all sections."""
    insights = []
    
    for section in sections:
        # Add section key points
        if section.key_points:
            insights.extend(section.key_points[:2])  # Top 2 from each section
        
        # Look for insights in content
        if "important" in section.content.lower() or "note" in section.content.lower():
            # Extract sentences with important keywords
            sentences = re.split(r'[.!?]+', section.content)
            for sentence in sentences:
                if any(keyword in sentence.lower() for keyword in ["important", "note", "remember", "key"]):
                    insights.append(sentence.strip())
    
    return insights[:8]  # Return top 8 insights


def _extract_main_topics(sections: List[DocumentSection], config: SummaryConfig) -> List[str]:
    """Extract main topics from section titles and content."""
    topics = set()
    
    # Add section titles as topics
    for section in sections:
        if section.level <= 2:  # Only main sections
            topics.add(section.title.lower())
    
    # Extract topics from focus areas if specified
    if config.focus_topics:
        for topic in config.focus_topics:
            topics.add(topic.lower())
    
    return list(topics)[:10]  # Return top 10 topics


def _detect_document_type(document_path: str, content: str) -> str:
    """Detect document type from path and content."""
    path = Path(document_path)
    extension = path.suffix.lower()
    
    type_mapping = {
        '.md': 'markdown',
        '.markdown': 'markdown',
        '.rst': 'restructuredtext',
        '.txt': 'text',
        '.py': 'python_code',
        '.js': 'javascript_code',
        '.ts': 'typescript_code',
        '.java': 'java_code',
        '.cpp': 'cpp_code',
        '.c': 'c_code',
        '.go': 'go_code',
        '.rs': 'rust_code'
    }
    
    return type_mapping.get(extension, 'unknown')


# Convenience functions for common use cases
def quick_doc_summary(file_path: str) -> str:
    """Quick text summary of a document."""
    summary = summarize_document(file_path, SummaryConfig(summary_style="brief"))
    return f"{summary.title}: {summary.summary}"


def technical_doc_analysis(file_path: str) -> DocumentSummary:
    """Detailed technical analysis of documentation."""
    config = SummaryConfig(
        summary_style="technical",
        extract_key_points=True,
        include_code_blocks=True,
        max_section_words=300
    )
    
    return summarize_document(file_path, config)