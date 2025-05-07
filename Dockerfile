# -------- 1️⃣  Imagen base con Python 3.12 ----------
    FROM python:3.12-slim

    # -------- 2️⃣  Ajustes de entorno ----------
    ENV PYTHONDONTWRITEBYTECODE=1 \
        PYTHONUNBUFFERED=1 \
        # Railway siempre publica el puerto en $PORT
        PORT=8000
    
    # -------- 3️⃣  Dependencias del sistema ----------
    #  - build-essential → compilar libs de Python (cryptography, psycopg2, etc.)
    #  - curl + gnupg    → agregar repo de NodeSource
    RUN apt-get update && \
        apt-get install -y --no-install-recommends build-essential curl gnupg && \
        curl -fsSL https://deb.nodesource.com/setup_20.x | bash - && \
        apt-get install -y --no-install-recommends nodejs && \
        apt-get clean && rm -rf /var/lib/apt/lists/*
    
    # -------- 4️⃣  Instalar dependencias Python ----------
    WORKDIR /app
    COPY requirements*.txt pyproject.toml* poetry.lock* ./
    RUN pip install --upgrade pip && \
        if [ -f requirements.txt ]; then pip install --no-cache-dir -r requirements.txt ; fi && \
        if [ -f pyproject.toml ];   then pip install --no-cache-dir . ; fi
    
    # -------- 5️⃣  Copiar el código ----------
    COPY . .
    
    # -------- 6️⃣  Exponer el puerto ----------
    EXPOSE ${PORT}
    
    # -------- 7️⃣  Comando de arranque ----------
    # Usa PORT si Railway lo define (ej. 39787) o 8000 por defecto
    CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port ${PORT:-8000}"]
    