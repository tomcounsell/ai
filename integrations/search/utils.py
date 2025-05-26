"""Search utility functions."""

from typing import Tuple, Optional


def is_search_query(text: str) -> Tuple[bool, Optional[str]]:
    """
    Detect if a message is a search query and extract the search terms.
    
    Args:
        text: Message text to analyze
        
    Returns:
        Tuple of (is_search, search_terms)
    """
    text = text.strip()
    text_lower = text.lower()
    
    # Check longer patterns first to avoid partial matches
    all_patterns = [
        'search for ',
        'find me ',
        'look up ',
        'look for ',
        'search ',
        'search:',
        'find ',
        'lookup ',
        'google ',
        'bing '
    ]
    
    for pattern in all_patterns:
        if text_lower.startswith(pattern):
            query = text[len(pattern):].strip()
            if query:  # Make sure there's actually something to search for
                return True, query
    
    return False, None


def extract_search_terms(text: str) -> str:
    """
    Extract clean search terms from a message.
    
    Args:
        text: Message text
        
    Returns:
        Cleaned search terms
    """
    # Remove common question words and clean up
    question_words = ['what', 'where', 'when', 'who', 'why', 'how', 'is', 'are', 'can', 'could', 'would', 'should']
    
    words = text.split()
    cleaned_words = []
    
    for word in words:
        clean_word = word.strip('.,!?').lower()
        if clean_word not in question_words and len(clean_word) > 1:
            cleaned_words.append(word.strip('.,!?'))
    
    return ' '.join(cleaned_words)