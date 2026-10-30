"""All settings in one place. Values come from environment variables so no
secret ever lives in the code."""

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Read KEY=value lines from .env for local runs (same on Windows, macOS and
    Linux). Real environment variables, e.g. Space secrets, always win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip().strip('"').strip("'")
        if key.strip() and value:
            os.environ.setdefault(key.strip(), value)


_load_dotenv(ROOT / ".env")

# Vercel runs the app as a serverless function: the code folder is read-only
# and only /tmp can be written (and is wiped between cold starts).
SERVERLESS = bool(os.getenv("VERCEL"))
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data"))
LOG_DIR = Path(os.getenv("LOG_DIR", "/tmp/logs" if SERVERLESS else ROOT / "logs"))
KB_PATH = DATA_DIR / "kb.json"

# Gemini. GEMINI_MODEL can be a comma separated list; the first one that
# exists for this key is used, and when one hits its free-tier quota the next
# one takes over. Flash-Lite comes first: fast, and a much larger free quota.
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODELS = [
    m.strip()
    for m in os.getenv(
        "GEMINI_MODEL",
        "gemini-3.5-flash-lite,gemini-3.1-flash-lite,gemini-flash-lite-latest,gemini-3.5-flash",
    ).split(",")
    if m.strip()
]

# Protects /api/refresh and the admin export endpoints.
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")

# Optional voice agent (Vapi). The public key is meant for browsers.
VAPI_PUBLIC_KEY = os.getenv("VAPI_PUBLIC_KEY", "")
VAPI_ASSISTANT_ID = os.getenv("VAPI_ASSISTANT_ID", "")
VAPI_TOOL_SECRET = os.getenv("VAPI_TOOL_SECRET", "")

# Crawler identity and politeness.
USER_AGENT = os.getenv(
    "CRAWLER_USER_AGENT",
    "PlacidWayKBBot/1.0 (assessment chatbot; +https://github.com/zsufyan86)",
)
MIN_DELAY_SECONDS = float(os.getenv("MIN_DELAY_SECONDS", "1.0"))

# Retrieval.
TOP_K = int(os.getenv("TOP_K", "8"))
MAX_PER_PAGE = int(os.getenv("MAX_PER_PAGE", "4"))

# Abuse limits (per IP, per window).
RATE_LIMIT_REQUESTS = int(os.getenv("RATE_LIMIT_REQUESTS", "40"))
RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "600"))
# All visitors together, per window. Protects the free Gemini quota even if
# someone fakes X-Forwarded-For to dodge the per-IP limit.
GLOBAL_RATE_LIMIT_REQUESTS = int(os.getenv("GLOBAL_RATE_LIMIT_REQUESTS", "200"))
MAX_QUESTION_CHARS = 800
MAX_HISTORY_TURNS = 6
