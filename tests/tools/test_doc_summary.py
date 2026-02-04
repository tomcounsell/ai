"""Tests for the document summary tool."""

import os

from tools.doc_summary import extract_key_points, summarize, summarize_file


class TestSummarizeValidation:
    """Test input validation."""

    def test_empty_content_returns_error(self):
        """Test that empty content returns error."""
        result = summarize("")
        assert "error" in result
        assert "empty" in result["error"].lower()

    def test_whitespace_content_returns_error(self):
        """Test that whitespace-only content returns error."""
        result = summarize("   \n\t  ")
        assert "error" in result

    def test_missing_api_key_returns_error(self):
        """Test that missing API keys return error."""
        original_anthropic = os.environ.pop("ANTHROPIC_API_KEY", None)
        original_openrouter = os.environ.pop("OPENROUTER_API_KEY", None)
        try:
            result = summarize("Test content to summarize")
            assert "error" in result
            assert "API_KEY" in result["error"]
        finally:
            if original_anthropic:
                os.environ["ANTHROPIC_API_KEY"] = original_anthropic
            if original_openrouter:
                os.environ["OPENROUTER_API_KEY"] = original_openrouter


class TestSummarizeExecution:
    """Test actual summarization with real API."""

    def test_basic_summary(self, anthropic_api_key):
        """Test basic text summarization."""
        content = """
        Python is a high-level, general-purpose programming language.
        Its design philosophy emphasizes code readability with the use
        of significant indentation. Python is dynamically typed and
        garbage-collected. It supports multiple programming paradigms,
        including structured, object-oriented and functional programming.
        """
        result = summarize(content)

        assert "error" not in result
        assert result.get("summary")
        assert result.get("word_count", 0) > 0

    def test_brief_summary(self, anthropic_api_key):
        """Test brief summary type."""
        content = """
        Machine learning is a subset of artificial intelligence that enables
        systems to learn from data rather than through explicit programming.
        It involves algorithms that improve their performance as they are
        exposed to more data over time.
        """
        result = summarize(content, summary_type="brief")

        assert "error" not in result
        assert result.get("summary_type") == "brief"

    def test_detailed_summary(self, anthropic_api_key):
        """Test detailed summary type."""
        content = """
        Cloud computing is the delivery of computing services over the internet.
        These services include servers, storage, databases, networking, software,
        analytics, and intelligence. Cloud computing offers faster innovation,
        flexible resources, and economies of scale. Users only pay for cloud
        services they use, helping lower operating costs and run infrastructure
        more efficiently.
        """
        result = summarize(content, summary_type="detailed")

        assert "error" not in result
        assert result.get("summary_type") == "detailed"

    def test_bullets_summary(self, anthropic_api_key):
        """Test bullet point summary."""
        content = """
        Benefits of exercise include improved cardiovascular health,
        weight management, better mental health, increased energy levels,
        stronger muscles and bones, and improved sleep quality.
        """
        result = summarize(content, summary_type="bullets")

        assert "error" not in result
        assert result.get("summary_type") == "bullets"


class TestSummarizeFeatures:
    """Test summary features."""

    def test_summary_with_max_length(self, anthropic_api_key):
        """Test summary with max length constraint."""
        content = "A" * 1000 + " detailed technical document " + "B" * 1000
        result = summarize(content, max_length=50)

        assert "error" not in result
        # Word count should be roughly around the limit
        assert result.get("word_count", 0) < 200  # Reasonable upper bound

    def test_summary_with_focus_areas(self, anthropic_api_key):
        """Test summary with focus areas."""
        content = """
        Python supports multiple programming paradigms including procedural,
        object-oriented, and functional programming. It has a large standard
        library and active community. Python is used in web development,
        data science, machine learning, automation, and scripting.
        """
        result = summarize(content, focus_areas=["web development", "data science"])

        assert "error" not in result
        assert result.get("summary")

    def test_key_points_extraction(self, anthropic_api_key):
        """Test key points are extracted."""
        content = """
        The importance of documentation in software development cannot be
        overstated. Good documentation helps new team members onboard faster,
        reduces bugs by clarifying intended behavior, and serves as a reference
        for future maintenance. It should be kept up to date and written clearly.
        """
        result = summarize(content)

        assert "error" not in result
        assert "key_points" in result

    def test_compression_ratio_calculated(self, anthropic_api_key):
        """Test that compression ratio is calculated."""
        content = " ".join(["word"] * 200)  # 200 words
        result = summarize(content)

        assert "error" not in result
        assert "compression_ratio" in result
        assert result.get("original_word_count", 0) > 0


class TestSummarizeFile:
    """Test file summarization."""

    def test_summarize_markdown_file(self, temp_markdown_file, anthropic_api_key):
        """Test summarizing a markdown file."""
        result = summarize_file(str(temp_markdown_file))

        assert "error" not in result
        assert result.get("summary")

    def test_summarize_nonexistent_file(self):
        """Test summarizing a non-existent file."""
        result = summarize_file("/nonexistent/file.txt")
        # Either error in file reading or API key
        assert "error" in result


class TestExtractKeyPoints:
    """Test key point extraction."""

    def test_extract_key_points(self, anthropic_api_key):
        """Test extracting key points."""
        content = """
        Effective team communication is essential for project success.
        Regular meetings keep everyone aligned. Clear documentation
        prevents misunderstandings. Using the right tools improves efficiency.
        Feedback loops help identify issues early.
        """
        result = extract_key_points(content, max_points=3)

        assert "error" not in result
        assert "key_points" in result
        assert len(result["key_points"]) <= 3
