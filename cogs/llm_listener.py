"""
LLM Listener Cog – intercepts Discord messages and routes them through
Claude (Tool Use) to the Music cog.

Triggers on:
  1. Any message in a designated music channel (MUSIC_CHANNEL_IDS env var)
  2. Any message that @mentions the bot (any channel)

Search flow (multi-turn):
  User: "BTS 검색해줘"
    → search_songs tool → results stored in _search_context[channel_id]
    → bot replies with numbered list
  User: "1, 3번 추가해줘"
    → _handle_llm injects previous search display as assistant context
    → Claude calls select_from_search([1, 3])
    → _search_context cleared after selection
"""
from __future__ import annotations

import asyncio
import json as _json
import logging
import os
import urllib.parse
import urllib.request
from typing import Optional

import anthropic
import discord
from discord.ext import commands

from cogs.music import Music
from utils.llm_tools import MUSIC_TOOLS
from utils.youtube import search_youtube_multi

log = logging.getLogger(__name__)

# ── system prompt ─────────────────────────────────────────────────────────────
_SYSTEM_PROMPT = """
[ROLE]
You are a friendly Discord music bot assistant.

Your primary job is to understand the user's natural language message and choose the correct music control behavior.

If the user's message is related to music control or bot commands, call the appropriate tool(s).
If the user's message is not related to music control or bot commands, do NOT call any tool and reply conversationally in the same language the user used.

When you call a tool:
- Do not add any extra text before or after tool calls.
- Do not include roleplay, cat speech, emojis, or casual filler in tool arguments.
- The bot will handle the tool result.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[TOOL SELECTION GUIDE]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Single song request:
→ play_song(title, artist)
- Provide the actual song title and artist — not a mood description or genre.
- ✅ title="Dynamite" artist="BTS"
- ❌ title="신나는 노래" (this is a mood, not a song)

Multiple songs at once:
Examples: "3개 틀어줘", "5곡 추천해줘", "카녜웨스트 5개", "우울한 노래 10개"
→ Call play_song multiple times — once per song.
- YOU decide which specific real songs to include.
- For artist requests: pick well-known songs by that artist.
- For mood/genre requests: pick songs that actually fit the mood.
- Always provide real title + artist for every call.

User wants to browse/search before choosing:
Examples: "검색해줘", "search for X", "찾아줘"
→ search_songs

User is picking from previous search results:
Examples: "1번 추가", "2랑 4번 넣어줘", "전부"
→ select_from_search
- Only use this when the previous assistant message showed a numbered search list.

User asks for info about current song/artist/album:
Examples: "지금 곡 정보 알려줘", "가수 정보", "앨범 알려줘"
→ get_music_info

User asks about play history:
Examples: "기록 보여줘", "이전에 뭐 들었어", "show history"
→ show_history

User is picking from previous history list:
Examples: "2번 다시 틀어줘", "전부 재생해줘"
→ select_from_history
- Only use this when the previous assistant message showed a numbered history list.

User asks to see a playlist:
Examples: "내 플리", "플레이리스트 보여줘", "X의 플리"
→ view_playlist

User adds a song to their own playlist:
Examples: "지금 곡 내 플리에 추가해줘", "BTS Dynamite 내 플리에 넣어줘"
→ add_to_playlist
- Never use this for another user's playlist.

User removes a song from their own playlist:
Examples: "내 플리에서 3번 빼줘", "내 플레이리스트에서 Dynamite 삭제해줘"
→ remove_from_playlist
- Never use this for another user's playlist.

User is picking from previous playlist display:
Examples: "1번 재생", "2랑 5번 틀어줘", "전부 추가"
→ select_from_playlist
- Only use this when the previous assistant message showed a numbered playlist.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[PERSONALITY FOR NORMAL REPLIES]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

You are a talking anime-style cat character.

Style:
- Use friendly casual speech.
- Use 반말 in Korean.
- Be warm, playful, and slightly otaku/anime-like.
- You like energetic reactions and a confident vibe.
- Occasionally use expressions like "~냥", "~냐옹", "집사야", "헉", "우먀아", "큭".
- Do not overuse cat expressions.
- Do not put "~냥" at the end of every sentence.
- Prioritize clarity over roleplay.
- In serious or technical explanations, reduce the roleplay and stay helpful.

Good examples:
- "오오 이 노래 좋다냥."
- "헉 집사야, 그건 이렇게 하면 돼."
- "우먀아, 이건 꽤 좋은 선택이야."

Bad examples:
- "냥냥냥냥냥."
- "BTS 냥~ 검색할게 냥~"
- Adding cat speech inside tool arguments.

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
[HELP / COMMAND GUIDE]
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

If the user asks what commands are available, explain based on the list below.

🎵 재생 제어
- 곡 재생/추가: "다이너마이트 틀어줘", "BTS 틀어줘"
- 여러 곡 한번에: "카녜웨스트 5개 틀어줘", "우울할때 노래 10개 추천해줘"
- 일시정지: "멈춰", "pause"
- 재개: "다시 재생", "resume"
- 스킵: "다음", "skip"
- 정지 + 큐 비우기: "그만", "stop"
- 볼륨 조절: "볼륨 50", "volume 80"
- 반복 모드: "한 곡 반복", "전체 반복", "반복 끄기"

🔍 검색
- 유튜브 검색: "BTS 검색해줘"
- 검색 결과 선택: "1번 추가해줘", "1, 3번 넣어줘"

📋 대기열 관리
- 대기열 보기: "큐 보여줘"
- 번호로 삭제: "2, 4, 7번 지워줘"
- 제목으로 삭제: "큐에서 Dynamite 빼줘"

📁 개인 플레이리스트
- 내 플리 보기: "내 플레이리스트 보여줘"
- 다른 사람 플리 보기: "jinwook의 플리 보여줘"
- 플리에서 재생: 플리 보기 후 "1, 3번 재생해줘"
- 현재 곡 추가: "지금 곡 내 플리에 추가해줘"
- 검색해서 추가: "BTS Dynamite 내 플리에 넣어줘"
- 플리에서 삭제: "내 플리에서 3번 빼줘"

🕐 재생 기록
- 기록 보기: "기록 보여줘", "이전에 뭐 들었어"
- 기록에서 재생: 기록 보기 후 "2번 다시 틀어줘"

🎤 음악 정보
- 곡 정보: "지금 곡 정보 알려줘"
- 아티스트 정보: "가수 정보 알려줘"
- 앨범 정보: "앨범 알려줘"
- 상세 정보: "더 자세히 알려줘"

🔊 채널 제어
- 입장: "들어와", "join"
- 퇴장: "나가", "leave"
"""

