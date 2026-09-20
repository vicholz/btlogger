FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
        libglib2.0-0 libdbus-1-3 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY scanner/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY core /app/core
COPY scanner /app/scanner
COPY data /app/data

ENV PYTHONPATH=/app
ENV DATA_DIR=/app/data
ENV PYTHONUNBUFFERED=1

CMD ["python", "-m", "scanner"]
