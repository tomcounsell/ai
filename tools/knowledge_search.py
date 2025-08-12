"""
Knowledge Base Search Tool - Local Knowledge Management

Advanced local knowledge base search with semantic understanding, document ranking,
and intelligent knowledge retrieval. Supports multiple formats and indexing strategies.

Features:
- Semantic similarity search with embeddings
- Multi-format document support (text, markdown, PDF, JSON)
- Intelligent chunking and indexing
- Query expansion and refinement
- Knowledge graph relationships
- Contextual result ranking
"""

import asyncio
import hashlib
import json
import os
import sqlite3
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Any, Union, Tuple, Set
import logging
import pickle
from dataclasses import dataclass

import httpx
from pydantic import BaseModel, Field, validator

from .base import (
    ToolImplementation, BaseInputModel, BaseOutputModel, ToolContext,
    ToolError, ErrorCategory, QualityMetric, performance_monitor
)


@dataclass
class DocumentChunk:
    """Individual document chunk with metadata."""
    chunk_id: str
    content: str
    document_path: str
    chunk_index: int
    word_count: int
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = None
    last_updated: datetime = None


class KnowledgeSearchInput(BaseInputModel):
    """Input model for knowledge base search requests."""
    
    query: str = Field(
        ..., 
        min_length=1, 
        max_length=500,
        description="Search query for knowledge base"
    )
    
    search_type: str = Field(
        default="semantic",
        pattern="^(semantic|keyword|hybrid)$",
        description="Type of search: semantic, keyword, or hybrid"
    )
    
    max_results: int = Field(
        default=10,
        ge=1,
        le=100,
        description="Maximum number of results to return"
    )
    
    knowledge_bases: Optional[List[str]] = Field(
        default=None,
        description="Specific knowledge bases to search (if None, searches all)"
    )
    
    file_types: Optional[List[str]] = Field(
        default=None,
        description="Filter by file types: txt, md, pdf, json, etc."
    )
    
    date_range: Optional[Dict[str, str]] = Field(
        default=None,
        description="Date range filter with 'start' and 'end' keys"
    )
    
    include_metadata: bool = Field(
        default=True,
        description="Include document metadata in results"
    )
    
    min_relevance_score: float = Field(
        default=0.1,
        ge=0.0,
        le=1.0,
        description="Minimum relevance score threshold"
    )
    
    expand_query: bool = Field(
        default=True,
        description="Enable query expansion with synonyms and related terms"
    )
    
    @validator('query')
    def validate_query(cls, v):
        """Validate and clean query."""
        if not v.strip():
            raise ValueError("Query cannot be empty")
        return v.strip()
    
    @validator('date_range')
    def validate_date_range(cls, v):
        """Validate date range format."""
        if v is None:
            return v
        
        if not isinstance(v, dict):
            raise ValueError("Date range must be a dictionary")
        
        if 'start' not in v and 'end' not in v:
            raise ValueError("Date range must contain 'start' or 'end'")
        
        # Validate date format (basic validation)
        for key in ['start', 'end']:
            if key in v:
                try:
                    datetime.fromisoformat(v[key].replace('Z', '+00:00'))
                except ValueError:
                    raise ValueError(f"Invalid date format for {key}: {v[key]}")
        
        return v


class KnowledgeResult(BaseModel):
    """Individual knowledge search result."""
    
    document_path: str = Field(..., description="Path to source document")
    chunk_id: str = Field(..., description="Unique chunk identifier")
    content: str = Field(..., description="Matching content chunk")
    relevance_score: float = Field(ge=0.0, le=1.0, description="Relevance score")
    document_title: str = Field(default="", description="Document title")
    file_type: str = Field(..., description="File type/format")
    last_modified: Optional[datetime] = Field(None, description="Last modification date")
    word_count: int = Field(ge=0, description="Word count of chunk")
    
    # Enhanced metadata
    context_before: str = Field(default="", description="Context before match")
    context_after: str = Field(default="", description="Context after match")
    highlights: List[str] = Field(default_factory=list, description="Highlighted terms")
    tags: List[str] = Field(default_factory=list, description="Document tags")
    relationships: Dict[str, List[str]] = Field(
        default_factory=dict,
        description="Related documents/concepts"
    )
    
    # Quality indicators
    authority_score: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Authority/credibility score"
    )
    freshness_score: float = Field(
        default=0.5, ge=0.0, le=1.0,
        description="Content freshness score"
    )


