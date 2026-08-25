FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    AUTO_REGISTER_HOST=0.0.0.0 \
    AUTO_REGISTER_PORT=8091

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY protocol_register.py server.py ./
COPY engine ./engine
COPY elevenlabs_assisted ./elevenlabs_assisted

EXPOSE 8091
CMD ["python", "server.py"]
