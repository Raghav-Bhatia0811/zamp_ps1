FROM python:3.12-slim

# poppler (pdftotext/pdftoppm) and tesseract (OCR + orientation detection) power the offline reader
RUN apt-get update && apt-get install -y --no-install-recommends poppler-utils tesseract-ocr libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
# bake the embedding model into the image so retrieval works offline
RUN python -c "from fastembed import TextEmbedding; TextEmbedding('BAAI/bge-small-en-v1.5', cache_dir='/app/models/embed')"
COPY app ./app
COPY static ./static
COPY knowledge ./knowledge
COPY models/layout-yolo.onnx models/layout-yolo.json ./models/
COPY data/master.json ./data/master.json
COPY samples ./samples
COPY scripts ./scripts

ENV PORT=8000 \
    STATE_DIR=/state \
    PYTHONUNBUFFERED=1
EXPOSE 8000
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT}"]
