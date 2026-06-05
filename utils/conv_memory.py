"""
Conversation memory for the LLM listener.

Two layers:
  1. Channel history  – recent 30 message-pairs (user+bot) per channel, kept
                        in-memory and injected as the message thread on every
                        LLM call so Claude has rolling conversation context.

  2. Member memory    – per-member JSON file that stores:
                          • summary   : rolling compact summary (updated every
                                        COMPACT_EVERY new pairs by Claude)
                          • recent    : last MEMBER_RECENT pairs with timestamps
                        Injected as a system-prompt block.  By default only the
                        last DEFAULT_LOAD pairs are sent; pass full=True to send
                        all recent pairs (used when the user explicitly asks for
                        older memories).

File path: <DATA_DIR>/<guild_id>/<member_id>.json
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from collections import deque
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import anthropic

log = logging.getLogger(__name__)

_KST = timezone(timedelta(hours=9))
_DATA_DIR = Path(os.getenv("MEMBER_MEMORY_PATH", "/app/data/member_memory"))

CHANNEL_WINDOW = 30   # message-pairs kept per channel (in-memory)
MEMBER_RECENT  = 30   # message-pairs kept in member file
DEFAULT_LOAD   = 10   # pairs injected by default
COMPACT_EVERY  = 30   # compact after this many NEW member pairs


def _now_iso() -> str:
    return datetime.now(_KST).isoformat(timespec="seconds")


def _member_path(guild_id: int, member_id: int) -> Path:
    return _DATA_DIR / str(guild_id) / f"{member_id}.json"


def _load_member(guild_id: int, member_id: int) -> dict:
    p = _member_path(guild_id, member_id)
    if not p.exists():
        return {"message_count": 0, "summary": "", "recent": []}
    try:
        with p.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        log.warning("conv_memory: load failed %s: %s", p, exc)
        return {"message_count": 0, "summary": "", "recent": []}


def _save_member(guild_id: int, member_id: int, data: dict) -> None:
    p = _member_path(guild_id, member_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    try:
        tmp = p.with_suffix(".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        tmp.replace(p)
    except Exception as exc:
        log.error("conv_memory: save failed %s: %s", p, exc)


class ConvMemory:
    def __init__(self) -> None:
        # channel_id → deque of {"role": "user"|"assistant", "content": str}
        # Each pair = 2 entries; deque maxlen = CHANNEL_WINDOW * 2
        self._channel: dict[int, deque[dict]] = {}
        # guild_id → member_id → asyncio.Lock (prevents concurrent compaction)
        self._compact_locks: dict[tuple[int, int], asyncio.Lock] = {}

    # ── channel history ───────────────────────────────────────────────────────

    def record(
        self,
        channel_id: int,
        guild_id: int,
        member_id: int,
        display_name: str,
        user_text: str,
        bot_reply: str,
    ) -> None:
        """Store one exchange (user + bot) in both channel and member memory."""
        # Channel (in-memory ring buffer)
        if channel_id not in self._channel:
            self._channel[channel_id] = deque(maxlen=CHANNEL_WINDOW * 2)
        buf = self._channel[channel_id]
        buf.append({"role": "user",      "content": user_text})
        buf.append({"role": "assistant", "content": bot_reply})

        # Member file (sync write — small JSON, acceptable)
        data = _load_member(guild_id, member_id)
        data.setdefault("display_name", display_name)
        data.setdefault("message_count", 0)
        data.setdefault("summary", "")
        data.setdefault("recent", [])

        ts = _now_iso()
        data["recent"].append({"role": "user",      "content": user_text, "ts": ts})
        data["recent"].append({"role": "assistant",  "content": bot_reply, "ts": ts})

        # Trim to MEMBER_RECENT pairs
        max_entries = MEMBER_RECENT * 2
        if len(data["recent"]) > max_entries:
            data["recent"] = data["recent"][-max_entries:]

        data["message_count"] += 1
        _save_member(guild_id, member_id, data)

    def get_channel_history(self, channel_id: int) -> list[dict]:
        """Return recent channel messages as a list ready for the messages= array."""
        buf = self._channel.get(channel_id)
        if not buf:
            return []
        return [{"role": m["role"], "content": m["content"]} for m in buf]

    # ── member memory ─────────────────────────────────────────────────────────

    def get_member_system_block(
        self,
        guild_id: int,
        member_id: int,
        display_name: str,
        full: bool = False,
    ) -> str | None:
        """Build a system-prompt text block for the current member.

        Returns None when there is no stored memory yet.
        full=True injects all recent pairs; False injects only the last DEFAULT_LOAD.
        """
        data = _load_member(guild_id, member_id)
        summary = data.get("summary", "").strip()
        recent  = data.get("recent", [])
        total   = data.get("message_count", 0)

        if not summary and not recent:
            return None

        parts: list[str] = [f"[{display_name}님과의 기억 (총 {total}회 대화)]"]

        if summary:
            parts.append(f"[요약]\n{summary}")

        pairs_to_show = recent if full else recent[-(DEFAULT_LOAD * 2):]
        if pairs_to_show:
            conv_lines = []
            for m in pairs_to_show:
                role_label = display_name if m["role"] == "user" else "동쿠"
                conv_lines.append(f"{role_label}: {m['content']}")
            label = "최근 대화 (전체)" if full else f"최근 대화 (최근 {DEFAULT_LOAD}회)"
            parts.append(f"[{label}]\n" + "\n".join(conv_lines))

        return "\n\n".join(parts)

    def should_compact(self, guild_id: int, member_id: int) -> bool:
        data = _load_member(guild_id, member_id)
        cnt  = data.get("message_count", 0)
        return cnt > 0 and cnt % COMPACT_EVERY == 0

    async def compact(
        self,
        guild_id: int,
        member_id: int,
        anthropic_client: "anthropic.AsyncAnthropic",
    ) -> None:
        """Summarise recent member messages and update the stored summary.

        Uses an asyncio.Lock per member to avoid concurrent compaction.
        """
        key  = (guild_id, member_id)
        lock = self._compact_locks.setdefault(key, asyncio.Lock())
        if lock.locked():
            return  # already running

        async with lock:
            data    = _load_member(guild_id, member_id)
            recent  = data.get("recent", [])
            old_sum = data.get("summary", "").strip()
            name    = data.get("display_name", str(member_id))

            if not recent:
                return

            conv_text = "\n".join(
                f"{'유저' if m['role'] == 'user' else '봇'}: {m['content']}"
                for m in recent
            )
            prior = f"[이전 요약]\n{old_sum}\n\n" if old_sum else ""
            prompt = (
                f"{prior}"
                f"아래는 Discord 뮤직봇 '동쿠'와 {name}의 대화 기록입니다.\n\n"
                f"{conv_text}\n\n"
                "이 대화를 바탕으로 이 유저에 대한 중요한 정보를 5~10줄로 간결하게 정리해줘. "
                "(음악 취향, 좋아하는 아티스트/장르, 자주 하는 요청, 대화 패턴 등. "
                "이전 요약이 있으면 합쳐서 업데이트해줘.)"
            )

            try:
                resp = await anthropic_client.messages.create(
                    model="claude-haiku-4-5-20251001",
                    max_tokens=512,
                    messages=[{"role": "user", "content": prompt}],
                )
                new_summary = resp.content[0].text.strip()
            except Exception as exc:
                log.error("conv_memory: compaction failed for %s/%s: %s", guild_id, member_id, exc)
                return

            data["summary"] = new_summary
            _save_member(guild_id, member_id, data)
            log.info("conv_memory: compacted memory for member %s (guild %s)", member_id, guild_id)
