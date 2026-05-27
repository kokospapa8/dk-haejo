FROM python:3.11-slim

# Install FFmpeg and build dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    libopus0 \
    libffi-dev \
    libnacl-dev \
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
