FROM --platform=linux/amd64 opensearchproject/opensearch:2.11.0

# Copy custom entrypoint
COPY --chmod=755 entrypoint.sh /usr/local/bin/entrypoint.sh

# Switch to root to install sudo
USER 0

# Install sudo and allow opensearch user to run chown as root
RUN yum install -y sudo && yum clean all && \
    echo "opensearch ALL=(root) NOPASSWD: /bin/chown" > /etc/sudoers.d/opensearch

# Switch back to opensearch user (OpenSearch refuses to run as root)
USER 1000:0

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
