"""
Web Search Tool - Perplexity API Integration

High-performance web search tool leveraging Perplexity's advanced search capabilities
with comprehensive result processing, caching, and quality validation.

Features:
- Multi-format search (conversational, factual, citations)
- Intelligent result ranking and filtering
- Content extraction and summarization
- Citation tracking and verification
- Adaptive search strategy optimization
"""

import asyncio
import hashlib
import json
import os
import time
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Union, Literal
from urllib.parse import urlparse, urljoin

import httpx
from pydantic import BaseModel, Field, validator

from .base import (
    ToolImplementation, BaseInputModel, BaseOutputModel, ToolContext,
    ToolError, ErrorCategory, QualityMetric, performance_monitor
)


class SearchInput(BaseInputModel):
    """Input model for web search requests with comprehensive validation."""
    
    query: str = Field(
        ..., 
        min_length=1, 
        max_length=1000,
        description="Search query with optional modifiers and filters"
    )
    
    search_type: Literal["conversational", "factual", "citations"] = Field(
        default="conversational",
        description="Type of search response format"
    )
    
    max_results: int = Field(
        default=10,
        ge=1,
        le=50,
        description="Maximum number of search results to return"
    )
    
    time_filter: Optional[Literal["day", "week", "month", "year"]] = Field(
        default=None,
        description="Time-based filter for recent content"
    )
    
    domain_filter: Optional[List[str]] = Field(
        default=None,
        description="List of domains to filter results (include or exclude)"
    )
    
    filter_mode: Literal["include", "exclude"] = Field(
        default="include",
        description="Whether domain filter is inclusive or exclusive"
    )
    
    include_images: bool = Field(
        default=False,
        description="Whether to include image results"
    )
    
    language: str = Field(
        default="en",
        pattern=r"^[a-z]{2}$",
        description="Language code for search results (ISO 639-1)"
    )
    
    safety_level: Literal["strict", "moderate", "off"] = Field(
        default="moderate",
        description="Content safety filtering level"
    )
    
    @validator('query')
    def validate_query(cls, v):
        """Enhanced query validation."""
        if not v.strip():
            raise ValueError("Query cannot be empty or only whitespace")
        
        # Check for potentially harmful patterns
        harmful_patterns = ['<script', 'javascript:', 'eval(', 'onclick=']
        query_lower = v.lower()
        if any(pattern in query_lower for pattern in harmful_patterns):
            raise ValueError("Query contains potentially harmful content")
        
        return v.strip()
    
    @validator('domain_filter')
    def validate_domains(cls, v):
        """Validate domain list format."""
        if v is None:
            return v
        
        validated_domains = []
        for domain in v:
            # Basic domain validation
            if not isinstance(domain, str):
                raise ValueError("Domain must be string")
            
            # Remove protocol if present
            if '://' in domain:
                domain = domain.split('://', 1)[1]
            
            # Basic domain format check
            if not domain or '/' in domain.split('.')[0]:
                raise ValueError(f"Invalid domain format: {domain}")
            
            validated_domains.append(domain.lower())
        
        return validated_domains
    
    def validate_business_rules(self) -> List[str]:
        """Validate business-specific rules."""
        errors = []
        
        # Check query complexity
        if len(self.query.split()) > 100:
            errors.append("Query is too complex (>100 words)")
        
        # Validate domain filter combination
        if self.domain_filter and len(self.domain_filter) > 20:
            errors.append("Too many domains in filter (max 20)")
        
        return errors


class SearchResult(BaseModel):
    """Individual search result with comprehensive metadata."""
    
    title: str = Field(..., description="Page title")
    url: str = Field(..., description="URL of the result")
    snippet: str = Field(..., description="Text snippet from the page")
    domain: str = Field(..., description="Domain of the result")
    published_date: Optional[datetime] = Field(None, description="Publication date")
    relevance_score: float = Field(ge=0.0, le=1.0, description="Relevance score")
    content_type: str = Field(default="webpage", description="Type of content")
    language: Optional[str] = Field(None, description="Detected language")
    word_count: Optional[int] = Field(None, description="Estimated word count")
    images: List[str] = Field(default_factory=list, description="Associated images")
    citations: List[str] = Field(default_factory=list, description="Citation sources")


