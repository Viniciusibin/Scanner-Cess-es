FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend backend
COPY frontend frontend
COPY database database
COPY diario diario
COPY filtro_cessoes.py filtro_cessoes.py
COPY pdpj_capa.py pdpj_capa.py
COPY pdpj_valor_causa.py pdpj_valor_causa.py

ENV SCANNER_DATABASE_DIR=/app/database
EXPOSE 5000

WORKDIR /app/backend
CMD ["sh", "-c", "gunicorn run:app --bind 0.0.0.0:${PORT:-5000}"]
