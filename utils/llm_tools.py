"""
Claude Tool Use definitions for music bot commands.
These are sent to the Anthropic API so Claude can interpret
natural language and call the right music function.
"""

MUSIC_TOOLS: list[dict] = [
    {
        "name": "play_song",
        "description": (
            "Search YouTube and play a song, or add it to the queue. "
            "Use when the user wants to play, listen to, search for, or add a song/artist. "
            "Examples: '다이너마이트 틀어줘', 'play BTS', 'add Bohemian Rhapsody to queue'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "Song title, artist name, or YouTube URL to search and play.",
                },
            },
            "required": ["query"],
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
            "Remove a specific song from the queue by its 1-based position number. "
            "Use for '3번 곡 빼줘', 'remove song 2', 'delete #1 from queue'."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "index": {
                    "type": "integer",
                    "description": "1-based position number of the song to remove.",
                    "minimum": 1,
                },
            },
            "required": ["index"],
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
