"""
YouTube cookie expiry monitor.

Parses the Netscape cookie file (no network required) to detect expired
or near-expiry YouTube login cookies.

Env vars:
  COOKIE_PATH       Path to cookies.txt (default: /app/cookies.txt)
  COOKIE_WARN_DAYS  Days-until-expiry threshold for "near expiry" warning (default: 7)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict

log = logging.getLogger(__name__)

_COOKIE_PATH  = Path(os.getenv("COOKIE_PATH", "/app/cookies.txt"))
_WARN_DAYS    = int(os.getenv("COOKIE_WARN_DAYS", "7"))

# Cookies that only appear when the user is actually logged in to YouTube.
_LOGIN_COOKIES = frozenset({
    "SAPISID", "APISID", "SSID", "HSID", "SID",
    "__Secure-1PSID", "__Secure-3PSID",
    "LOGIN_INFO",
})

# Any cookie domain suffix that counts as "YouTube".
_YT_SUFFIXES = ("youtube.com", "youtu.be")


class CookieStatus(TypedDict):
    present:     bool          # file exists and is non-empty
    has_login:   bool          # login cookies found
    days:        int | None    # days until earliest expiry (None = session / unknown)
    expires_at:  datetime | None
    expired:     bool
    near_expiry: bool          # True if 0 <= days < WARN_DAYS


def get_status() -> CookieStatus:
    """Return the current status of cookies.txt."""
    absent: CookieStatus = {
        "present": False, "has_login": False,
        "days": None, "expires_at": None,
        "expired": False, "near_expiry": False,
    }

    if not _COOKIE_PATH.exists() or _COOKIE_PATH.stat().st_size == 0:
        return absent

    now         = datetime.now(timezone.utc)
    min_expiry: int | None = None
    has_login   = False

    try:
        with _COOKIE_PATH.open("r", encoding="utf-8", errors="ignore") as fh:
            for raw in fh:
                line = raw.strip()
                if not line or line.startswith("#"):
                    continue

                parts = line.split("\t")
                if len(parts) < 7:
                    continue

                domain      = parts[0]
                expiry_str  = parts[4]
                name        = parts[5]
                value       = parts[6] if len(parts) > 6 else ""

                # Only care about YouTube domains
                if not any(domain.endswith(s) for s in _YT_SUFFIXES):
                    continue

                # Detect login presence
                if name in _LOGIN_COOKIES and value:
                    has_login = True

                # Track earliest non-session expiry
                try:
                    expiry = int(expiry_str)
                except ValueError:
                    continue
                if expiry <= 0:
                    continue  # session cookie — no fixed expiry
                if min_expiry is None or expiry < min_expiry:
                    min_expiry = expiry

    except Exception as exc:
        log.warning("cookie_monitor: failed to parse %s: %s", _COOKIE_PATH, exc)
        return {**absent, "present": True}

    if min_expiry is None:
        # File has content but only session cookies (expiry=0)
        return {
            "present": True, "has_login": has_login,
            "days": None, "expires_at": None,
            "expired": False, "near_expiry": False,
        }

    expires_at = datetime.fromtimestamp(min_expiry, tz=timezone.utc)
    days_left  = (expires_at - now).days

    return {
        "present":    True,
        "has_login":  has_login,
        "days":       days_left,
        "expires_at": expires_at,
        "expired":    days_left < 0,
        "near_expiry": 0 <= days_left < _WARN_DAYS,
    }


def status_summary(status: CookieStatus) -> str:
    """Return a one-line human-readable summary of *status*."""
    if not status["present"]:
        return "❌ cookies.txt 없음 (bgutil로 기본 재생은 가능)"
    if not status["has_login"]:
        return "⚠️ 쿠키 있으나 로그인 쿠키 없음 — 연령제한 영상 재생 불가"
    if status["expired"]:
        exp = status["expires_at"]
        date_str = exp.strftime("%Y-%m-%d") if exp else "?"
        return f"❌ 쿠키 만료됨 (만료일: {date_str}) — 연령제한 영상 재생 불가"
    if status["near_expiry"]:
        return f"⚠️ 쿠키 만료 임박 — {status['days']}일 남음 ({status['expires_at'].strftime('%Y-%m-%d')})"
    if status["days"] is not None:
        return f"✅ 쿠키 정상 — {status['days']}일 남음 ({status['expires_at'].strftime('%Y-%m-%d')})"
    return "✅ 쿠키 있음 (세션 쿠키, 만료일 없음)"
