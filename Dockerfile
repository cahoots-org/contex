# Multi-stage build for Context Engine Service
FROM python:3.14-slim@sha256:4ed33101ee7ec299041cc41dd268dae17031184be94384b1ce7936dc4e5dead3 as builder

# Install system dependencies
RUN apt-get update && apt-get install -y \
    gcc \
    g++ \
    && rm -rf /var/lib/apt/lists/*

# Create virtual environment
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Copy requirements and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Production stage
FROM python:3.14-slim@sha256:4ed33101ee7ec299041cc41dd268dae17031184be94384b1ce7936dc4e5dead3

# Install curl for healthcheck
RUN apt-get update && apt-get install -y curl && rm -rf /var/lib/apt/lists/*

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

# Create model cache directory
RUN mkdir -p /home/appuser/.cache/torch /home/appuser/.cache/huggingface && \
    chown -R appuser:appuser /app /home/appuser

# Set environment variables for model cache
ENV TORCH_HOME=/home/appuser/.cache/torch \
    HF_HOME=/home/appuser/.cache/huggingface \
    TRANSFORMERS_CACHE=/home/appuser/.cache/huggingface/transformers \
    SENTENCE_TRANSFORMERS_HOME=/home/appuser/.cache/torch/sentence_transformers

# Switch to non-root user
USER appuser

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
    CMD curl -f http://localhost:8001/health || exit 1

# Expose port
EXPOSE 8001

# Command to run the context engine
CMD ["python", "main.py"]
