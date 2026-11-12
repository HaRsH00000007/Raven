FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

COPY requirements.txt .
# libatomic1 is needed by Playwright's bundled Node driver on slim images.
RUN apt-get update && apt-get install -y --no-install-recommends libatomic1 \
 && pip install -r requirements.txt \
 && playwright install --with-deps chromium \
 && rm -rf /var/lib/apt/lists/*

COPY . .

RUN useradd --create-home --uid 10001 raven \
 && mkdir -p /data/screenshots \
 && chown -R raven:raven /data /app
USER raven

ENV SCREENSHOT_DIR=/data/screenshots
EXPOSE 8000

# The same image runs the API (default) and the worker (see docker-compose.yml).
CMD ["sh", "-c", "alembic upgrade head && uvicorn app.main:app --host 0.0.0.0 --port 8000 --proxy-headers"]
