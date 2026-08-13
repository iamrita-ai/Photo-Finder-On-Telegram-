"""
Central configuration. Everything is read from environment variables so the
same code runs unmodified on Render (Web Service) via env vars set in the
dashboard, or locally via a `.env` file (see .env.example).
"""
import os

# ---- Telegram -----------------------------------------------------------
BOT_TOKEN = os.environ["BOT_TOKEN"]

# ---- MongoDB --------------------------------------------------------------
MONGO_URI = os.environ["MONGO_URI"]
MONGO_DB_NAME = os.getenv("MONGO_DB_NAME", "pinterest_bot")

# ---- Owners (hardcoded, matches your other bots) --------------------------
OWNER_IDS = {6518065496, 1598576202}
_extra_owners = os.getenv("EXTRA_OWNER_IDS", "")
OWNER_IDS.update(int(x) for x in _extra_owners.split(",") if x.strip().isdigit())

# ---- Web service / webhook -------------------------------------------------
# Render injects PORT automatically for Web Services.
PORT = int(os.getenv("PORT", "8000"))

# Render also injects RENDER_EXTERNAL_HOSTNAME automatically. If you set
# WEBHOOK_URL yourself in the dashboard, that takes priority.
WEBHOOK_URL = os.getenv("WEBHOOK_URL") or (
    f"https://{os.environ['RENDER_EXTERNAL_HOSTNAME']}"
    if os.getenv("RENDER_EXTERNAL_HOSTNAME")
    else None
)
WEBHOOK_SECRET = os.getenv("WEBHOOK_SECRET", "")

if not WEBHOOK_URL:
    raise RuntimeError(
        "WEBHOOK_URL not set and RENDER_EXTERNAL_HOSTNAME not found. "
        "On Render this is automatic. Locally / elsewhere, set WEBHOOK_URL "
        "manually, e.g. https://your-app.onrender.com"
    )

# ---- Search behaviour -------------------------------------------------------
DEFAULT_RESULT_LIMIT = int(os.getenv("DEFAULT_RESULT_LIMIT", "10"))
# Inline results each require a separate get_pin() call to resolve media URLs
# (see pinterest_service.resolve) run concurrently — keep this modest to
# avoid slow inline responses or Pinterest rate-limiting.
INLINE_RESULT_LIMIT = int(os.getenv("INLINE_RESULT_LIMIT", "10"))

# ---- Optional Pinterest login (best-effort, see login_service.py) ---------
PINTEREST_EMAIL = os.getenv("PINTEREST_EMAIL", "")
PINTEREST_PASSWORD = os.getenv("PINTEREST_PASSWORD", "")

# ---- Logging ----------------------------------------------------------------
# Set LOG_LEVEL=DEBUG in Render env vars to see full raw Pinterest payloads
# in the logs when diagnosing "media load nahi ho paaya" issues.
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO").upper()
