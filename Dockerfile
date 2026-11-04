FROM python:3.12-slim

# poppler (pdftotext/pdftoppm) and tesseract power the offline OCR fallback
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY app ./app
COPY static ./static
COPY data/master.json ./data/master.json
COPY samples ./samples
COPY scripts ./scripts

ENV PORT=8000 \
    STATE_DIR=/state \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
