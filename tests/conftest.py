import os
import tempfile

# Tests use a throwaway database and never touch real API keys.
_tmp = tempfile.mkdtemp(prefix="nodexm-test-")
os.environ["DATABASE_URL"] = f"sqlite:///{_tmp}/test.db"
os.environ["NODEXM_JWT_SECRET"] = "test-secret-that-is-long-enough-for-hs256-signing"
for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "TAVILY_API_KEY", "JINA_API_KEY", "RESEND_API_KEY", "SEARXNG_URL"):
    os.environ[key] = ""

from nodexm.db import init_db  # noqa: E402

init_db()
