"""
Optional, best-effort Pinterest email/password login.

IMPORTANT — please read before relying on this:
Pinterest search/fetch in this bot (pinterest_service.py) works fully
WITHOUT logging in - it uses Pinterest's public endpoints. This module
is only here because you asked for email/password login "if needed".

Pinterest actively fights automated logins (CSRF, device fingerprinting,
CAPTCHA, 2FA), and their login endpoint can change without notice. This
implementation is a best-effort scaffold using the same flow real login
scripts use (fetch CSRF cookie -> POST credentials to the internal
UserSessionResource endpoint). It may stop working if Pinterest changes
that endpoint, and it will simply fail closed (return None) rather than
break the bot - the /login command reports failure clearly instead of
crashing anything.

Triggered only via the owner-only /login command in bot.py.
"""
import json
import logging
from typing import Optional

import requests

logger = logging.getLogger(__name__)

LOGIN_PAGE = "https://www.pinterest.com/login/"
LOGIN_API = "https://www.pinterest.com/resource/UserSessionResource/create/"

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
)


def attempt_login(email: str, password: str) -> Optional[list[dict]]:
    """Try to log in. Returns a list of cookie dicts on success, None on failure."""
    session = requests.Session()
    session.headers.update({"User-Agent": USER_AGENT})

    try:
        resp = session.get(LOGIN_PAGE, timeout=10)
        resp.raise_for_status()
        csrf_token = session.cookies.get("csrftoken")
        if not csrf_token:
            logger.warning("Pinterest login: could not obtain csrftoken, aborting.")
            return None

        headers = {
            "X-CSRFToken": csrf_token,
            "X-Requested-With": "XMLHttpRequest",
            "Referer": LOGIN_PAGE,
            "Origin": "https://www.pinterest.com",
        }
        data_payload = {
            "options": {
                "username_or_email": email,
                "password": password,
                "ensure_org_related": [],
            },
            "context": {},
        }

        resp = session.post(
            LOGIN_API,
            headers=headers,
            data={"source_url": "/login/", "data": json.dumps(data_payload)},
            timeout=15,
        )

        if resp.status_code != 200:
            logger.warning("Pinterest login: unexpected status %s", resp.status_code)
            return None

        body = resp.json()
        status = body.get("resource_response", {}).get("status")
        if status != "success":
            logger.warning("Pinterest login rejected: %s", body.get("resource_response"))
            return None

        return [
            {"name": c.name, "value": c.value, "domain": c.domain, "path": c.path}
            for c in session.cookies
        ]
    except Exception:
        logger.exception("Pinterest login attempt raised an exception.")
        return None
