# Tool Rebuild Requirements

**Created**: 2026-01-19
**Status**: ✅ Complete (2026-01-19)

This document captured requirements for rebuilding 12 tools from the old codebase.

All tools have been rebuilt and are available in `tools/`:
- search, image_analysis, code_execution, test_judge (High Priority)
- knowledge_search, link_analysis, doc_summary, documentation (Medium Priority)
- telegram_history, image_tagging, test_params, test_scheduler (Lower Priority)

See [tools/README.md](../tools/README.md) for usage documentation.

---

## 1. code-execution

**Purpose**: Sandboxed code execution environment with comprehensive safety measures.

**Capabilities**:
- Multi-language support (Python, JavaScript, SQL, Bash)
- Secure sandboxing with resource limits (memory, CPU, timeout)
- Dependency management and isolation
- Real-time execution monitoring
- Output capture and analysis
- Security scanning and validation

**Requirements**:
- `env`: None (runs locally)
- `python`: >=3.10

**Input Parameters**:
- `code`: Source code to execute (required)
- `language`: python|javascript|sql|bash (default: python)
- `timeout_seconds`: Execution timeout 1-300 (default: 30)
- `memory_limit_mb`: Memory limit 16-1024 (default: 128)
- `enable_network`: Allow network access (default: false)
- `dependencies`: List of required packages
- `input_data`: Input data to pass to code

**Output**:
- `stdout`: Standard output
- `stderr`: Standard error
- `exit_code`: Process exit code
- `execution_time_ms`: Execution duration
- `memory_used_mb`: Peak memory usage

---

## 2. image-analysis

**Purpose**: Multi-modal vision analysis using AI models.

**Capabilities**:
- Object detection and classification
- Scene understanding and context analysis
- Text extraction (OCR) with layout analysis
- Visual similarity comparison
- Content safety and moderation
- Accessibility analysis (alt-text generation)

**Requirements**:
- `env`: OPENROUTER_API_KEY or ANTHROPIC_API_KEY
- `python`: >=3.10

**Input Parameters**:
- `image_source`: File path, URL, or base64 encoded image (required)
- `analysis_types`: List of analysis types (default: ["description", "objects", "text"])
- `detail_level`: minimal|standard|detailed|comprehensive (default: standard)
- `output_format`: structured|narrative|technical|accessibility (default: structured)
- `max_image_size`: Max image dimension in pixels (default: 2048)

**Output**:
- `description`: Natural language description
- `objects`: List of detected objects with confidence
- `text`: Extracted text (if any)
- `tags`: Relevant tags/labels
- `safety_rating`: Content safety assessment

---

## 3. knowledge-search

**Purpose**: Local knowledge base search with semantic understanding.

**Capabilities**:
- Semantic similarity search with embeddings
- Multi-format document support (text, markdown, PDF, JSON)
- Intelligent chunking and indexing
- Query expansion and refinement
- Contextual result ranking

**Requirements**:
- `env`: OPENROUTER_API_KEY (for embeddings)
- `python`: >=3.10

**Input Parameters**:
- `query`: Search query (required)
- `search_type`: semantic|keyword|hybrid (default: semantic)
- `max_results`: Maximum results 1-100 (default: 10)
- `knowledge_bases`: List of paths to search (default: all)
- `file_types`: Filter by file type
- `similarity_threshold`: Minimum similarity score 0-1 (default: 0.7)

**Output**:
- `results`: List of matching documents with snippets
- `total_matches`: Total number of matches
- `search_time_ms`: Search duration

---

## 4. search (web)

**Purpose**: Web search using Perplexity API.

**Capabilities**:
- Multi-format search (conversational, factual, citations)
- Intelligent result ranking and filtering
- Content extraction and summarization
- Citation tracking and verification
- Adaptive search strategy optimization

**Requirements**:
- `env`: PERPLEXITY_API_KEY
- `python`: >=3.10

**Input Parameters**:
- `query`: Search query (required)
- `search_type`: conversational|factual|citations (default: conversational)
- `max_results`: Maximum results 1-50 (default: 10)
- `time_filter`: day|week|month|year (optional)
- `domain_filter`: List of domains to include/exclude
- `include_images`: Include image results (default: false)
- `language`: ISO 639-1 language code (default: en)

**Output**:
- `results`: List of search results with title, URL, snippet
- `total_results`: Total results found
- `result_summary`: AI-generated summary
- `suggested_refinements`: Query refinement suggestions
- `confidence_score`: Result quality confidence

---

## 5. test-judge

**Purpose**: AI-powered test evaluation and quality assessment.

**Capabilities**:
- Analyze test results comprehensively
- Identify patterns in failures
- Provide actionable insights for improvement
- Track quality metrics over time
- Generate recommendations

**Requirements**:
- `env`: ANTHROPIC_API_KEY or OPENROUTER_API_KEY
- `python`: >=3.10

**Input Parameters**:
- `test_results`: Test suite results (required)
- `quality_gates`: Quality gate criteria (optional)
- `previous_results`: Historical results for comparison (optional)
- `analysis_focus`: Areas to focus analysis on

**Output**:
- `overall_score`: Quality score 0-10
- `pass_rate`: Test pass percentage
- `summary`: Brief assessment
- `detailed_analysis`: In-depth analysis
- `recommendations`: Prioritized improvements
- `risk_factors`: Identified risks
- `quality_gates_status`: Pass/fail for each gate

---

## 6. doc-summary

**Purpose**: Document summarization with configurable detail levels.

**Capabilities**:
- Summarize documents of various formats
- Extract key points and themes
- Generate different summary lengths
- Preserve important context

**Requirements**:
- `env`: ANTHROPIC_API_KEY or OPENROUTER_API_KEY
- `python`: >=3.10

