"""Central configuration. All tunables come from env vars so scaling = infra change, not code change."""
import os
from dotenv import load_dotenv

load_dotenv()

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN", "")

USER_AGENT = os.getenv("USER_AGENT", "GraphOneResearchBot/1.0")
GLOBAL_CONCURRENCY = int(os.getenv("GLOBAL_CONCURRENCY", "50"))
PER_HOST_CONCURRENCY = int(os.getenv("PER_HOST_CONCURRENCY", "5"))
REQUEST_TIMEOUT_S = float(os.getenv("REQUEST_TIMEOUT_S", "30"))
MAX_RETRIES = int(os.getenv("MAX_RETRIES", "5"))
BACKOFF_BASE_S = float(os.getenv("BACKOFF_BASE_S", "1.0"))
BACKOFF_CAP_S = float(os.getenv("BACKOFF_CAP_S", "60.0"))

SCHEMA_VERSION = "1.0"
