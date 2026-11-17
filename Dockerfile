# Production-oriented image for the AWS MCP server; it also supports direct local Docker runs.
# Runtime configuration is supplied through environment variables and external AWS credentials.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/aws-mcp \
    MCP_TRANSPORT=streamable-http \
    MCP_HOST=0.0.0.0 \
    MCP_PORT=8000

WORKDIR /app

# Run the service without root privileges; AWS credentials are provided externally.
RUN groupadd --system --gid 10001 aws-mcp \
    && useradd --system --uid 10001 --gid 10001 --create-home --home-dir /home/aws-mcp aws-mcp

COPY pyproject.toml README.md ./
COPY src ./src

# Install the application without carrying package-manager caches into the image.
RUN python -m pip install --no-cache-dir . \
    && chown -R aws-mcp:aws-mcp /app /home/aws-mcp

USER aws-mcp

EXPOSE 8000

# A socket check confirms that the HTTP listener accepts connections without invoking AWS.
HEALTHCHECK --interval=30s --timeout=3s --start-period=10s --retries=3 \
    CMD python -c "import socket; s=socket.create_connection(('127.0.0.1', 8000), 2); s.close()"

CMD ["aws-mcp-server"]
