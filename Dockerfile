FROM python:3.11-slim

WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 關鍵：建立 /data 資料夾
RUN mkdir -p /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# 修改啟動指令，讓 Port 跟著 Zeabur 的環境變數走
CMD ["sh", "-c", "gunicorn app:app -b 0.0.0.0:${PORT:-5000} -w 1 --threads 2"]