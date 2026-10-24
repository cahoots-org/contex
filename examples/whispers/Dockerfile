# The Whispers web game. Build from the repo root:
#   docker build -f examples/whispers/Dockerfile .
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 WHISPERS_WEB_HOST=0.0.0.0

RUN useradd --create-home whispers
WORKDIR /app

COPY examples/whispers/requirements.txt examples/whispers/requirements.txt
RUN pip install --no-cache-dir -r examples/whispers/requirements.txt

COPY examples/whispers examples/whispers
# Without a volume the game saves its village and need baselines in the working directory.
RUN chown whispers /app

# The entrypoint runs as root to take the volume, then drops to whispers.
ENTRYPOINT ["examples/whispers/entrypoint.sh"]
CMD ["python", "-m", "examples.whispers", "--web"]
