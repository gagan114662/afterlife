FROM python:3.11-slim

WORKDIR /app

COPY services/personality/requirements.txt /app/services/personality/requirements.txt
RUN pip install --no-cache-dir -r /app/services/personality/requirements.txt

COPY services/personality/ /app/services/personality/

ENV PYTHONPATH=/app
ENV PYTHONUNBUFFERED=1

# Worker — invoked on demand:
#   docker compose run personality python -m services.personality.extractor
CMD ["python", "-c", "import time; print('personality worker ready'); time.sleep(1e9)"]
