# herness — образ приложения.
# Всё необходимое ставится внутрь образа: на сервере не нужно ничего,
# кроме Docker и docker compose.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    TZ=Europe/Moscow

WORKDIR /app

# Системные зависимости: LibreOffice нужен для чтения .doc (старый формат),
# шрифты — чтобы документы отчётов корректно формировались.
RUN apt-get update && apt-get install -y --no-install-recommends \
        libreoffice-writer \
        libreoffice-calc \
        fonts-dejavu-core \
        fonts-liberation \
        curl \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY docs ./docs
COPY scripts ./scripts
COPY tests ./tests

# Каталог данных монтируется томом: база, загруженные документы, отчёты.
RUN mkdir -p /data/db /data/uploads /data/reports /data/ntd

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8080/health || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8080"]
