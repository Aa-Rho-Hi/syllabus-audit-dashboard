# Cloud Run image for Syllabus Audit
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PORT=8080

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static
COPY sample_syllabi ./sample_syllabi

# One worker: background audit jobs live in this process's memory.
CMD exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT} --workers 1
