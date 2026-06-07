FROM python:3.11-slim

WORKDIR /app

# Instalace všech potřebných závislostí pro FastAPI a formuláře
RUN pip install --no-cache-dir fastapi uvicorn jinja2 python-multipart

# Zkopírování zdrojového kódu a šablon
COPY app.py .
COPY templates/ ./templates/

EXPOSE 8000

CMD ["python", "app.py"]