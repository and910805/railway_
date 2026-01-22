FROM python:3.11-slim

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY . /app

# Zeabur uses PORT / WEB_PORT mapping; your env sets PORT=${WEB_PORT}
CMD ["sh", "-c", "gunicorn -w ${GUNICORN_WORKERS:-1} --threads ${GUNICORN_THREADS:-2} --timeout ${GUNICORN_TIMEOUT:-60} -b 0.0.0.0:${PORT:-8080} app:app"]
