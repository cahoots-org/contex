FROM --platform=linux/amd64 opensearchproject/opensearch:2.11.0

USER root
RUN which gosu || (apt-get update && apt-get install -y gosu && rm -rf /var/lib/apt/lists/*) || (yum install -y gosu || true)
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
