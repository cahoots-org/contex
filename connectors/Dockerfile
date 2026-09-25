# Shared image for every Contex connector. Select one with the CONNECTOR
# build-arg, e.g. `--build-arg CONNECTOR=s3`. Build from the repo root so the
# `connectors` package copies in as an importable module.
#
#   docker build --platform linux/amd64 --build-arg CONNECTOR=s3 \
#     -f connectors/Dockerfile -t contex-connector-s3 .
FROM python:3.12-slim

ARG CONNECTOR
RUN test -n "$CONNECTOR" || (echo "CONNECTOR build-arg is required" && exit 1)

WORKDIR /app

# The per-connector requirements pull in the shared base deps via a relative
# `-r ../base/requirements.txt`, so both requirement trees must be present when
# pip runs. The base tree already pins the mcp SDK used by the MCP transport.
COPY connectors/base/requirements.txt /app/connectors/base/requirements.txt
COPY connectors/${CONNECTOR}/requirements.txt /app/connectors/${CONNECTOR}/requirements.txt
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r /app/connectors/${CONNECTOR}/requirements.txt

COPY connectors/__init__.py /app/connectors/__init__.py
COPY connectors/base /app/connectors/base
COPY connectors/${CONNECTOR} /app/connectors/${CONNECTOR}

# Bake the connector's env-driven template as the default config. Every value is
# a ${VAR} placeholder expanded at load time, so `docker run -e ...` needs no
# file mount. Power users can still mount their own yaml over this path.
RUN mkdir -p /etc/contex && \
    cp /app/connectors/${CONNECTOR}/connector.template.yaml /etc/contex/connector.yaml

# Drop root: the connector only reads config and streams to Contex over MCP.
RUN useradd --system --create-home --uid 10001 connector
USER connector

# A build-arg does not survive into an exec-form ENTRYPOINT, so promote it to an
# env var and resolve the module in a shell at runtime.
ENV CONNECTOR=${CONNECTOR}
ENTRYPOINT ["sh", "-c", "exec python -m connectors.\"$CONNECTOR\" --config /etc/contex/connector.yaml \"$@\"", "--"]
