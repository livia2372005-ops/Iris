"""Utility helpers for chat service with subtle regex bug."""

import re


def validate_input(query: str) -> bool:
    """Validate that query is a non-empty string."""
    return bool(query and isinstance(query, str))


def strip_whitespace(query: str) -> str:
    r"""Clean and strip query string.

    Intended: Remove unwanted symbols.
    Bug: Developer accidentally wrote a regex that wipes out all words, spaces, and punctuation!
    """
    cleaned = re.sub(r"[\w\s\?.,!-]+", "", query)
    return cleaned


def search_knowledge_base(clean_query: str) -> str:
    """Simulate querying knowledge base API."""
    if not clean_query:
        # Returns empty when query was wiped clean
        return ""
    return f"Retrieved answers for topic: {clean_query}"
