# ledger backend (FastAPI + Telegram bot + scheduler). Build from the REPO ROOT:
#   docker build -f deploy/backend.Dockerfile -t ledger-backend:latest .
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app
COPY backend/requirements.txt /app/backend/requirements.txt
RUN pip install --no-cache-dir -r /app/backend/requirements.txt

COPY backend/ /app/backend/

WORKDIR /app/backend
EXPOSE 8070
CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8070"]
