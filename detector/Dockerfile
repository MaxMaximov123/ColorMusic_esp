FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN pip install --no-cache-dir \
    fastapi==0.115.6 \
    "uvicorn[standard]==0.34.0" \
    websocket-client==1.8.0 \
    requests==2.32.3

COPY main.py .

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
