FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    ELEVENLABS_GATEWAY_HOST=0.0.0.0 \
    ELEVENLABS_GATEWAY_PORT=8092 \
    ELEVENLABS_MEDIA_DIR=/app/data

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY client.py runtime_config.py server.py ./

EXPOSE 8092
CMD ["python", "server.py"]
