FROM python:3.11-slim

# Install FFmpeg, build dependencies, and Node.js (required by yt-dlp for
# JavaScript evaluation — decrypts YouTube's n-function / player configs.
# Without it, some videos return "Sign in to confirm you're not a bot".)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    libffi-dev \
    libnacl-dev \
    nodejs \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies first (layer cache)
COPY requirements.txt .
# yt-dlp is pinned to a specific version in requirements.txt
# do NOT add --upgrade here; it would override the pinned version
RUN pip install --no-cache-dir -r requirements.txt

# Copy project source
COPY . .

CMD ["python", "-u", "bot.py"]
