# Multi-stage build for Context Engine Service
FROM --platform=linux/amd64 python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea as builder

# Harden APT against "Hash Sum mismatch" from proxies/pipelining
RUN printf 'Acquire::http::Pipeline-Depth "0";\nAcquire::http::No-Cache "true";\nAcquire::BrokenProxy "true";\nAcquire::Retries "3";\n' > /etc/apt/apt.conf.d/99fixbadproxy

# Install system dependencies
RUN apt-get update -o Acquire::Retries=5 && \
    apt-get install -y --fix-missing -o Acquire::Retries=5 \
    gcc \
    g++ \
    git \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu && \
    pip install --no-cache-dir -r requirements.txt && \
    # Remove unnecessary files to reduce image size
    find /opt/venv -type d -name "tests" -exec rm -rf {} + 2>/dev/null || true && \
    find /opt/venv -type d -name "__pycache__" -exec rm -rf {} + 2>/dev/null || true && \
    find /opt/venv -name "*.pyc" -delete && \
    find /opt/venv -name "*.pyo" -delete

# Production stage
FROM --platform=linux/amd64 python:3.12-slim@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea

# Harden APT against "Hash Sum mismatch" from proxies/pipelining
RUN printf 'Acquire::http::Pipeline-Depth "0";\nAcquire::http::No-Cache "true";\nAcquire::BrokenProxy "true";\nAcquire::Retries "3";\n' > /etc/apt/apt.conf.d/99fixbadproxy

# Install curl for healthcheck
RUN apt-get update -o Acquire::Retries=5 && \
    apt-get install -y --fix-missing -o Acquire::Retries=5 curl && \
    rm -rf /var/lib/apt/lists/*

# Set environment variables
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/opt/venv/bin:$PATH" \
    PYTHONPATH=/app

# Copy virtual environment from builder
COPY --from=builder /opt/venv /opt/venv

# Create app user with home directory
RUN groupadd -r appuser && useradd -r -g appuser -m -d /home/appuser appuser

# Create app directory
WORKDIR /app

# Copy the context engine code
COPY src/ ./src/

# Copy the main entry points
COPY main.py ./

# Copy alembic config + migration scripts. These MUST be in the image: the app
# runs `alembic upgrade head` at boot, so a container without them crash-loops.
COPY alembic.ini ./
COPY alembic/ ./alembic/

# Create model cache directory
RUN mkdir -p /home/appuser/.cache/torch /home/appuser/.cache/huggingface && \
    chown -R appuser:appuser /app /home/appuser

# Set environment variables for model cache
ENV TORCH_HOME=/home/appuser/.cache/torch \
    HF_HOME=/home/appuser/.cache/huggingface \
    SENTENCE_TRANSFORMERS_HOME=/home/appuser/.cache/torch/sentence_transformers

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8001/health || curl -f -g http://[::1]:8001/health || exit 1

# Expose port
EXPOSE 8001

# Command to run the context engine
CMD ["python", "main.py"]
