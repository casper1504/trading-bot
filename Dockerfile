# Railway / Render long-running background bot (stdlib-only, no deps)
FROM python:3.12-slim

WORKDIR /app

# No pip dependencies — the bot is stdlib only. requirements.txt exists for
# platforms that expect it, but installs nothing.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt || true

COPY *.py ./

# Paper mode needs no API keys; config comes from env vars or defaults.
# Do NOT copy .env or local state/db into the image (see .dockerignore).
CMD ["python", "-u", "bot.py"]
