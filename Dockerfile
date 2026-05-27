FROM python:3.11-slim

# System deps + Deno (JS runtime for yt-dlp signature/n-challenge solving)
# -----------------------------------------------------------------------
# nodejs apt package installs `nodejs` binary, NOT `node` — yt-dlp looks
# for `node`, so signature solving silently fails with apt nodejs.
# Deno is a single binary, installs as `deno`, and yt-dlp recognises it.
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    libffi-dev \
    libnacl-dev \
    curl \
    unzip \
    && curl -fsSL https://deno.land/install.sh | DENO_INSTALL=/usr/local sh \
    && deno --version \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Pre-cache EJS challenge solver scripts from GitHub so the bot doesn't
# need to hit GitHub on first playback at runtime.
# Uses Rick Astley as a harmless probe video; `|| true` so a transient
# network error during build doesn't break the image.
RUN yt-dlp --remote-components ejs:github \
    --skip-download --print id \
    "https://www.youtube.com/watch?v=dQw4w9WgXcQ" || true

COPY . .

CMD ["python", "-u", "bot.py"]
