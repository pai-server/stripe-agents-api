# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables for Python
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
# Aunque usamos uv, no hace daño
ENV PIP_NO_CACHE_DIR=off

# Install system dependencies including Node.js (for npx) and curl (for uv install)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg ca-certificates nodejs && \
    # Clean up apt cache
    rm -rf /var/lib/apt/lists/*

# Install uv (Python package installer/resolver)
# Esto lo instala en /root/.local/bin/uv, luego lo movemos a /usr/local/bin
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv && \
    # Limpiamos curl y gnupg DESPUÉS de usarlos para instalar uv
    apt-get purge -y --auto-remove curl gnupg && apt-get autoremove -y

# Define path for the virtual environment
ENV VENV_PATH=/opt/venv

# Create the virtual environment
RUN python3 -m venv $VENV_PATH

# Set the working directory in the container
WORKDIR /app

# Copy dependency definition files
COPY pyproject.toml uv.lock ./

# Install Python dependencies into the virtual environment using uv
# Usamos el uv global para instalar en el venv especificado por --python
RUN echo "Installing dependencies into venv: $VENV_PATH" && \
    /usr/local/bin/uv sync --frozen --python $VENV_PATH/bin/python && \
    echo "Finished installing dependencies."

# Copy the rest of the application code
COPY main.py ./
# Si tienes otros módulos locales, cópialos también:
# COPY ./agents_module ./agents_module/

# Expose the port the app runs on
EXPOSE 8000

# Define the command to run the application using python from the venv
# Asegúrate de que STRIPE_SECRET_KEY y GOOGLE_MAPS_API_KEY estén configuradas en Railway.
CMD ["/opt/venv/bin/python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"] 