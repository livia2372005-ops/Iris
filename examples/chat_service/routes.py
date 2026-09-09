"""Route handler representing a FastAPI/Flask chat endpoint."""

from examples.chat_service.utils import (
    search_knowledge_base,
    strip_whitespace,
    validate_input,
)


def handle_chat(query: str, auth_token: str) -> dict:
    """Handle chat query endpoint.

    Takes a user query and an auth_token, validates input,
    cleans query, and queries the knowledge base.
    """
    is_valid = validate_input(query)
    if not is_valid:
        return {"error": "Invalid query", "response": ""}

    cleaned_query = strip_whitespace(query)
    search_result = search_knowledge_base(cleaned_query)

    return {
        "status": "success",
        "query": query,
        "cleaned_query": cleaned_query,
        "response": search_result,
    }