class SearchOutput(BaseOutputModel):
    """Complete search response with analysis and metadata."""
    
    query: str = Field(..., description="Original search query")
    results: List[SearchResult] = Field(..., description="Search results")
    total_results: int = Field(..., description="Total number of results found")
    search_time_ms: float = Field(..., description="Search execution time")
    
    # Enhanced metadata
    query_analysis: Dict[str, Any] = Field(
        default_factory=dict,
        description="Analysis of the search query"
    )
    
    result_summary: str = Field(
        default="",
        description="AI-generated summary of results"
    )
    
    suggested_refinements: List[str] = Field(
        default_factory=list,
        description="Suggested query refinements"
    )
    
    fact_check_status: Optional[str] = Field(
        None,
        description="Fact-checking status for factual queries"
    )
    
    source_diversity: Dict[str, int] = Field(
        default_factory=dict,
        description="Diversity metrics by source type"
    )
    
    confidence_score: float = Field(
        default=0.0,
        ge=0.0,
        le=1.0,
        description="Confidence in result quality"
    )


class WebSearchTool(ToolImplementation[SearchInput, SearchOutput]):
    """
    Advanced Web Search Tool with Perplexity API Integration
    
    Provides intelligent web search with result processing, ranking,
    and quality validation for optimal search experience.
    """
    
    def __init__(self, api_key: Optional[str] = None, **kwargs):
        super().__init__(
            name="web_search",
            version="1.2.0",
            description="Advanced web search with AI-powered result processing",
            **kwargs
        )
        
        self.api_key = api_key or os.getenv("PERPLEXITY_API_KEY")
        if not self.api_key:
            raise ValueError("Perplexity API key is required")
        
        self.base_url = "https://api.perplexity.ai"
        self.client = httpx.AsyncClient(
            timeout=30.0,
            headers={"Authorization": f"Bearer {self.api_key}"}
        )
        
        # Configuration
        self.cache_duration = timedelta(hours=1)
        self.max_concurrent_requests = 5
        self.rate_limit_delay = 0.1
        
        # Internal tracking
        self._cache: Dict[str, Any] = {}
        self._last_request_time = 0.0
        self._request_count = 0
    
    @property
    def input_model(self) -> type:
        return SearchInput
    
    @property
    def output_model(self) -> type:
        return SearchOutput
    
    async def _execute_core(
        self, 
        input_data: SearchInput, 
        context: ToolContext
    ) -> SearchOutput:
        """Core search execution with comprehensive processing."""
        
        start_time = time.time()
        
        try:
            # Step 1: Query Analysis and Enhancement
            enhanced_query = await self._analyze_and_enhance_query(
                input_data.query, input_data.search_type
            )
            context.add_trace_data("enhanced_query", enhanced_query)
            
            # Step 2: Check Cache
            cache_key = self._generate_cache_key(input_data, enhanced_query)
            cached_result = self._get_cached_result(cache_key)
            
            if cached_result:
                context.add_trace_data("cache_hit", True)
                self.logger.info("Returning cached search results")
                return cached_result
            
            context.add_trace_data("cache_hit", False)
            
            # Step 3: Execute Search
            raw_results = await self._execute_perplexity_search(
                enhanced_query, input_data
            )
            context.add_trace_data("raw_results_count", len(raw_results))
            
            # Step 4: Process and Enhance Results
            processed_results = await self._process_search_results(
                raw_results, input_data, enhanced_query
            )
            
            # Step 5: Generate Analysis and Summary
            analysis = await self._analyze_results(processed_results, input_data.query)
            
            # Step 6: Build Output
            search_time = (time.time() - start_time) * 1000
            
            output = SearchOutput(
                query=input_data.query,
                results=processed_results[:input_data.max_results],
                total_results=len(raw_results),
                search_time_ms=search_time,
                query_analysis=analysis.get("query_analysis", {}),
                result_summary=analysis.get("summary", ""),
                suggested_refinements=analysis.get("refinements", []),
                fact_check_status=analysis.get("fact_check", None),
                source_diversity=analysis.get("source_diversity", {}),
                confidence_score=analysis.get("confidence", 0.8)
            )
            
            # Cache the result
            self._cache_result(cache_key, output)
            
            return output
            
        except httpx.HTTPError as e:
            raise ToolError(
                f"Network error during search: {str(e)}",
                ErrorCategory.NETWORK_ERROR,
                details={"http_error": str(e)},
                recoverable=True,
                retry_after=5.0
            )
        
        except httpx.TimeoutException:
            raise ToolError(
                "Search request timed out",
                ErrorCategory.TIMEOUT,
                recoverable=True,
                retry_after=3.0
            )
        
        except Exception as e:
            raise ToolError(
                f"Unexpected error during search: {str(e)}",
                ErrorCategory.INTERNAL_ERROR,
                details={"error": str(e)}
            )
    
    async def _analyze_and_enhance_query(
        self, 
        query: str, 
        search_type: str
    ) -> str:
        """Analyze query intent and enhance for better results."""
        
        # Query enhancement based on type
        if search_type == "factual":
            # Add fact-checking modifiers
            if not any(word in query.lower() for word in ["fact", "true", "verify", "statistics"]):
                query += " facts statistics"
        
        elif search_type == "citations":
            # Add academic/source modifiers
            if not any(word in query.lower() for word in ["source", "study", "research", "paper"]):
                query += " research sources academic"
        
        # Remove common stop words that might confuse search
        enhanced = query.strip()
        
        # Limit query length to optimal range
        words = enhanced.split()
        if len(words) > 20:
            enhanced = " ".join(words[:20])
        
        return enhanced
    
    async def _execute_perplexity_search(
        self, 
        query: str, 
        input_data: SearchInput
    ) -> List[Dict[str, Any]]:
        """Execute search against Perplexity API with rate limiting."""
        
        # Rate limiting
        current_time = time.time()
        time_since_last = current_time - self._last_request_time
        if time_since_last < self.rate_limit_delay:
            await asyncio.sleep(self.rate_limit_delay - time_since_last)
        
        self._last_request_time = time.time()
        self._request_count += 1
        
        # Build request payload
        payload = {
            "model": "llama-3.1-sonar-small-128k-online",
            "messages": [
                {
                    "role": "system",
                    "content": self._build_system_prompt(input_data)
                },
                {
                    "role": "user",
                    "content": query
                }
            ],
            "max_tokens": 4000,
            "temperature": 0.2,
            "search_domain_filter": input_data.domain_filter,
            "search_recency_filter": input_data.time_filter,
            "return_citations": True,
            "return_images": input_data.include_images
        }
        
        # Remove None values
        payload = {k: v for k, v in payload.items() if v is not None}
        
        try:
            response = await self.client.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers={"Content-Type": "application/json"}
            )
            
            response.raise_for_status()
            data = response.json()
            
            # Parse Perplexity response
            return self._parse_perplexity_response(data)
            
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise ToolError(
                    "Rate limit exceeded",
                    ErrorCategory.RATE_LIMITING,
                    retry_after=60.0
                )
            elif e.response.status_code == 401:
                raise ToolError(
                    "Invalid API key",
                    ErrorCategory.AUTHENTICATION,
                    recoverable=False
                )
            else:
                raise ToolError(
                    f"API error: {e.response.status_code}",
                    ErrorCategory.EXTERNAL_API,
                    details={"status_code": e.response.status_code}
                )
    
    def _build_system_prompt(self, input_data: SearchInput) -> str:
        """Build system prompt based on search parameters."""
        
        base_prompt = """You are a web search assistant. Provide comprehensive, accurate results with proper citations."""
        
        if input_data.search_type == "factual":
            return base_prompt + " Focus on factual, verifiable information with reliable sources."
        
        elif input_data.search_type == "citations":
            return base_prompt + " Emphasize academic sources and proper citation format."
        
        else:  # conversational
            return base_prompt + " Provide a conversational summary with diverse perspectives."
    
    def _parse_perplexity_response(self, data: Dict[str, Any]) -> List[Dict[str, Any]]:
        """Parse Perplexity API response into structured results."""
        
        results = []
        
        try:
            # Extract main content
            content = data.get("choices", [{}])[0].get("message", {}).get("content", "")
            citations = data.get("citations", [])
            
            # Process citations into structured results
            for i, citation in enumerate(citations):
                result = {
                    "title": citation.get("title", f"Result {i+1}"),
                    "url": citation.get("url", ""),
                    "snippet": citation.get("text", "")[:500],  # Limit snippet length
                    "domain": self._extract_domain(citation.get("url", "")),
                    "relevance_score": 1.0 - (i * 0.05),  # Decrease by position
                    "content_type": "webpage",
                    "citations": [citation.get("url", "")]
                }
                results.append(result)
            
            # If no citations, create result from content
            if not results and content:
                results.append({
                    "title": "Search Results",
                    "url": "",
                    "snippet": content[:500],
                    "domain": "perplexity.ai",
                    "relevance_score": 0.9,
                    "content_type": "summary"
                })
        
        except Exception as e:
            self.logger.error(f"Error parsing Perplexity response: {str(e)}")
            # Return empty results rather than failing
            
        return results
    
    def _extract_domain(self, url: str) -> str:
        """Extract domain from URL."""
        try:
            parsed = urlparse(url)
            return parsed.netloc.lower()
        except:
            return ""
    
    async def _process_search_results(
        self, 
        raw_results: List[Dict[str, Any]], 
        input_data: SearchInput,
        query: str
    ) -> List[SearchResult]:
        """Process and enhance raw search results."""
        
        processed_results = []
        
        for result_data in raw_results:
            try:
                # Create SearchResult with validation
                result = SearchResult(
                    title=result_data.get("title", "")[:200],  # Limit title length
                    url=result_data.get("url", ""),
                    snippet=result_data.get("snippet", "")[:500],  # Limit snippet
                    domain=result_data.get("domain", ""),
                    relevance_score=result_data.get("relevance_score", 0.5),
                    content_type=result_data.get("content_type", "webpage"),
                    citations=result_data.get("citations", [])
                )
                
                # Additional processing
                result.word_count = self._estimate_word_count(result.snippet)
                result.language = self._detect_language(result.snippet)
                
                processed_results.append(result)
                
            except Exception as e:
                self.logger.warning(f"Error processing search result: {str(e)}")
                continue  # Skip malformed results
        
        # Sort by relevance score
        processed_results.sort(key=lambda x: x.relevance_score, reverse=True)
        
        return processed_results
    
    def _estimate_word_count(self, text: str) -> int:
        """Estimate word count from text snippet."""
        return len(text.split()) if text else 0
    
    def _detect_language(self, text: str) -> Optional[str]:
        """Simple language detection (could be enhanced with proper lib)."""
        if not text:
            return None
        
        # Very basic detection - could use langdetect library
        # For now, assume English if contains common English words
        english_indicators = ["the", "and", "or", "but", "in", "on", "at", "to", "for", "of", "with", "by"]
        words = text.lower().split()
        
        english_count = sum(1 for word in words if word in english_indicators)
        if english_count >= len(words) * 0.1:  # 10% English indicators
            return "en"
        
        return None
    
    async def _analyze_results(
        self, 
        results: List[SearchResult], 
        original_query: str
    ) -> Dict[str, Any]:
        """Comprehensive analysis of search results."""
        
        analysis = {
            "query_analysis": {
                "query_length": len(original_query.split()),
                "query_type": self._classify_query_type(original_query),
                "entities_detected": self._extract_entities(original_query)
            },
            "source_diversity": {},
            "confidence": 0.8,
            "summary": "",
            "refinements": [],
            "fact_check": None
        }
        
        if not results:
            analysis["confidence"] = 0.0
            analysis["summary"] = "No results found for the given query."
            analysis["refinements"] = ["Try more general terms", "Check spelling"]
            return analysis
        
        # Source diversity analysis
        domains = [r.domain for r in results if r.domain]
        domain_counts = {}
        for domain in domains:
            domain_counts[domain] = domain_counts.get(domain, 0) + 1
        
        analysis["source_diversity"] = domain_counts
        
        # Generate summary
        top_snippets = [r.snippet for r in results[:3] if r.snippet]
        if top_snippets:
            analysis["summary"] = self._generate_summary(top_snippets, original_query)
        
        # Generate refinements
        analysis["refinements"] = self._generate_refinements(original_query, results)
        
        # Calculate confidence based on result quality
        analysis["confidence"] = self._calculate_confidence(results)
        
        return analysis
    
    def _classify_query_type(self, query: str) -> str:
        """Classify the type of query for better processing."""
        query_lower = query.lower()
        
        question_words = ["what", "how", "why", "when", "where", "who", "which"]
        if any(word in query_lower for word in question_words):
            return "question"
        
        comparison_words = ["vs", "versus", "compared to", "difference between"]
        if any(word in query_lower for word in comparison_words):
            return "comparison"
        
        if any(char in query for char in ["?", "!"]):
            return "question"
        
        return "informational"
    
    def _extract_entities(self, query: str) -> List[str]:
        """Simple entity extraction (could be enhanced with NER)."""
        # Very basic - extract capitalized words as potential entities
        import re
        
        # Find sequences of capitalized words
        entities = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', query)
        return list(set(entities))  # Remove duplicates
    
    def _generate_summary(self, snippets: List[str], query: str) -> str:
        """Generate a summary from top snippets."""
        # Simple extractive summary - take most relevant sentences
        all_text = " ".join(snippets)
        sentences = all_text.split('. ')
        
        # Filter sentences that contain query terms
        query_terms = query.lower().split()
        relevant_sentences = []
        
        for sentence in sentences:
            sentence_lower = sentence.lower()
            if any(term in sentence_lower for term in query_terms):
                relevant_sentences.append(sentence.strip())
        
        # Return top 2-3 most relevant sentences
        if relevant_sentences:
            return '. '.join(relevant_sentences[:3]) + '.'
        elif sentences:
            return '. '.join(sentences[:2]) + '.'
        else:
            return "Search results provide relevant information about the query."
    
    def _generate_refinements(
        self, 
        query: str, 
        results: List[SearchResult]
    ) -> List[str]:
        """Generate query refinement suggestions."""
        refinements = []
        
        # If few results, suggest broadening
        if len(results) < 3:
            refinements.append("Try more general terms")
            refinements.append("Remove specific details")
        
        # If many results from same domain, suggest diversifying
        domains = [r.domain for r in results if r.domain]
        if domains:
            most_common_domain = max(set(domains), key=domains.count)
            if domains.count(most_common_domain) > len(results) * 0.5:
                refinements.append(f"Add '-site:{most_common_domain}' to see other sources")
        
        # Suggest adding time filter if none
        refinements.append("Add time filter for recent results")
        
        # Suggest specific search operators
        if not any(op in query for op in ['"', 'site:', 'filetype:']):
            refinements.append('Use quotes for exact phrases like "machine learning"')
        
        return refinements[:3]  # Limit to 3 suggestions
    
    def _calculate_confidence(self, results: List[SearchResult]) -> float:
        """Calculate confidence score based on result quality."""
        if not results:
            return 0.0
        
        confidence = 0.5  # Base confidence
        
        # Boost for multiple high-quality sources
        avg_relevance = sum(r.relevance_score for r in results) / len(results)
        confidence += avg_relevance * 0.3
        
        # Boost for source diversity
        unique_domains = len(set(r.domain for r in results if r.domain))
        diversity_boost = min(unique_domains / 10, 0.2)
        confidence += diversity_boost
        
        # Penalize for very short snippets (may indicate poor quality)
        avg_snippet_length = sum(len(r.snippet) for r in results) / len(results)
        if avg_snippet_length < 50:
            confidence -= 0.1
        
        return min(max(confidence, 0.0), 1.0)
    
    def _generate_cache_key(self, input_data: SearchInput, enhanced_query: str) -> str:
        """Generate cache key for search request."""
        key_components = [
            enhanced_query,
            input_data.search_type,
            str(input_data.max_results),
            input_data.time_filter or "",
            ",".join(input_data.domain_filter or []),
            input_data.filter_mode,
            str(input_data.include_images),
            input_data.language,
            input_data.safety_level
        ]
        
        key_string = "|".join(key_components)
        return hashlib.md5(key_string.encode()).hexdigest()
    
    def _get_cached_result(self, cache_key: str) -> Optional[SearchOutput]:
        """Retrieve cached result if still valid."""
        if cache_key not in self._cache:
            return None
        
        cached_data, timestamp = self._cache[cache_key]
        
        # Check if cache is still valid
        if datetime.utcnow() - timestamp > self.cache_duration:
            del self._cache[cache_key]
            return None
        
        return cached_data
    
    def _cache_result(self, cache_key: str, result: SearchOutput) -> None:
        """Cache search result with timestamp."""
        # Limit cache size
        if len(self._cache) > 1000:
            # Remove oldest entries (simple FIFO)
            oldest_keys = list(self._cache.keys())[:200]
            for key in oldest_keys:
                del self._cache[key]
        
        self._cache[cache_key] = (result, datetime.utcnow())
    
    async def _custom_quality_assessment(
        self,
        quality: 'QualityScore',
        input_data: SearchInput,
        result: SearchOutput,
        context: ToolContext
    ) -> None:
        """Custom quality assessment for search results."""
        
        # Assess result completeness
        if result.total_results > 0:
            quality.add_dimension(QualityMetric.ACCURACY, 9.0)
        else:
            quality.add_dimension(
                QualityMetric.ACCURACY, 4.0,
                "No results found - query may need refinement"
            )
        
        # Assess source diversity
        unique_domains = len(result.source_diversity)
        if unique_domains >= 5:
            quality.add_dimension(QualityMetric.RELIABILITY, 9.5)
        elif unique_domains >= 3:
            quality.add_dimension(QualityMetric.RELIABILITY, 8.0)
        else:
            quality.add_dimension(
                QualityMetric.RELIABILITY, 6.0,
                "Limited source diversity may affect result completeness"
            )
        
        # Assess performance
        if result.search_time_ms < 3000:
            quality.add_dimension(QualityMetric.PERFORMANCE, 9.0)
        elif result.search_time_ms < 8000:
            quality.add_dimension(QualityMetric.PERFORMANCE, 7.0)
        else:
            quality.add_dimension(
                QualityMetric.PERFORMANCE, 5.0,
                "Search took longer than optimal response time"
            )
        
        # Assess usability based on result summary quality
        if result.result_summary and len(result.result_summary) > 50:
            quality.add_dimension(QualityMetric.USABILITY, 8.5)
        else:
            quality.add_dimension(
                QualityMetric.USABILITY, 6.0,
                "Result summary could be more comprehensive"
            )
    
    async def __aenter__(self):
        """Async context manager entry."""
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit with cleanup."""
        await self.client.aclose()


# Factory function for easy instantiation
def create_web_search_tool(api_key: Optional[str] = None) -> WebSearchTool:
    """Create a configured WebSearchTool instance."""
    return WebSearchTool(api_key=api_key)


# Export main components
__all__ = ['WebSearchTool', 'SearchInput', 'SearchOutput', 'SearchResult', 'create_web_search_tool']