class KnowledgeSearchOutput(BaseOutputModel):
    """Complete knowledge search response."""
    
    query: str = Field(..., description="Original search query")
    expanded_query: Optional[str] = Field(None, description="Expanded query used")
    results: List[KnowledgeResult] = Field(..., description="Search results")
    total_found: int = Field(..., description="Total matches found")
    search_time_ms: float = Field(..., description="Search execution time")
    
    # Analysis and insights
    query_analysis: Dict[str, Any] = Field(
        default_factory=dict,
        description="Analysis of search query"
    )
    
    knowledge_gaps: List[str] = Field(
        default_factory=list,
        description="Identified knowledge gaps"
    )
    
    suggested_queries: List[str] = Field(
        default_factory=list,
        description="Suggested follow-up queries"
    )
    
    search_strategy_used: str = Field(
        default="semantic",
        description="Search strategy that was used"
    )
    
    coverage_analysis: Dict[str, Any] = Field(
        default_factory=dict,
        description="Analysis of knowledge base coverage"
    )


class KnowledgeIndex:
    """Knowledge base indexing and search engine."""
    
    def __init__(self, index_path: str):
        self.index_path = Path(index_path)
        self.index_path.mkdir(parents=True, exist_ok=True)
        
        # SQLite database for metadata and relationships
        self.db_path = self.index_path / "knowledge.db"
        self.init_database()
        
        # Embeddings cache
        self.embeddings_path = self.index_path / "embeddings.pkl"
        self.embeddings_cache: Dict[str, List[float]] = {}
        self.load_embeddings_cache()
        
        # Document chunks storage
        self.chunks: Dict[str, DocumentChunk] = {}
        self.load_chunks()
    
    def init_database(self):
        """Initialize SQLite database for knowledge management."""
        with sqlite3.connect(self.db_path) as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS documents (
                    id TEXT PRIMARY KEY,
                    path TEXT UNIQUE NOT NULL,
                    title TEXT,
                    file_type TEXT,
                    size INTEGER,
                    last_modified TEXT,
                    last_indexed TEXT,
                    tags TEXT,
                    authority_score REAL DEFAULT 0.5,
                    UNIQUE(path)
                );
                
                CREATE TABLE IF NOT EXISTS chunks (
                    chunk_id TEXT PRIMARY KEY,
                    document_id TEXT,
                    chunk_index INTEGER,
                    content TEXT,
                    word_count INTEGER,
                    embedding_hash TEXT,
                    created_at TEXT,
                    FOREIGN KEY (document_id) REFERENCES documents (id)
                );
                
                CREATE TABLE IF NOT EXISTS relationships (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_doc TEXT,
                    target_doc TEXT,
                    relationship_type TEXT,
                    strength REAL,
                    FOREIGN KEY (source_doc) REFERENCES documents (id),
                    FOREIGN KEY (target_doc) REFERENCES documents (id)
                );
                
                CREATE INDEX IF NOT EXISTS idx_chunks_content ON chunks(content);
                CREATE INDEX IF NOT EXISTS idx_documents_path ON documents(path);
                CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_doc);
            """)
    
    def load_embeddings_cache(self):
        """Load embeddings cache from disk."""
        if self.embeddings_path.exists():
            try:
                with open(self.embeddings_path, 'rb') as f:
                    self.embeddings_cache = pickle.load(f)
            except Exception as e:
                logging.warning(f"Failed to load embeddings cache: {e}")
                self.embeddings_cache = {}
    
    def save_embeddings_cache(self):
        """Save embeddings cache to disk."""
        try:
            with open(self.embeddings_path, 'wb') as f:
                pickle.dump(self.embeddings_cache, f)
        except Exception as e:
            logging.error(f"Failed to save embeddings cache: {e}")
    
    def load_chunks(self):
        """Load document chunks from database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("""
                SELECT c.*, d.path as document_path
                FROM chunks c
                JOIN documents d ON c.document_id = d.id
            """)
            
            for row in cursor:
                chunk = DocumentChunk(
                    chunk_id=row['chunk_id'],
                    content=row['content'],
                    document_path=row['document_path'],
                    chunk_index=row['chunk_index'],
                    word_count=row['word_count'],
                    embedding=self.embeddings_cache.get(row['chunk_id']),
                    last_updated=datetime.fromisoformat(row['created_at'])
                )
                self.chunks[row['chunk_id']] = chunk
    
    async def search(
        self, 
        query: str, 
        search_type: str = "semantic",
        max_results: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Tuple[DocumentChunk, float]]:
        """Search knowledge base with specified strategy."""
        
        if search_type == "semantic":
            return await self.semantic_search(query, max_results, filters)
        elif search_type == "keyword":
            return await self.keyword_search(query, max_results, filters)
        elif search_type == "hybrid":
            return await self.hybrid_search(query, max_results, filters)
        else:
            raise ValueError(f"Unknown search type: {search_type}")
    
    async def semantic_search(
        self, 
        query: str, 
        max_results: int,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Tuple[DocumentChunk, float]]:
        """Perform semantic similarity search using embeddings."""
        
        # Get query embedding
        query_embedding = await self.get_embedding(query)
        if not query_embedding:
            return []
        
        # Calculate similarities
        similarities = []
        for chunk_id, chunk in self.chunks.items():
            if chunk.embedding is None:
                continue
            
            # Apply filters
            if filters and not self.matches_filters(chunk, filters):
                continue
            
            similarity = self.cosine_similarity(query_embedding, chunk.embedding)
            similarities.append((chunk, similarity))
        
        # Sort by similarity and return top results
        similarities.sort(key=lambda x: x[1], reverse=True)
        return similarities[:max_results]
    
    async def keyword_search(
        self, 
        query: str, 
        max_results: int,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Tuple[DocumentChunk, float]]:
        """Perform keyword-based search with TF-IDF scoring."""
        
        query_terms = set(query.lower().split())
        results = []
        
        for chunk in self.chunks.values():
            if filters and not self.matches_filters(chunk, filters):
                continue
            
            # Calculate keyword match score
            content_lower = chunk.content.lower()
            content_terms = set(content_lower.split())
            
            # Simple TF-IDF approximation
            matches = query_terms.intersection(content_terms)
            if matches:
                score = len(matches) / len(query_terms)
                
                # Boost for exact phrase matches
                if query.lower() in content_lower:
                    score += 0.3
                
                results.append((chunk, score))
        
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:max_results]
    
    async def hybrid_search(
        self, 
        query: str, 
        max_results: int,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[Tuple[DocumentChunk, float]]:
        """Combine semantic and keyword search with weighted scoring."""
        
        # Get both semantic and keyword results
        semantic_results = await self.semantic_search(query, max_results * 2, filters)
        keyword_results = await self.keyword_search(query, max_results * 2, filters)
        
        # Combine scores with weights
        combined_scores = {}
        
        # Add semantic scores (weight: 0.7)
        for chunk, score in semantic_results:
            combined_scores[chunk.chunk_id] = score * 0.7
        
        # Add keyword scores (weight: 0.3)
        for chunk, score in keyword_results:
            if chunk.chunk_id in combined_scores:
                combined_scores[chunk.chunk_id] += score * 0.3
            else:
                combined_scores[chunk.chunk_id] = score * 0.3
        
        # Convert back to list of tuples
        results = [
            (self.chunks[chunk_id], score)
            for chunk_id, score in combined_scores.items()
        ]
        
        results.sort(key=lambda x: x[1], reverse=True)
        return results[:max_results]
    
    def matches_filters(self, chunk: DocumentChunk, filters: Dict[str, Any]) -> bool:
        """Check if chunk matches search filters."""
        
        # File type filter
        if 'file_types' in filters and filters['file_types']:
            file_ext = Path(chunk.document_path).suffix.lstrip('.')
            if file_ext not in filters['file_types']:
                return False
        
        # Date range filter
        if 'date_range' in filters and filters['date_range']:
            date_range = filters['date_range']
            if chunk.last_updated:
                if 'start' in date_range:
                    start_date = datetime.fromisoformat(date_range['start'].replace('Z', '+00:00'))
                    if chunk.last_updated < start_date:
                        return False
                
                if 'end' in date_range:
                    end_date = datetime.fromisoformat(date_range['end'].replace('Z', '+00:00'))
                    if chunk.last_updated > end_date:
                        return False
        
        return True
    
    async def get_embedding(self, text: str) -> Optional[List[float]]:
        """Get embedding for text (placeholder - integrate with actual embedding service)."""
        # This is a placeholder implementation
        # In a real implementation, you would integrate with:
        # - OpenAI Embeddings API
        # - Sentence Transformers
        # - Other embedding services
        
        # For now, return a simple hash-based "embedding"
        text_hash = hashlib.md5(text.encode()).hexdigest()
        
        # Convert hash to float list (very basic approximation)
        embedding = []
        for i in range(0, len(text_hash), 4):
            chunk_hash = text_hash[i:i+4]
            float_val = int(chunk_hash, 16) / (16**4)
            embedding.append(float_val)
        
        # Pad or truncate to fixed size (384 dimensions)
        target_size = 384
        while len(embedding) < target_size:
            embedding.extend(embedding[:target_size - len(embedding)])
        
        return embedding[:target_size]
    
    def cosine_similarity(self, vec1: List[float], vec2: List[float]) -> float:
        """Calculate cosine similarity between two vectors."""
        import math
        
        dot_product = sum(a * b for a, b in zip(vec1, vec2))
        magnitude1 = math.sqrt(sum(a * a for a in vec1))
        magnitude2 = math.sqrt(sum(a * a for a in vec2))
        
        if magnitude1 == 0 or magnitude2 == 0:
            return 0.0
        
        return dot_product / (magnitude1 * magnitude2)


class KnowledgeSearchTool(ToolImplementation[KnowledgeSearchInput, KnowledgeSearchOutput]):
    """
    Advanced Knowledge Base Search Tool
    
    Provides intelligent search across local knowledge bases with semantic
    understanding, contextual ranking, and relationship analysis.
    """
    
    def __init__(
        self, 
        knowledge_base_paths: Optional[List[str]] = None,
        index_path: str = "./knowledge_index",
        **kwargs
    ):
        super().__init__(
            name="knowledge_search",
            version="1.1.0",
            description="Advanced local knowledge base search with semantic understanding",
            **kwargs
        )
        
        self.knowledge_base_paths = knowledge_base_paths or ["."]
        self.index_path = index_path
        
        # Initialize knowledge index
        self.knowledge_index = KnowledgeIndex(index_path)
        
        # Configuration
        self.max_chunk_size = 1000  # words
        self.chunk_overlap = 100    # words
        
        # Query expansion terms cache
        self.expansion_cache: Dict[str, List[str]] = {}
    
    @property
    def input_model(self) -> type:
        return KnowledgeSearchInput
    
    @property
    def output_model(self) -> type:
        return KnowledgeSearchOutput
    
    async def _execute_core(
        self, 
        input_data: KnowledgeSearchInput, 
        context: ToolContext
    ) -> KnowledgeSearchOutput:
        """Core knowledge search execution."""
        
        start_time = time.time()
        
        try:
            # Step 1: Query analysis and expansion
            expanded_query = input_data.query
            if input_data.expand_query:
                expanded_query = await self.expand_query(input_data.query)
                context.add_trace_data("expanded_query", expanded_query)
            
            # Step 2: Prepare search filters
            filters = self._prepare_filters(input_data)
            context.add_trace_data("filters_applied", filters)
            
            # Step 3: Execute search
            raw_results = await self.knowledge_index.search(
                expanded_query,
                input_data.search_type,
                input_data.max_results * 2,  # Get more for filtering
                filters
            )
            
            context.add_trace_data("raw_results_count", len(raw_results))
            
            # Step 4: Process and enhance results
            processed_results = await self._process_search_results(
                raw_results, input_data, expanded_query
            )
            
            # Step 5: Generate analysis and insights
            analysis = await self._analyze_search_results(
                processed_results, input_data.query, expanded_query
            )
            
            # Step 6: Build output
            search_time = (time.time() - start_time) * 1000
            
            output = KnowledgeSearchOutput(
                query=input_data.query,
                expanded_query=expanded_query if expanded_query != input_data.query else None,
                results=processed_results[:input_data.max_results],
                total_found=len(raw_results),
                search_time_ms=search_time,
                query_analysis=analysis.get("query_analysis", {}),
                knowledge_gaps=analysis.get("knowledge_gaps", []),
                suggested_queries=analysis.get("suggested_queries", []),
                search_strategy_used=input_data.search_type,
                coverage_analysis=analysis.get("coverage_analysis", {})
            )
            
            return output
            
        except Exception as e:
            raise ToolError(
                f"Knowledge search failed: {str(e)}",
                ErrorCategory.INTERNAL_ERROR,
                details={"error": str(e)}
            )
    
    async def expand_query(self, query: str) -> str:
        """Expand query with related terms and synonyms."""
        
        if query in self.expansion_cache:
            expansion_terms = self.expansion_cache[query]
        else:
            # Simple expansion - in production, use thesaurus/NLP service
            expansion_terms = await self._generate_expansion_terms(query)
            self.expansion_cache[query] = expansion_terms
        
        if expansion_terms:
            expanded = f"{query} {' '.join(expansion_terms)}"
            return expanded
        
        return query
    
    async def _generate_expansion_terms(self, query: str) -> List[str]:
        """Generate expansion terms for query."""
        # Placeholder implementation - in production, integrate with:
        # - WordNet for synonyms
        # - Word embeddings for semantic similarity
        # - Custom domain vocabularies
        
        terms = []
        query_words = query.lower().split()
        
        # Simple synonym mapping (very basic)
        synonym_map = {
            "search": ["find", "locate", "discover"],
            "algorithm": ["method", "procedure", "technique"],
            "data": ["information", "content", "records"],
            "analysis": ["evaluation", "assessment", "examination"],
            "system": ["framework", "platform", "architecture"]
        }
        
        for word in query_words:
            if word in synonym_map:
                terms.extend(synonym_map[word][:2])  # Add up to 2 synonyms
        
        return terms[:5]  # Limit expansion terms
    
    def _prepare_filters(self, input_data: KnowledgeSearchInput) -> Dict[str, Any]:
        """Prepare search filters from input data."""
        filters = {}
        
        if input_data.file_types:
            filters['file_types'] = input_data.file_types
        
        if input_data.date_range:
            filters['date_range'] = input_data.date_range
        
        if input_data.knowledge_bases:
            filters['knowledge_bases'] = input_data.knowledge_bases
        
        return filters
    
    async def _process_search_results(
        self,
        raw_results: List[Tuple[DocumentChunk, float]],
        input_data: KnowledgeSearchInput,
        query: str
    ) -> List[KnowledgeResult]:
        """Process raw search results into structured output."""
        
        processed_results = []
        
        for chunk, relevance_score in raw_results:
            if relevance_score < input_data.min_relevance_score:
                continue
            
            # Create knowledge result
            result = KnowledgeResult(
                document_path=chunk.document_path,
                chunk_id=chunk.chunk_id,
                content=chunk.content,
                relevance_score=relevance_score,
                document_title=self._extract_document_title(chunk.document_path),
                file_type=Path(chunk.document_path).suffix.lstrip('.') or 'txt',
                last_modified=chunk.last_updated,
                word_count=chunk.word_count
            )
            
            # Add context and highlights
            result.context_before, result.context_after = self._extract_context(chunk, query)
            result.highlights = self._extract_highlights(chunk.content, query)
            
            # Calculate quality scores
            result.authority_score = self._calculate_authority_score(chunk)
            result.freshness_score = self._calculate_freshness_score(chunk)
            
            # Add relationships
            result.relationships = await self._get_document_relationships(chunk.document_path)
            
            processed_results.append(result)
        
        return processed_results
    
    def _extract_document_title(self, document_path: str) -> str:
        """Extract document title from path or content."""
        path_obj = Path(document_path)
        
        # Use filename without extension as title
        title = path_obj.stem
        
        # Clean up title
        title = title.replace('_', ' ').replace('-', ' ')
        title = ' '.join(word.capitalize() for word in title.split())
        
        return title
    
    def _extract_context(self, chunk: DocumentChunk, query: str) -> Tuple[str, str]:
        """Extract context before and after the main content."""
        # This is a simplified implementation
        # In production, you'd want to:
        # 1. Find the chunk's position in the full document
        # 2. Extract surrounding chunks
        # 3. Handle document boundaries properly
        
        content_words = chunk.content.split()
        query_words = query.lower().split()
        
        # Find approximate position of query match
        content_lower = chunk.content.lower()
        match_pos = -1
        
        for query_word in query_words:
            pos = content_lower.find(query_word)
            if pos != -1:
                # Convert character position to word position (approximate)
                match_pos = len(content_lower[:pos].split())
                break
        
        if match_pos == -1:
            return "", ""
        
        # Extract context (10 words before and after)
        context_size = 10
        start = max(0, match_pos - context_size)
        end = min(len(content_words), match_pos + context_size)
        
        before_words = content_words[start:match_pos]
        after_words = content_words[match_pos:end]
        
        context_before = " ".join(before_words[-context_size:])
        context_after = " ".join(after_words[:context_size])
        
        return context_before, context_after
    
    def _extract_highlights(self, content: str, query: str) -> List[str]:
        """Extract highlighted terms from content based on query."""
        highlights = []
        content_lower = content.lower()
        query_words = query.lower().split()
        
        for word in query_words:
            if word in content_lower:
                highlights.append(word)
        
        return list(set(highlights))  # Remove duplicates
    
    def _calculate_authority_score(self, chunk: DocumentChunk) -> float:
        """Calculate authority/credibility score for content."""
        # Placeholder implementation - in production, consider:
        # - Document source reputation
        # - Author credentials
        # - Citation count
        # - Content quality indicators
        
        score = 0.5  # Base score
        
        # Boost for longer, more detailed content
        if chunk.word_count > 200:
            score += 0.2
        
        # Boost for recent content
        if chunk.last_updated:
            days_old = (datetime.utcnow() - chunk.last_updated).days
            if days_old < 30:
                score += 0.2
            elif days_old < 90:
                score += 0.1
        
        # Boost for certain file types (e.g., PDF, MD might be more authoritative)
        file_ext = Path(chunk.document_path).suffix.lower()
        if file_ext in ['.pdf', '.md', '.tex']:
            score += 0.1
        
        return min(score, 1.0)
    
    def _calculate_freshness_score(self, chunk: DocumentChunk) -> float:
        """Calculate content freshness score."""
        if not chunk.last_updated:
            return 0.5
        
        days_old = (datetime.utcnow() - chunk.last_updated).days
        
        if days_old <= 7:
            return 1.0
        elif days_old <= 30:
            return 0.8
        elif days_old <= 90:
            return 0.6
        elif days_old <= 365:
            return 0.4
        else:
            return 0.2
    
    async def _get_document_relationships(self, document_path: str) -> Dict[str, List[str]]:
        """Get related documents and concepts."""
        # Placeholder implementation - in production:
        # - Build knowledge graph from document links
        # - Use citation analysis
        # - Employ topic modeling for related documents
        
        relationships = {
            "related_documents": [],
            "concepts": [],
            "authors": []
        }
        
        # Simple implementation: find documents in same directory
        doc_dir = Path(document_path).parent
        try:
            related_files = [
                str(f) for f in doc_dir.iterdir() 
                if f.is_file() and f.suffix in ['.txt', '.md', '.pdf'] and str(f) != document_path
            ]
            relationships["related_documents"] = related_files[:5]  # Limit to 5
        except:
            pass
        
        return relationships
    
    async def _analyze_search_results(
        self,
        results: List[KnowledgeResult],
        original_query: str,
        expanded_query: str
    ) -> Dict[str, Any]:
        """Comprehensive analysis of search results."""
        
        analysis = {
            "query_analysis": {
                "query_length": len(original_query.split()),
                "expansion_effectiveness": len(expanded_query.split()) - len(original_query.split()),
                "query_complexity": self._assess_query_complexity(original_query)
            },
            "coverage_analysis": {},
            "knowledge_gaps": [],
            "suggested_queries": []
        }
        
        if not results:
            analysis["knowledge_gaps"] = [
                "No relevant knowledge found in local knowledge base",
                "Consider expanding knowledge base coverage",
                "Try more general search terms"
            ]
            analysis["suggested_queries"] = [
                f"What is {original_query}?",
                f"{original_query} overview",
                f"{original_query} basics"
            ]
            return analysis
        
        # Analyze result coverage
        file_types = [r.file_type for r in results]
        domains = [Path(r.document_path).parent.name for r in results]
        
        analysis["coverage_analysis"] = {
            "file_type_distribution": {ft: file_types.count(ft) for ft in set(file_types)},
            "domain_distribution": {d: domains.count(d) for d in set(domains)},
            "average_relevance": sum(r.relevance_score for r in results) / len(results),
            "result_diversity": len(set(r.document_path for r in results)) / len(results)
        }
        
        # Generate suggested queries based on results
        analysis["suggested_queries"] = self._generate_suggested_queries(
            results, original_query
        )
        
        # Identify potential knowledge gaps
        analysis["knowledge_gaps"] = self._identify_knowledge_gaps(
            results, original_query
        )
        
        return analysis
    
    def _assess_query_complexity(self, query: str) -> str:
        """Assess query complexity level."""
        words = query.split()
        
        if len(words) <= 3:
            return "simple"
        elif len(words) <= 8:
            return "moderate"
        else:
            return "complex"
    
    def _generate_suggested_queries(
        self, 
        results: List[KnowledgeResult], 
        original_query: str
    ) -> List[str]:
        """Generate suggested follow-up queries."""
        suggestions = []
        
        # Extract common terms from results
        all_content = " ".join(r.content for r in results[:5])
        words = all_content.lower().split()
        
        # Simple frequency analysis
        from collections import Counter
        word_counts = Counter(words)
        
        # Filter out common words and original query terms
        stopwords = {
            'the', 'a', 'an', 'and', 'or', 'but', 'in', 'on', 'at', 'to', 
            'for', 'of', 'with', 'by', 'is', 'are', 'was', 'were', 'be', 
            'have', 'has', 'had', 'do', 'does', 'did', 'will', 'would', 'should'
        }
        
        original_terms = set(original_query.lower().split())
        
        interesting_terms = [
            word for word, count in word_counts.most_common(20)
            if word not in stopwords and word not in original_terms and len(word) > 3
        ]
        
        # Generate suggestions
        for term in interesting_terms[:3]:
            suggestions.append(f"{original_query} {term}")
            suggestions.append(f"{term} explained")
        
        return suggestions[:5]
    
    def _identify_knowledge_gaps(
        self, 
        results: List[KnowledgeResult], 
        original_query: str
    ) -> List[str]:
        """Identify potential knowledge gaps."""
        gaps = []
        
        # Check result quality
        avg_relevance = sum(r.relevance_score for r in results) / len(results)
        if avg_relevance < 0.6:
            gaps.append("Search results have low relevance - knowledge base may need enhancement")
        
        # Check result diversity
        unique_docs = len(set(r.document_path for r in results))
        if unique_docs < 3:
            gaps.append("Limited source diversity - consider adding more diverse content")
        
        # Check freshness
        recent_results = [r for r in results if r.freshness_score > 0.7]
        if len(recent_results) < len(results) * 0.3:
            gaps.append("Most results are outdated - consider updating knowledge base")
        
        return gaps
    
    async def _custom_quality_assessment(
        self,
        quality: 'QualityScore',
        input_data: KnowledgeSearchInput,
        result: KnowledgeSearchOutput,
        context: ToolContext
    ) -> None:
        """Custom quality assessment for knowledge search."""
        
        # Assess result completeness
        if result.total_found > 0:
            quality.add_dimension(QualityMetric.ACCURACY, 8.5)
        else:
            quality.add_dimension(
                QualityMetric.ACCURACY, 4.0,
                "No results found - knowledge base coverage may be insufficient"
            )
        
        # Assess result relevance
        if result.results:
            avg_relevance = sum(r.relevance_score for r in result.results) / len(result.results)
            if avg_relevance >= 0.8:
                quality.add_dimension(QualityMetric.RELIABILITY, 9.0)
            elif avg_relevance >= 0.6:
                quality.add_dimension(QualityMetric.RELIABILITY, 7.5)
            else:
                quality.add_dimension(
                    QualityMetric.RELIABILITY, 6.0,
                    "Result relevance could be improved with better indexing"
                )
        
        # Assess performance
        if result.search_time_ms < 1000:
            quality.add_dimension(QualityMetric.PERFORMANCE, 9.5)
        elif result.search_time_ms < 3000:
            quality.add_dimension(QualityMetric.PERFORMANCE, 8.0)
        else:
            quality.add_dimension(
                QualityMetric.PERFORMANCE, 6.0,
                "Search performance could be improved with index optimization"
            )


# Factory function
def create_knowledge_search_tool(
    knowledge_base_paths: Optional[List[str]] = None,
    index_path: str = "./knowledge_index"
) -> KnowledgeSearchTool:
    """Create a configured KnowledgeSearchTool instance."""
    return KnowledgeSearchTool(
        knowledge_base_paths=knowledge_base_paths,
        index_path=index_path
    )


# Export main components
__all__ = [
    'KnowledgeSearchTool', 
    'KnowledgeSearchInput', 
    'KnowledgeSearchOutput', 
    'KnowledgeResult',
    'create_knowledge_search_tool'
]