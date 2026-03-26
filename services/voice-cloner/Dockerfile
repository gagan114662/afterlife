FROM python:3.11-slim

WORKDIR /app

# ffmpeg for audio processing (ogg→wav conversion, quality analysis)
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    && rm -rf /var/lib/apt/lists/*

COPY services/voice-cloner/requirements.txt /app/services/voice-cloner/requirements.txt
RUN pip install --no-cache-dir -r /app/services/voice-cloner/requirements.txt

COPY services/voice-cloner/ /app/services/voice-cloner/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Worker — invoked on demand:
#   docker compose run voice-cloner python -m services.voice-cloner.elevenlabs_cloner
CMD ["python", "-c", "import time; print('voice-cloner worker ready'); time.sleep(1e9)"]
