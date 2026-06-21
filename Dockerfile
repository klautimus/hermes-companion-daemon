FROM python:3.11-slim

LABEL maintainer="Hermes Community"
LABEL description="Hermes Companion Daemon — HTTP shim for Hermes API + Kanban CLI"

# Install system dependencies
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Create non-root user
RUN groupadd -r companion && useradd -r -g companion -d /app -s /bin/bash companion

# Set up app directory
WORKDIR /app

# Copy project files
COPY pyproject.toml setup.py ./
COPY server.py config.py config_schema.py first_run.py setup_wizard.py email_2fa.py companion_cli.py ./
COPY src/ ./src/

# Install the package
RUN pip install --no-cache-dir -e .

# Create data directory
RUN mkdir -p /data && chown -p companion:companion /data

# Expose port
EXPOSE 8777

# Override host for Docker (must bind to 0.0.0.0 not 127.0.0.1)
ENV COMPANION_HOST=0.0.0.0

# Health check
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD curl -f http://localhost:8777/healthz || exit 1

# Volume for persistent data
VOLUME ["/data"]

# Switch to non-root
USER companion

# Run
CMD ["hermes-companion", "serve"]