# ── helper: parse MUSIC_CHANNEL_IDS ──────────────────────────────────────────

def _namu_fetch_sync(query: str) -> tuple[str, str] | None:
    """Fetch a brief excerpt from 나무위키 (namu.wiki) for *query*.

    Uses the /w/ direct-article endpoint with a browser-like User-Agent.
    Extracts the <meta name="description"> tag — a short summary the site
    generates for every article.  Returns (description, article_url) or None.
    Runs in a thread pool — do NOT call from the event loop directly.
    """
    import re
    _UA = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    )
    encoded = urllib.parse.quote(query)
    url = f"https://namu.wiki/w/{encoded}"
    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": _UA,
            "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.8",
            "Accept": "text/html,application/xhtml+xml",
        })
        with urllib.request.urlopen(req, timeout=7) as r:
            final_url = r.url
            html = r.read().decode("utf-8", errors="ignore")

        # namu.wiki embeds a short summary in <meta name="description">
        m = re.search(
            r'<meta\s+name=["\']description["\']\s+content=["\']([^"\']{30,})["\']',
            html, re.IGNORECASE,
        )
        if m:
            text = (
                m.group(1)
                .replace("&#39;", "'")
                .replace("&amp;", "&")
                .replace("&lt;", "<")
                .replace("&gt;", ">")
                .strip()
            )
            if len(text) > 30:
                log.debug("namu.wiki found: %r (%d chars)", query, len(text))
                return text, final_url
    except Exception as exc:
        log.debug("namu.wiki fetch failed for %r: %s", query, exc)
    return None


