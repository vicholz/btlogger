FROM python:3.12-slim

WORKDIR /app
COPY api/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY core /app/core
COPY api /app/api
COPY data /app/data

ENV PYTHONPATH=/app
ENV DATA_DIR=/app/data
ENV PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8080"]
