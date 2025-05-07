# ────────────────────────────────
# 1. Etapa de build de dependencias
# ────────────────────────────────
FROM python:3.12-slim AS builder

# Instalamos compiladores mínimos por si alguna wheel necesita "build"
RUN apt-get update && \
    apt-get install -y --no-install-recommends build-essential curl gnupg && \
    rm -rf /var/lib/apt/lists/*

# Copiamos los archivos de dependencias primero (capas de cache)
WORKDIR /app
COPY pyproject.toml uv.lock* ./

# Creamos un venv aislado que luego pasaremos a la imagen final
ENV VENV_PATH=/venv
RUN python -m venv $VENV_PATH

# Instalamos uv y sincronizamos dependencias dentro del venv
RUN pip install --upgrade pip && \
    pip install uv && \
    uv sync --frozen --python $VENV_PATH/bin/python

# ────────────────────────────────
# 2. Imagen final de runtime
# ────────────────────────────────
FROM python:3.12-slim

# — Añadimos Node.js + npm para que funcione npx (@modelcontextprotocol usa npx)
RUN apt-get update && \
    apt-get install -y --no-install-recommends nodejs npm && \
    npm install -g npm@latest && \
    rm -rf /var/lib/apt/lists/*

# Copiamos el venv ya poblado desde la etapa builder
ENV VENV_PATH=/venv
COPY --from=builder $VENV_PATH $VENV_PATH

# Aseguramos que el venv sea el intérprete por defecto
ENV PATH="$VENV_PATH/bin:$PATH"
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=off

# Carpeta de la app y resto del código
WORKDIR /app
COPY . .

# Exponemos puerto (FastAPI)
EXPOSE 8000

# Variables de entorno que tu app espera DEBE ponerlas Railway o tu .env, ej.:
# ENV STRIPE_SECRET_KEY=sk_live_xxx
# ENV GOOGLE_MAPS_API_KEY=AIza...

# Comando de arranque
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"] 