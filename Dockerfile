# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS builder
WORKDIR /build
COPY requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim
WORKDIR /app
COPY --from=builder /install /usr/local
COPY server.py .
COPY web/ ./web/
COPY panel_library.json .

# Cloud Run sets PORT; honour it
ENV PORT=8080
EXPOSE 8080

# Gunicorn with Uvicorn workers
CMD exec gunicorn -k uvicorn.workers.UvicornWorker \
    -b 0.0.0.0:${PORT} \
    -w 2 --timeout 120 \
    server:app
