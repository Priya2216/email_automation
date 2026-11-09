# Email automation system: agents + MCP server + mock core banking API
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
# tesseract-ocr enables OCR of image attachments inside the container
RUN apt-get update && apt-get install -y --no-install-recommends tesseract-ocr curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY . .

EXPOSE 8000 8001
# default: REST API of the orchestrator (it auto-starts the mock core API)
CMD ["uvicorn", "apis.orchestrator_api:app", "--host", "0.0.0.0", "--port", "8000"]