def _wiki_fetch_sync(query: str) -> tuple[str, str] | None:
    """Search Wikipedia (English then Korean) for *query*.

    Returns (extract_text, article_url) on success, or None if nothing useful
    was found.  Runs in a thread pool — do not call from the event loop directly.
    """
    _UA = "DiscordMusicBot/1.0 (https://github.com; bot)"

    for lang in ("en", "ko"):
        base = f"https://{lang}.wikipedia.org"
        try:
            # Step 1 – find the closest page title
            search_url = (
                f"{base}/w/api.php?"
                + urllib.parse.urlencode({
                    "action": "opensearch",
                    "search": query,
                    "limit": 1,
                    "format": "json",
                })
            )
            req = urllib.request.Request(search_url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req, timeout=6) as r:
                results = _json.loads(r.read())

            if not results[1]:          # no matches
                continue

            page_title = results[1][0]
            page_url   = results[3][0] if results[3] else (
                f"{base}/wiki/{urllib.parse.quote(page_title)}"
            )

            # Step 2 – get the page summary extract
            summary_url = (
                f"{base}/api/rest_v1/page/summary/"
                + urllib.parse.quote(page_title)
            )
            req2 = urllib.request.Request(summary_url, headers={"User-Agent": _UA})
            with urllib.request.urlopen(req2, timeout=6) as r:
                summary = _json.loads(r.read())

            extract = summary.get("extract", "").strip()
            if len(extract) > 80:       # skip stubs / disambiguation pages
                log.debug("Wikipedia [%s] found: %r (%d chars)", lang, page_title, len(extract))
                return extract, page_url

        except Exception as exc:
            log.debug("Wikipedia [%s] failed for %r: %s", lang, query, exc)
            continue

    return None


def _load_channel_ids() -> set[int]:
    raw = os.getenv("MUSIC_CHANNEL_IDS", "")
    ids: set[int] = set()
    for part in raw.split(","):
        part = part.strip()
        if part.isdigit():
            ids.add(int(part))
    return ids


def _fmt_dur(seconds: int) -> str:
    """Format seconds → m:ss or h:mm:ss."""
    if not seconds:
        return "LIVE"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m}:{s:02d}"


def _build_play_query(inputs: dict) -> str:
    """Build a YouTube search query from play_song tool inputs (artist + title)."""
    artist = inputs.get("artist", "").strip()
    title  = inputs.get("title", "").strip()
    return f"{artist} - {title}" if artist else title


