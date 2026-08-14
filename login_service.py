"""
Real Pinterest email/password login, via py3-pinterest's built-in
Selenium-based login() (headless Chrome).

This REQUIRES a Chrome/Chromium binary inside the container — the default
Dockerfile does NOT install one (to keep the image small), since search
works without login. If you want the owner-only /login command to work,
uncomment the Chrome install block in the Dockerfile — see README.

Cookies are cached to disk (cred_root) by py3-pinterest itself and reused
for ~15 days. Render's filesystem is ephemeral, so a fresh deploy/restart
will require logging in again.
"""
import logging

from pinterest_service import PinterestService

logger = logging.getLogger(__name__)


def attempt_login(service: PinterestService) -> bool:
    """Blocking (Selenium). Call via asyncio.to_thread. Returns True/False."""
    try:
        return service.login()
    except Exception:
        logger.exception("Pinterest login attempt raised an unexpected exception.")
        return False
