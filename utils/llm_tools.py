"""
Claude Tool Use definitions for music bot commands.
These are sent to the Anthropic API so Claude can interpret
natural language and call the right music function.
"""

MUSIC_TOOLS: list[dict] = [
    {
        "name": "play_song",
        "description": (
            "Search YouTube for a specific song and add it to the queue. "
            "Use when the user wants to play one or more songs. "
            "YOU decide which actual song(s) to play — provide the real artist name and song title, "
            "never pass mood descriptions or vague phrases as the title. "
            "For multiple songs, call this tool multiple times (once per song). "
            "Examples: title='Dynamite' artist='BTS', title='Bohemian Rhapsody' artist='Queen'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "The actual song title. E.g. 'Dynamite', 'Karma Police', 'LOVE DIVE'.",
                },
                "artist": {
                    "type": "string",
                    "description": "The artist or group name. E.g. 'BTS', 'Radiohead', 'IVE'. Optional but recommended.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "pause_playback",
        "description": "Pause the currently playing song. Use for '멈춰', '일시정지', 'pause'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "resume_playback",
        "description": "Resume a paused song. Use for '다시 재생', '계속', 'resume', 'unpause'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "skip_song",
        "description": "Skip the current song and play the next one. Use for '다음', '넘겨', 'skip', 'next'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "stop_playback",
        "description": "Stop music entirely and clear the queue. Use for '정지', '그만', 'stop', 'clear queue'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "view_queue",
        "description": "Show the current song queue. Use for '큐 보여줘', '대기열', 'show queue', 'list songs'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "remove_from_queue",
        "description": (
            "Remove one or more songs from the queue by their 1-based position numbers. "
            "Use for '3번 빼줘', 'remove song 2', '2, 4, 7번 지워줘', '1이랑 5번 삭제해줘'. "
            "Always use the position numbers shown in the queue list."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "description": "List of 1-based position numbers to remove. E.g. [2, 3, 4, 15] or [1].",
                    "minItems": 1,
                },
            },
            "required": ["indices"],
        },
    },
    {
        "name": "remove_song_by_title",
        "description": (
            "Remove a song from the queue by searching its title (partial, case-insensitive match). "
            "Use when the user refers to a song by name rather than position number. "
            "E.g. '큐에서 Bohemian Rhapsody 빼줘', '다이너마이트 지워줘', 'remove the BTS song'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": "Song title or partial title to search for in the queue.",
                },
            },
            "required": ["title"],
        },
    },
    {
        "name": "set_repeat",
        "description": (
            "Set the repeat/loop mode. "
            "Use for '반복', '루프', 'loop', 'repeat'. "
            "mode: off=no repeat, single=repeat current song, queue=repeat entire queue."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["off", "single", "queue"],
                    "description": "Repeat mode: off / single / queue",
                },
            },
            "required": ["mode"],
        },
    },
    {
        "name": "set_volume",
        "description": "Set the playback volume (0–100). Use for '볼륨 50', 'volume 80', 'louder/quieter'.",
        "input_schema": {
            "type": "object",
            "properties": {
                "level": {
                    "type": "integer",
                    "description": "Volume level 0–100.",
                    "minimum": 0,
                    "maximum": 100,
                },
            },
            "required": ["level"],
        },
    },
    {
        "name": "show_history",
        "description": (
            "Show the guild's recent playback history (up to 20 songs). "
            "Use when the user asks '최근 재생 기록', '이전에 뭐 들었어', 'show history', '기록 보여줘'. "
            "After showing results, the user can pick by number with select_from_history."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "limit": {
                    "type": "integer",
                    "description": "How many recent songs to show (1–20). Default 20.",
                    "minimum": 1,
                    "maximum": 20,
                },
            },
            "required": [],
        },
    },
    {
        "name": "select_from_history",
        "description": (
            "Add songs from the PREVIOUS history list to the queue. "
            "Use ONLY after show_history when the user picks by number. "
            "E.g. '1번 다시 틀어줘', '2랑 4번 재생해줘', '전부 다 틀어줘'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "description": "1-based indices from the history list to add to queue.",
                    "minItems": 1,
                },
            },
            "required": ["indices"],
        },
    },
    {
        "name": "search_songs",
        "description": (
            "Search YouTube for songs and show a numbered list of results (up to 10). "
            "Use when the user wants to browse results before choosing. "
            "E.g. 'BTS 검색해줘', '아이유 노래 찾아줘', 'search for Coldplay songs'. "
            "After showing results, the user picks by number using select_from_search."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Search query (artist, song title, or keywords).",
                },
                "count": {
                    "type": "integer",
                    "description": "Number of results to show (1–10). Default 10.",
                    "minimum": 1,
                    "maximum": 10,
                },
            },
            "required": ["query"],
        },
    },
    {
        "name": "select_from_search",
        "description": (
            "Add songs from the PREVIOUS search results to the queue. "
            "Use ONLY after search_songs has been called and the user picks by number. "
            "E.g. '1번 추가해줘', '2랑 5번 넣어줘', '전부 다 넣어줘' (pass all indices shown). "
            "Do NOT use this if there were no prior search results."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "description": "1-based result numbers to add. E.g. [1, 3, 5] or [1,2,3,4,5,6,7,8,9,10] for all.",
                    "minItems": 1,
                },
            },
            "required": ["indices"],
        },
    },
    {
        "name": "get_music_info",
        "description": (
            "Look up information about the currently playing song, its artist, or its album. "
            "The user message always includes '[현재 재생 중: title]' — use that to fill in search_query.\n"
            "Examples:\n"
            "  '곡 정보 알려줘'  → subject=song,   search_query=song title\n"
            "  '가수 정보 알려줘' → subject=artist, search_query=artist/group name\n"
            "  '앨범 알려줘'     → subject=album,  search_query='album_name artist'\n"
            "  '더 자세히 알려줘' → same subject, detail_level=detailed\n"
            "  '아티스트 정보 자세히' → subject=artist, detail_level=detailed"
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "subject": {
                    "type": "string",
                    "enum": ["song", "artist", "album"],
                    "description": "song = the track itself, artist = performer/group, album = the album it's from.",
                },
                "search_query": {
                    "type": "string",
                    "description": (
                        "What to search Wikipedia/나무위키 for. "
                        "song → song title (e.g. 'BTS Dynamite'). "
                        "artist → group/artist name (e.g. 'BTS'). "
                        "album → 'album_name artist' (e.g. 'BE BTS album'). "
                        "Infer from [현재 재생 중: ...] in the user message."
                    ),
                },
                "detail_level": {
                    "type": "string",
                    "enum": ["normal", "detailed"],
                    "description": (
                        "normal = ~10 sentences. "
                        "detailed = comprehensive, no sentence limit. "
                        "Use 'detailed' when user says '더 자세히', '자세하게', 'in depth', 'more detail'."
                    ),
                },
            },
            "required": ["subject", "search_query"],
        },
    },
    # ── playlist tools ────────────────────────────────────────────────────────
    {
        "name": "view_playlist",
        "description": (
            "Show a user's personal playlist. Anyone can view any playlist. "
            "Use for '내 플레이리스트 보여줘', '내 플리', 'jinwook의 플리 보여줘', 'show my playlist'. "
            "After showing, user can pick songs with select_from_playlist."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "username": {
                    "type": "string",
                    "description": (
                        "Target user's display name. "
                        "Omit (or leave blank) to show the requesting user's own playlist."
                    ),
                },
            },
            "required": [],
        },
    },
    {
        "name": "add_to_playlist",
        "description": (
            "Add a song to the requesting user's OWN playlist ONLY. "
            "Cannot add to another user's playlist. "
            "If no query given, adds the currently playing song. "
            "E.g. '지금 곡 내 플리에 추가해줘', 'BTS Dynamite 플레이리스트에 넣어줘'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Song to search and add. Omit to add the currently playing song.",
                },
            },
            "required": [],
        },
    },
    {
        "name": "remove_from_playlist",
        "description": (
            "Remove songs from the requesting user's OWN playlist ONLY by position number. "
            "Cannot edit another user's playlist. "
            "E.g. '내 플리에서 3번 빼줘', '1이랑 5번 삭제해줘'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "description": "1-based positions to remove from own playlist.",
                    "minItems": 1,
                },
            },
            "required": ["indices"],
        },
    },
    {
        "name": "select_from_playlist",
        "description": (
            "Add songs from the PREVIOUSLY shown playlist to the queue. "
            "Anyone can play from any playlist. "
            "Use ONLY after view_playlist when user picks by number. "
            "E.g. '1번 재생해줘', '2랑 5번 틀어줘', '전부 재생해줘'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 1},
                    "description": "1-based indices from the playlist to add to queue.",
                    "minItems": 1,
                },
            },
            "required": ["indices"],
        },
    },
    {
        "name": "join_voice_channel",
        "description": "Make the bot join the user's current voice channel. Use for '들어와', 'join', 'connect'.",
        "input_schema": {"type": "object", "properties": {}},
    },
    {
        "name": "leave_voice_channel",
        "description": "Make the bot leave the voice channel. Use for '나가', 'leave', 'disconnect', 'bye'.",
        "input_schema": {"type": "object", "properties": {}},
    },
]
