FROM python:3.12-slim AS base

WORKDIR /app

# Install dependencies first (cache layer)
COPY pyproject.toml .
RUN pip install --no-cache-dir . && rm -rf /root/.cache

# Copy application code
COPY codyclaw/ codyclaw/

# Create non-root user
RUN useradd --create-home --shell /bin/bash codyclaw
USER codyclaw

# Data directory
RUN mkdir -p /home/codyclaw/.codyclaw
VOLUME ["/home/codyclaw/.codyclaw"]

EXPOSE 8080

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')" || exit 1

CMD ["codyclaw"]
