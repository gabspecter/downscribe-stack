FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=300

RUN pip install --no-cache-dir --retries 10 \
    fastapi \
    uvicorn[standard] \
    yt-dlp \
    requests \
    redis \
    "curl_cffi<0.14"

WORKDIR /app
COPY main.py /app/main.py

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
