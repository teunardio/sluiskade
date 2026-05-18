FROM python:3.12-slim

# System dependencies:
#   libjpeg-dev / zlib1g-dev / libwebp-dev  -> Pillow image codecs
#   libheif-dev                              -> pillow-heif (iPhone HEIC photos)
#   libmagic1                                -> python-magic (magic-byte detection)
#   curl                                     -> healthcheck
RUN apt-get update && apt-get install -y --no-install-recommends \
    libjpeg-dev \
    zlib1g-dev \
    libwebp-dev \
    libheif-dev \
    libmagic1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps first so they're cached when only app code changes
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the app
COPY . .

# Persistent data dir is mounted via Coolify volume; create fallback for dev
RUN mkdir -p /data/photos /data/thumbs

ENV PYTHONUNBUFFERED=1
ENV FLASK_ENV=production
ENV DATA_PATH=/data

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:5000/healthz || exit 1

CMD ["python", "-m", "gunicorn", \
     "--workers", "2", \
     "--bind", "0.0.0.0:5000", \
     "--timeout", "120", \
     "--access-logfile", "-", \
     "app:app"]
