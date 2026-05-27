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
# --upgrade ensures yt-dlp is always the newest release at build time
# (YouTube bot-detection patches ship frequently)
RUN pip install --no-cache-dir --upgrade -r requirements.txt

# Copy project source
COPY . .

CMD ["python", "-u", "bot.py"]
