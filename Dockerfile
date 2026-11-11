# One Railway service: Telegram Local Bot API + owner-only Python bot.
# Both processes use the same filesystem, so getFile's absolute paths work.
FROM aiogram/telegram-bot-api:latest

USER root
RUN apk add --no-cache python3 py3-pip \
    && python3 -m venv /opt/venv

WORKDIR /app
COPY requirements.txt /app/requirements.txt
RUN /opt/venv/bin/pip install --no-cache-dir -r /app/requirements.txt
COPY bot.py /app/bot.py
COPY start.sh /app/start.sh
RUN chmod 755 /app/start.sh && mkdir -p /data

ENV PYTHONUNBUFFERED=1 \
    TELEGRAM_LOCAL=1 \
    TELEGRAM_HTTP_IP_ADDRESS=127.0.0.1 \
    TELEGRAM_HTTP_PORT=8081 \
    LOCAL_BOT_API_URL=http://127.0.0.1:8081

# Do not publish port 8081: local API is for this container only.
ENTRYPOINT ["/bin/sh", "/app/start.sh"]