class LLMListener(commands.Cog):
    def __init__(self, bot: commands.Bot) -> None:
        self.bot = bot
        self.music_channel_ids: set[int] = _load_channel_ids()
        self._anthropic = anthropic.AsyncAnthropic(
            api_key=os.environ["ANTHROPIC_API_KEY"]
        )
        self._model: str = os.getenv("CLAUDE_MODEL", "claude-sonnet-4-6")
        # channel_id → {"results": list[dict], "display": str}
        # Stores the last search results per channel for follow-up selection.
        self._search_context: dict[int, dict] = {}
        # Same structure but for playback history selections.
        self._history_context: dict[int, dict] = {}
        # Same structure but for personal playlist selections.
        self._playlist_context: dict[int, dict] = {}

    # ── event listener ────────────────────────────────────────────────────────

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        # Ignore bot messages
        if message.author.bot:
            return
        # Ignore DMs (no voice channels in DMs)
        if not message.guild:
            return

        mentioned = self.bot.user in message.mentions
        in_music_channel = message.channel.id in self.music_channel_ids

        if not (mentioned or in_music_channel):
            return

        # Strip the @mention prefix so Claude sees clean text
        content = message.content
        if mentioned and self.bot.user:
            content = content.replace(f"<@{self.bot.user.id}>", "").strip()
            content = content.replace(f"<@!{self.bot.user.id}>", "").strip()

        if not content:
            await message.reply("무엇을 도와드릴까요? 🎵")
            return

        async with message.channel.typing():
            reply = await self._handle_llm(message, content)

        if reply is None:
            return

        if isinstance(reply, discord.Embed):
            await message.reply(embed=reply)
        else:
            await message.reply(str(reply))

    # ── LLM dispatch ─────────────────────────────────────────────────────────

    async def _handle_llm(
        self, message: discord.Message, user_text: str
    ) -> Optional[str | discord.Embed]:
        """Send *user_text* to Claude with tool definitions; execute the result.

        If there are pending search results for this channel, the previous
        search display is injected as assistant context so Claude understands
        follow-up index selections like '1, 3번 추가해줘'.
        """
        # Append currently playing song so Claude can fill in get_music_info.search_query
        music_cog: Optional[Music] = self.bot.cogs.get("Music")  # type: ignore[assignment]
        current_song = music_cog.get_current_song(message.guild) if music_cog else None
        enriched_text = (
            f"{user_text}\n[현재 재생 중: {current_song.title}]"
            if current_song else user_text
        )

        # Build messages — inject search / history / playlist context when available.
        search_ctx   = self._search_context.get(message.channel.id)
        hist_ctx     = self._history_context.get(message.channel.id)
        playlist_ctx = self._playlist_context.get(message.channel.id)
        active_ctx   = search_ctx or hist_ctx or playlist_ctx

        if active_ctx:
            messages = [
                {"role": "user", "content": "이전 명령"},
                {"role": "assistant", "content": active_ctx["display"]},
                {"role": "user", "content": enriched_text},
            ]
        else:
            messages = [{"role": "user", "content": enriched_text}]

        try:
            response = await self._anthropic.messages.create(
                model=self._model,
                max_tokens=1024,
                system=[
                    {
                        "type": "text",
                        "text": _SYSTEM_PROMPT,
                        # Cache the static system prompt to save API cost
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                tools=MUSIC_TOOLS,  # type: ignore[arg-type]
                messages=messages,  # type: ignore[arg-type]
            )
        except anthropic.APIError as exc:
            log.exception("Anthropic API error")
            return f"❌ LLM 오류: {exc}"

        # ── collect all tool_use blocks ───────────────────────────────────────
        tool_blocks = [b for b in response.content if b.type == "tool_use"]

        if not tool_blocks:
            # Claude replied conversationally (no tool call)
            text_block = next(
                (b for b in response.content if b.type == "text"), None
            )
            return text_block.text if text_block else None

        # Clear selection contexts for any unrelated tool call.
        tool_names = {tb.name for tb in tool_blocks}
        if not tool_names & {"search_songs", "select_from_search"}:
            self._search_context.pop(message.channel.id, None)
        if not tool_names & {"show_history", "select_from_history"}:
            self._history_context.pop(message.channel.id, None)
        if not tool_names & {"view_playlist", "select_from_playlist"}:
            self._playlist_context.pop(message.channel.id, None)

        # Single tool call — dispatch and return directly (original path)
        if len(tool_blocks) == 1:
            tb = tool_blocks[0]
            return await self._dispatch(message, tb.name, tb.input)

        # ── multiple tool calls (e.g. play_song × N for multi-song request) ───
        # Batch play_song calls into play_songs for efficiency;
        # run any other tool calls in parallel alongside.
        music_cog: Optional[Music] = self.bot.cogs.get("Music")  # type: ignore
        if music_cog is None:
            return "❌ Music 모듈이 로드되지 않았습니다."

        play_blocks  = [tb for tb in tool_blocks if tb.name == "play_song"]
        other_blocks = [tb for tb in tool_blocks if tb.name != "play_song"]

        tasks: list = []
        for tb in other_blocks:
            tasks.append(self._dispatch(message, tb.name, tb.input))

        if play_blocks:
            queries = [_build_play_query(tb.input) for tb in play_blocks]
            tasks.append(
                music_cog.play_songs(
                    message.guild, message.author, queries, message.channel  # type: ignore
                )
            )

        results = await asyncio.gather(*tasks, return_exceptions=True)

        # Surface errors, otherwise return the play_songs summary
        for r in results:
            if isinstance(r, Exception):
                log.error("multi-tool call error: %s", r)
        valid = [
            r for r in results
            if r and not isinstance(r, Exception)
        ]
        return valid[-1] if valid else None  # play_songs result is last

    # ── search helper ─────────────────────────────────────────────────────────

    async def _do_search(
        self, message: discord.Message, query: str, count: int
    ) -> str:
        """Run a YouTube search, store results, return a formatted numbered list."""
        try:
            results = await search_youtube_multi(query, count)
        except Exception as exc:
            log.exception("search_youtube_multi failed for %r", query)
            return f"❌ 검색 실패: {str(exc)[:200]}"

        if not results:
            return "❌ 검색 결과가 없습니다."

        lines = [f"🔍 **{query}** 검색 결과 ({len(results)}곡)\n"]
        for i, r in enumerate(results, 1):
            dur = _fmt_dur(r["duration"])
            lines.append(f"`{i}.` **{r['title']}** `[{dur}]`")
        lines.append(f"\n번호로 골라주세요  예: **\"1, 3번 추가해줘\"** / **\"전부 넣어줘\"**")

        display = "\n".join(lines)

        # Store for follow-up selection
        self._search_context[message.channel.id] = {
            "results": results,
            "display": display,
        }
        return display

    # ── music info helper ─────────────────────────────────────────────────────

    async def _do_music_info(
        self,
        message: discord.Message,
        music: Music,
        subject: str,           # "song" | "artist" | "album"
        search_query: str,      # what to look up on Wikipedia / 나무위키
        detail_level: str,      # "normal" | "detailed"
    ) -> discord.Embed | str:
        """Search Wikipedia + 나무위키 in parallel, then ask Claude to summarise.

        Returns a Discord Embed.  detail_level controls how verbose Claude is.
        """
        song = music.get_current_song(message.guild)
        if not song:
            return "⚠️ 현재 재생 중인 곡이 없습니다."

        # Parallel Wikipedia + 나무위키 fetch
        loop = asyncio.get_running_loop()
        wiki_result, namu_result = await asyncio.gather(
            loop.run_in_executor(None, _wiki_fetch_sync, search_query),
            loop.run_in_executor(None, _namu_fetch_sync, search_query),
        )

        # ── build reference context and link list ─────────────────────────────
        context_parts: list[str] = []
        sources: list[str] = []
        link_parts: list[str] = [f"[YouTube]({song.webpage_url})"]

        if wiki_result:
            wiki_extract, wiki_url = wiki_result
            context_parts.append(f"[Wikipedia]\n{wiki_extract}")
            sources.append("Wikipedia")
            link_parts.append(f"[Wikipedia]({wiki_url})")
        else:
            link_parts.append(
                f"[Wikipedia 검색](https://en.wikipedia.org/wiki/Special:Search?"
                + urllib.parse.urlencode({"search": search_query}) + ")"
            )

        if namu_result:
            namu_extract, namu_url = namu_result
            context_parts.append(f"[나무위키]\n{namu_extract}")
            sources.append("나무위키")
            link_parts.append(f"[나무위키]({namu_url})")
        else:
            link_parts.append(
                f"[나무위키 검색](https://namu.wiki/go/"
                + urllib.parse.quote(search_query) + ")"
            )

        link_parts.append(
            "[Google](https://www.google.com/search?"
            + urllib.parse.urlencode({"q": search_query}) + ")"
        )

        # ── subject-specific prompt config ────────────────────────────────────
        _FOCUS = {
            "song":   "발매 배경, 음악적 특징, 가사 의미, 차트 성적, 문화적 영향력, 흥미로운 사실",
            "artist": "데뷔 배경과 역사, 음악 스타일, 대표곡, 수상 경력, 사회적 영향력",
            "album":  "앨범 발매 배경, 수록곡 구성, 음악적 방향성, 차트 성적, 의의",
        }
        _EMOJI = {"song": "🎵", "artist": "🎤", "album": "💿"}
        _LABEL = {"song": "노래", "artist": "아티스트", "album": "앨범"}

        focus       = _FOCUS.get(subject, _FOCUS["song"])
        emoji       = _EMOJI.get(subject, "🎵")
        label       = _LABEL.get(subject, "노래")
        length_inst = (
            "10문장 내외로" if detail_level != "detailed"
            else "충분히 자세하게 (문장 수 제한 없이, 섹션 나눠서 설명해도 좋아)"
        )

        if context_parts:
            refs  = "\n\n".join(context_parts)
            prompt = (
                f'{label} "{search_query}"에 대해 알려줘.\n\n'
                f"{refs}\n\n"
                f"위 자료를 참고해서 {focus} 등을 중심으로 "
                f"{length_inst} 자연스럽고 흥미롭게 한국어로 정리해줘. "
                f"원문을 직역하지 말고 자연스럽게 재구성해줘."
            )
            source_label = " + ".join(sources) + " + Claude"
        else:
            prompt = (
                f'{label} "{search_query}"에 대해 알려줘. '
                f"{focus} 등을 포함해서 "
                f"{length_inst} 자연스럽고 흥미롭게 한국어로 알려줘."
            )
            source_label = "Claude"

        # ── Claude summarisation call ─────────────────────────────────────────
        max_tok = 2000 if detail_level == "detailed" else 1200
        try:
            resp = await self._anthropic.messages.create(
                model=self._model,
                max_tokens=max_tok,
                messages=[{"role": "user", "content": prompt}],
            )
            info_text: str = resp.content[0].text
        except Exception as exc:
            log.exception("Music info Claude call failed")
            return f"❌ 정보 조회 실패: {str(exc)[:200]}"

        # ── build Embed ───────────────────────────────────────────────────────
        embed = discord.Embed(
            title=f"{emoji} {search_query}",
            description=info_text,
            color=0x1DB954,
            url=song.webpage_url,
        )
        if song.thumbnail:
            embed.set_thumbnail(url=song.thumbnail)
        embed.add_field(
            name="🔗 더보기",
            value=" · ".join(link_parts),
            inline=False,
        )
        embed.set_footer(text=f"출처: {source_label}")
        return embed

    # ── playlist helper ───────────────────────────────────────────────────────

    def _do_view_playlist(
        self,
        message: discord.Message,
        music: Music,
        target_username: str | None,
    ) -> str:
        member = message.author  # type: ignore[assignment]
        display_name, songs = music.get_playlist_for_display(
            message.guild, member, target_username
        )

        if not display_name and not songs:
            name = target_username or member.display_name
            return f"❌ **{name}**의 플레이리스트를 찾을 수 없습니다."

        if not songs:
            return f"📁 **{display_name}**의 플레이리스트가 비어있습니다."

        lines = [f"📁 **{display_name}의 플레이리스트** ({len(songs)}곡)\n"]
        for i, s in enumerate(songs, 1):
            dur = _fmt_dur(s.get("duration", 0))
            lines.append(f"`{i}.` **{s['title']}** `[{dur}]`")
        lines.append(f"\n번호로 골라주세요  예: **\"1, 3번 재생해줘\"** / **\"전부 재생해줘\"**")

        display = "\n".join(lines)
        self._playlist_context[message.channel.id] = {
            "results": songs,
            "display": display,
        }
        return display

    # ── history helper ────────────────────────────────────────────────────────

    def _do_show_history(
        self, message: discord.Message, music: Music, limit: int
    ) -> str:
        """Fetch guild history, build a numbered display, store for follow-up."""
        entries = music.get_history(message.guild, limit)
        if not entries:
            return "📋 아직 재생 기록이 없습니다."

        lines = [f"📋 **최근 재생 기록** ({len(entries)}곡)\n"]
        for i, e in enumerate(entries, 1):
            dur = _fmt_dur(e.get("duration", 0))
            by  = e.get("requested_by", "?")
            lines.append(f"`{i}.` **{e['title']}** `[{dur}]` · 🎧 {by}")

        lines.append(f"\n번호로 골라주세요  예: **\"1, 3번 다시 틀어줘\"** / **\"전부 틀어줘\"**")
        display = "\n".join(lines)

        self._history_context[message.channel.id] = {
            "results": entries,
            "display": display,
        }
        return display

    # ── tool dispatcher ───────────────────────────────────────────────────────

    async def _dispatch(
        self,
        message: discord.Message,
        tool_name: str,
        inputs: dict,
    ) -> Optional[str | discord.Embed]:
        guild = message.guild
        member = message.author  # type: ignore[assignment]
        music: Optional[Music] = self.bot.cogs.get("Music")  # type: ignore[assignment]

        if music is None:
            log.error("Music cog not loaded")
            return "❌ Music 모듈이 로드되지 않았습니다."

        match tool_name:
            case "play_song":
                query = _build_play_query(inputs)
                return await music.play_song(
                    guild, member, query, message.channel
                )

            case "get_music_info":
                return await self._do_music_info(
                    message, music,
                    subject=inputs.get("subject", "song"),
                    search_query=inputs["search_query"],
                    detail_level=inputs.get("detail_level", "normal"),
                )

            case "view_playlist":
                return self._do_view_playlist(
                    message, music, inputs.get("username") or None
                )

            case "add_to_playlist":
                # Permission: always the requesting member's own playlist
                return await music.add_to_playlist(
                    guild, member, inputs.get("query") or None
                )

            case "remove_from_playlist":
                # Permission: always the requesting member's own playlist
                return await music.remove_from_playlist(
                    guild, member, inputs["indices"]
                )

            case "select_from_playlist":
                ctx = self._playlist_context.pop(message.channel.id, None)
                if not ctx:
                    return "❌ 플레이리스트가 없습니다. 먼저 '플레이리스트 보여줘'를 입력해 주세요."
                return await music.add_from_search(
                    guild, member, ctx["results"], inputs["indices"], message.channel
                )

            case "show_history":
                return self._do_show_history(message, music, inputs.get("limit", 20))

            case "select_from_history":
                ctx = self._history_context.pop(message.channel.id, None)
                if not ctx:
                    return "❌ 재생 기록이 없습니다. 먼저 '기록 보여줘'를 입력해 주세요."
                return await music.add_from_search(
                    guild, member, ctx["results"], inputs["indices"], message.channel
                )

            case "search_songs":
                return await self._do_search(
                    message, inputs["query"], inputs.get("count", 10)
                )

            case "select_from_search":
                ctx = self._search_context.pop(message.channel.id, None)
                if not ctx:
                    return "❌ 검색 결과가 없습니다. 먼저 검색해 주세요."
                return await music.add_from_search(
                    guild, member, ctx["results"], inputs["indices"], message.channel
                )

            case "pause_playback":
                return await music.pause(guild)

            case "resume_playback":
                return await music.resume(guild)

            case "skip_song":
                return await music.skip(guild)

            case "stop_playback":
                return await music.stop(guild)

            case "view_queue":
                return music.view_queue(guild)

            case "remove_from_queue":
                return await music.remove_from_queue(guild, inputs["indices"])

            case "remove_song_by_title":
                return await music.remove_by_title(guild, inputs["title"])

            case "set_repeat":
                return await music.set_repeat(guild, inputs["mode"])

            case "set_volume":
                return await music.set_volume(guild, inputs["level"])

            case "join_voice_channel":
                _, msg = await music.join_channel(member)
                return msg

            case "leave_voice_channel":
                return await music.leave(guild)

            case _:
                log.warning("Unknown tool name: %s", tool_name)
                return f"❌ 알 수 없는 명령어: `{tool_name}`"


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(LLMListener(bot))
