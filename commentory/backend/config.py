import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv(*args, **kwargs) -> None:
        return None


COMMENTORY_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"
load_dotenv(COMMENTORY_ENV_FILE)


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")


def require_github_token() -> str:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return GITHUB_TOKEN
