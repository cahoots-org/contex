#!/bin/bash
# Fix volume permissions for Railway
chown -R opensearch:opensearch /usr/share/opensearch/data 2>/dev/null || true

# Drop to opensearch user and run
exec setpriv --reuid=opensearch --regid=opensearch --keep-groups -- /usr/share/opensearch/opensearch-docker-entrypoint.sh
