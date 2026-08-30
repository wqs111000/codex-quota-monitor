FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    CHATGPT_QUOTA_DATA_DIR=/app/data

WORKDIR /app
COPY app.py ./app.py
COPY static ./static

RUN groupadd --system app && useradd --system --gid app --home-dir /app app && \
    mkdir -p /app/data && chown -R app:app /app

USER app
EXPOSE 5077

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:5077/api/status', timeout=5)"

CMD ["python", "app.py", "--host", "0.0.0.0", "--port", "5077"]
