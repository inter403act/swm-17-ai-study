import os

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> None:
        return None


load_dotenv()


GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GITHUB_API_URL = os.getenv("GITHUB_API_URL", "https://api.github.com")


def require_github_token() -> str:
    if not GITHUB_TOKEN:
        raise RuntimeError("GITHUB_TOKEN is not set")
    return GITHUB_TOKEN
