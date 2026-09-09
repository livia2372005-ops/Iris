from pathlib import Path
import sys

# Ensure Iris root is in python path
ROOT_DIR = Path(__file__).resolve().parent.parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from iris.core.runtime import auto_attach_from_db
from examples.chat_service.routes import handle_chat


def main():
    # If a session was armed via MCP server, auto_attach_from_db hooks into it
    auto_attach_from_db()

    query = "How do I use Antigravity with Iris?"
    secret_token = "secret_jwt_bearer_token_xyz987654321"

    print(f"Sending query: '{query}' with secret token...")
    result = handle_chat(query, secret_token)
    print(f"Server returned: {result}")


if __name__ == "__main__":
    main()
