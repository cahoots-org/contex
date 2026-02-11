#!/bin/bash
# Fix volume permissions for Railway
chown -R opensearch:opensearch /usr/share/opensearch/data 2>/dev/null || true

# Drop to opensearch user and run
exec runuser -u opensearch -- /usr/share/opensearch/opensearch-docker-entrypoint.sh