**Input Parameters**:
- `content`: Document content or file path (required)
- `summary_type`: brief|standard|detailed|bullets (default: standard)
- `max_length`: Maximum summary length in words (optional)
- `focus_areas`: Specific topics to emphasize (optional)
- `preserve_quotes`: Keep important quotes (default: false)

**Output**:
- `summary`: Generated summary
- `key_points`: List of main points
- `word_count`: Summary word count
- `compression_ratio`: Original vs summary size

---

## 7. documentation

**Purpose**: Generate documentation from code or descriptions.

**Capabilities**:
- Generate docstrings for functions/classes
- Create README files
- Generate API documentation
- Format in various styles (Google, NumPy, Sphinx)

**Requirements**:
- `env`: ANTHROPIC_API_KEY or OPENROUTER_API_KEY
- `python`: >=3.10

**Input Parameters**:
- `source`: Code or description to document (required)
- `doc_type`: docstring|readme|api|changelog (default: docstring)
- `style`: google|numpy|sphinx|markdown (default: google)
- `detail_level`: minimal|standard|comprehensive (default: standard)
- `include_examples`: Add usage examples (default: true)

**Output**:
- `documentation`: Generated documentation
- `format`: Output format used

---

## 8. image-tagging

**Purpose**: Tag and categorize images with AI.

**Capabilities**:
- Generate descriptive tags for images
- Categorize by content type
- Detect objects, scenes, activities
- Support custom taxonomies

**Requirements**:
- `env`: OPENROUTER_API_KEY
- `python`: >=3.10

**Input Parameters**:
- `image_source`: File path, URL, or base64 (required)
- `tag_categories`: Categories to include (default: all)
- `max_tags`: Maximum tags per category (default: 10)
- `confidence_threshold`: Minimum confidence 0-1 (default: 0.5)
- `custom_taxonomy`: Custom tag vocabulary (optional)

**Output**:
- `tags`: List of tags with confidence scores
- `categories`: Detected categories
- `dominant_colors`: Color palette
- `image_type`: Photo, illustration, screenshot, etc.

---

## 9. link-analysis

**Purpose**: URL analysis, extraction, and content summarization.

**Capabilities**:
- Extract URLs from text
- Validate URL formats
- Analyze URL content with AI
- Store and retrieve analyzed links

**Requirements**:
- `env`: PERPLEXITY_API_KEY (for content analysis)
- `python`: >=3.10

**Input Parameters**:
- `text`: Text containing URLs, or single URL (required)
- `analyze_content`: Fetch and analyze page content (default: true)
- `extract_metadata`: Get title, description, etc. (default: true)
- `validate_links`: Check if URLs are accessible (default: false)

**Output**:
- `urls`: List of extracted URLs
- `analysis`: Content analysis for each URL (if enabled)
- `metadata`: Page metadata (title, description, etc.)
- `validation`: Accessibility status for each URL

---

## 10. telegram-history

**Purpose**: Search Telegram conversation history.

**Capabilities**:
- Keyword search through message history
- Relevance + recency scoring algorithm
- Context summarization
- Configurable time windows

**Requirements**:
- `env`: None (uses local database)
- `python`: >=3.10

**Input Parameters**:
- `query`: Search query (required)
- `chat_id`: Telegram chat ID (required)
- `max_results`: Maximum results (default: 5)
- `max_age_days`: Time window in days (default: 30)

**Output**:
- `results`: Matching messages with relevance scores
- `total_matches`: Number of matches found
- `summary`: Context summary (optional)

---

## 11. test-params

**Purpose**: Generate test parameters for subjective AI testing.

**Capabilities**:
- Generate diverse parameter sets
- Support multiple test categories
- Configurable complexity levels
- Domain-specific context support

**Requirements**:
- `env`: None
- `python`: >=3.10

**Input Parameters**:
- `test_type`: Type of test (required)
- `param_categories`: Categories to generate (required)
- `num_variations`: Number of variations (default: 5)
- `complexity_level`: simple|medium|complex (default: medium)
- `domain_context`: Domain-specific context (optional)

**Output**:
- `test_params`: List of generated parameter sets
- `evaluation_criteria`: Criteria for each test
- `expected_behaviors`: Expected outcomes

---

## 12. test-scheduler

**Purpose**: Schedule test runs through background queue.

**Capabilities**:
- Parse test specifications
- Schedule tests with resource limits
- Background execution with notifications
- Support various test patterns

**Requirements**:
- `env`: None
- `python`: >=3.10

**Input Parameters**:
- `test_specification`: Description of tests to run (required)
- `notification_chat_id`: Where to send results (optional)
- `max_workers`: Parallel execution limit (default: 2)
- `timeout_minutes`: Maximum runtime (default: 10)

**Output**:
- `job_id`: Scheduled job identifier
- `status`: Scheduling status
- `tests_to_run`: List of tests that will run
- `estimated_duration`: Estimated run time

---

## Implementation Notes

### Standard Structure

Each tool should follow the structure in `tools/STANDARD.md`:

```
tools/<name>/
├── manifest.json         # Machine-readable spec
├── README.md             # Human documentation
├── __init__.py           # Tool implementation
└── tests/
    ├── __init__.py
    └── test_<name>.py    # Integration tests
```

### Priority Order

1. **High Priority** (core functionality):
   - search (web search)
   - image-analysis
   - code-execution
   - test-judge

2. **Medium Priority** (useful utilities):
   - knowledge-search
   - link-analysis
   - doc-summary
   - documentation

3. **Lower Priority** (specialized):
   - telegram-history
   - image-tagging
   - test-params
   - test-scheduler

### Dependencies

Shared `.env` location: `/Users/valorengels/src/.env`
- OPENROUTER_API_KEY
- PERPLEXITY_API_KEY
- ANTHROPIC_API_KEY
