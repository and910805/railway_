FROM python:3.11-slim

# 設定工作目錄
WORKDIR /app

# 安裝系統依賴與 Google Chrome 瀏覽器
RUN apt-get update && apt-get install -y \
    wget gnupg procps libnss3 libnspr4 libatk1.0-0 \
    libatk-bridge2.0-0 libcups2 libdrm2 libxkbcommon0 \
    libxcomposite1 libxdamage1 libxext6 libxfixes3 \
    libxrandr2 libgbm1 libasound2 \
    && wget -q -O - https://dl-ssl.google.com/linux/linux_signing_key.pub | apt-key add - \
    && echo "deb [arch=amd64] http://dl.google.com/linux/chrome/deb/ stable main" >> /etc/apt/sources.list.d/google.list \
    && apt-get update && apt-get install -y google-chrome-stable \
    && rm -rf /var/lib/apt/lists/*

# 建立資料庫資料夾
RUN mkdir -p /data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Zeabur 啟動指令
CMD ["sh", "-c", "gunicorn app:app -b 0.0.0.0:${PORT:-8080} -w 1 --threads 2"]