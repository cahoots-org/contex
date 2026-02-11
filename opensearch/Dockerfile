FROM --platform=linux/amd64 opensearchproject/opensearch:2.11.0

USER root
RUN yum install -y util-linux && yum clean all
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh

ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
