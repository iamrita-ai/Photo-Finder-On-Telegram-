FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
# OPTIONAL: only needed if you want the owner-only /login command to work
# (real Pinterest email/password login via headless Chrome + Selenium).
# Uncomment this block if you plan to use /login. It adds real size/RAM
# usage to the image, so it's off by default since search works without it.
# ---------------------------------------------------------------------------
# RUN apt-get update && apt-get install -y --no-install-recommends \
#         chromium chromium-driver \
#     && rm -rf /var/lib/apt/lists/*
# ENV CHROME_BIN=/usr/bin/chromium \
#     CHROMEDRIVER_PATH=/usr/bin/chromedriver

COPY . .

# Render sets $PORT at runtime; this is just documentation for local runs.
EXPOSE 8000

CMD ["python", "bot.py"]
