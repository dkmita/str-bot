FROM python:3.13-slim

RUN apt-get update && apt-get install -y --no-install-recommends git openssh-client \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY bot/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY bot/ ./

# The bot's notes (property.md etc.) live in a git clone (made at startup) so the bot can commit and push.
ENV REPO_DIR=/data/str-bot GIT_PUSH=true PYTHONUNBUFFERED=1
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/entrypoint.sh"]
CMD ["python", "main.py"]
