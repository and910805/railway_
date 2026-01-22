FROM python:3.11-slim

# 讓 log 直接輸出到 stdout、避免產生 .pyc
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

# tzdata：ZoneInfo/時區資料；ca-certificates：requests HTTPS
RUN apt-get update \
    && apt-get install -y --no-install-recommends tzdata ca-certificates \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# SQLite DB 放 /data，配合 volume 持久化
RUN mkdir -p /data

# 先裝依賴（利用 docker layer cache）
COPY requirements.txt .
RUN python -m pip install --upgrade pip \
    && pip install -r requirements.txt

# 再 copy 程式碼
COPY . .

# 非 root 執行（較安全）
RUN addgroup --system appgroup \
    && adduser  --system --ingroup appgroup appuser \
    && chown -R appuser:appgroup /app /data
USER appuser

EXPOSE 8080

# 注意：
# - 若 ENABLE_SCHEDULER=1，請務必 workers=1，否則排程會被多 worker 重複跑，造成重複推播
CMD ["sh", "-c", "gunicorn app:app -b 0.0.0.0:${PORT:-8080} -w ${GUNICORN_WORKERS:-1} --threads ${GUNICORN_THREADS:-2} --timeout ${GUNICORN_TIMEOUT:-60}"]
