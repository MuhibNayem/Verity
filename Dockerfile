# ═══════════════════════════════════════════════════════════════════════════════
# Free Search MCP Server — Docker Image (Lite)
# Multi-engine web search with tiered fallbacks. No API keys.
# NO browser binary included — uses HTTP-only tiers (DDGS, curl_cffi).
# Size: ~650MB
# ═══════════════════════════════════════════════════════════════════════════════

# ── Builder stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build dependencies for lxml and curl-cffi
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc \
    libxml2-dev \
    libxslt1-dev \
    libc6-dev \
    && rm -rf /var/lib/apt/lists/*

# Copy source and install
COPY pyproject.toml README.md ./
COPY src/ ./src/

# Install core dependencies into a virtualenv (no browser extras)
RUN python -m venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir .


# ── Runtime stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS runtime

LABEL org.opencontainers.image.title="Free Search MCP Server (Lite)"
LABEL org.opencontainers.image.description="Multi-engine web search MCP server — HTTP-only tiers (DDGS + curl_cffi)"
LABEL org.opencontainers.image.source="https://github.com/yourusername/free-search-mcp"

# Runtime system deps for lxml + curl
RUN apt-get update && apt-get install -y --no-install-recommends \
    libxml2 \
    libxslt1.1 \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Copy virtualenv from builder
COPY --from=builder /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH"

# Create non-root user for security
RUN useradd -m -u 1000 appuser
USER appuser
WORKDIR /home/appuser

# Default: stdio transport
ENTRYPOINT ["free-search"]
CMD ["--transport", "stdio"]
