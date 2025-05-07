# Use an official Python runtime as a parent image
FROM python:3.12-slim

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE 1
ENV PYTHONUNBUFFERED 1
ENV PIP_NO_CACHE_DIR=off
# Pin uv version for reproducibility
ENV UV_VERSION=0.1.40

# Add .local/bin to PATH for executables installed by uv/pip
ENV PATH="/root/.local/bin:${PATH}"

# Install system dependencies including Node.js and npm
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl gnupg ca-certificates && \
    # Add NodeSource repository for Node.js
    mkdir -p /etc/apt/keyrings && \
    curl -fsSL https://deb.nodesource.com/gpgkey/nodesource-repo.gpg.key | gpg --dearmor -o /etc/apt/keyrings/nodesource.gpg && \
    NODE_MAJOR=20 && \
    echo "deb [signed-by=/etc/apt/keyrings/nodesource.gpg] https://deb.nodesource.com/node_$NODE_MAJOR.x nodistro main" | tee /etc/apt/sources.list.d/nodesource.list && \
    apt-get update && \
    apt-get install -y nodejs && \
    # Clean up
    rm -rf /var/lib/apt/lists/*

# Install uv (Python package installer/resolver)
RUN curl -LsSf https://astral.sh/uv/install.sh | sh && \
    mv /root/.local/bin/uv /usr/local/bin/uv && \
    # Now clean up curl and gnupg
    apt-get purge -y --auto-remove curl gnupg && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Set the working directory in the container
WORKDIR /app

# Copy the dependency files
COPY pyproject.toml uv.lock ./

# Install Python dependencies using uv
# Ensure uv.lock is up-to-date with pyproject.toml by running `uv lock` locally first.
RUN uv sync --frozen

# Copy the rest of the application code
COPY main.py ./
# If you have other local modules/packages (e.g., an 'agents_module' directory if it's not a pip package), copy them too:
# COPY agents_module/ ./agents_module/

# Expose the port the app runs on
EXPOSE 8000

# Define the command to run the application.
# Ensure STRIPE_SECRET_KEY and GOOGLE_MAPS_API_KEY are set as environment variables when running the container.
CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"] 