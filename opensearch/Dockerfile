FROM --platform=linux/amd64 opensearchproject/opensearch:2.11.0

USER root

# Patch entrypoint to allow running as root (required for Railway volume permissions)
RUN sed -i 's/\[[ ]*"$(id -u)" == "0"[ ]*\]/false/' /usr/share/opensearch/opensearch-docker-entrypoint.sh

ENTRYPOINT ["/usr/share/opensearch/opensearch-docker-entrypoint.sh"]
