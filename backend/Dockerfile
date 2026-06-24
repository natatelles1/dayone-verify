FROM python:3.12-slim

WORKDIR /app

RUN pip install --no-cache-dir hatchling

# Copia pyproject.toml e cria stub para cachear instalação das deps em layer separado
COPY pyproject.toml .
RUN mkdir -p app && touch app/__init__.py
RUN pip install --no-cache-dir .

# Sobrescreve stub com código real
COPY . .

EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
