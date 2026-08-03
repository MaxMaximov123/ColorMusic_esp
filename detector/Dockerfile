FROM python:3.11-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg libgl1 libglib2.0-0 && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --system \
    --index-strategy unsafe-best-match \
    --extra-index-url https://download.pytorch.org/whl/cpu \
    fastapi==0.115.6 \
    "uvicorn[standard]==0.34.0" \
    websocket-client==1.8.0 \
    requests==2.32.3 \
    opencv-python-headless==4.10.0.84 \
    "numpy>=1.26,<2" \
    "ultralytics>=8.3.0" \
    "torch>=2.1.0" \
    "torchvision>=0.16.0"

COPY main.py .

RUN mkdir -p /models

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
