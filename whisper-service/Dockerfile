FROM python:3.11-slim

RUN apt-get update && apt-get install -y \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

ENV PIP_DISABLE_PIP_VERSION_CHECK=1
ENV PIP_DEFAULT_TIMEOUT=600

RUN pip install --no-cache-dir --retries 30 --timeout 600 --extra-index-url https://download.pytorch.org/whl/cpu \
    torch==2.2.2+cpu

RUN pip install --no-cache-dir --retries 10 --timeout 600 \
    "numpy<2" \
    fastapi \
    uvicorn[standard] \
    python-multipart \
    openai-whisper

WORKDIR /app
COPY main.py /app/main.py

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
