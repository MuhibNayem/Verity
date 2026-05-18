<div align="center">

# Verity — Free Search MCP Server

**Unlimited free web search for AI agents.** No API keys. No SearXNG. No Valkey.

Multi-engine search with intelligent tiered fallbacks: DuckDuckGo → Google → Brave → curl_cffi → nodriver.

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![MCP](https://img.shields.io/badge/MCP-1.6+-green.svg)](https://modelcontextprotocol.io/)

</div>

---

## Table of Contents

- [What This Solves](#what-this-solves)
- [Architecture](#architecture)
- [Features](#features)
- [Installation](#installation)
  - [From PyPI (soon)](#from-pypi-soon)
  - [From Source](#from-source)
  - [Docker](#docker)
- [Usage](#usage)
  - [Standalone](#standalone)
  - [Docker](#docker-1)
  - [Docker Compose](#docker-compose)
- [Connect Your MCP Client](#connect-your-mcp-client)
  - [Chorus-cli](#chorus-cli)
  - [Claude Desktop](#claude-desktop)
  - [Cursor](#cursor)
  - [SSE/HTTP Clients](#ssehttp-clients)
- [Tool Reference](#tool-reference)
- [Configuration](#configuration)
- [Development](#development)
  - [Setup](#setup)
  - [Testing](#testing)
  - [Linting](#linting)
- [Docker Reference](#docker-reference)
- [Troubleshooting](#troubleshooting)
- [License](#license)

---

## What This Solves

| Problem | Old Approach (SearXNG) | Verity Approach |
|---------|----------------------|-----------------|
| Poor/incomplete search results | SearXNG proxies to upstream engines with consensus scoring | Direct multi-engine queries + merge/deduplication |
| Requires SearXNG + Valkey infra | Docker Compose with 3+ services | Single Python process |
| Google API costs money | N/A — SearXNG doesn't use Google directly | DDGS backend="google" — free Google results |
| API rate limits | SearXNG instance gets blocked | 5-tier fallback chain ensures results |
| Heavy browser overhead | N/A | curl_cffi (no browser) → nodriver (lightweight CDP) |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         SEARCH QUERY                                      │
└─────────────────────────────────────────────────────────────────────────┘
                                    │
              ┌─────────────────────┼─────────────────────┐
              │                     │                     │
              ▼                     ▼                     ▼
    ┌─────────────────┐   ┌─────────────────┐   ┌─────────────────┐
    │  web_search     │   │ google_search   │   │  multi_search   │
    │  (DuckDuckGo)   │   │  (Tiered)       │   │  (Parallel)     │
    └─────────────────┘   └─────────────────┘   └─────────────────┘
              │                     │                     │
              └─────────────────────┼─────────────────────┘
                                    │
                                    ▼
              ┌─────────────────────────────────────────────┐
              │              TIERED FALLBACKS                 │
              ├─────────────────────────────────────────────┤
              │  Tier 1: DDGS backend="google"  (~1.4s)     │
              │  Tier 2: DDGS backend="brave"   (~0.8s)     │
              │  Tier 3: curl_cffi direct scrape (~0.5s)    │
              │  Tier 4: nodriver CDP browser   (~2s)       │
              │  Tier 5: Playwright legacy      (~3-5s)     │
              └─────────────────────────────────────────────┘
                                    │
                                    ▼
              ┌─────────────────────────────────────────────┐
              │            CONTENT EXTRACTION                 │
              │  fetch_and_extract — URL → clean markdown     │
              │  deep_research — search + extract combined    │
              └─────────────────────────────────────────────┘
```

---

## Features

### 8 MCP Tools

| Tool | Engine | Speed | Description |
|------|--------|-------|-------------|
| `web_search` | DuckDuckGo | ~500ms | General web search |
| `news_search` | DuckDuckGo | ~500ms | Recent news articles |
| `image_search` | DuckDuckGo | ~500ms | Image discovery |
| `google_search` | Tiered (Google→Brave→curl_cffi→nodriver) | ~1-3s | Google-quality results |
| `multi_search` | Parallel multi-engine | ~1-2s | Query DDG+Google+Brave simultaneously |
| `hybrid_search` | Auto-select best engine | ~500ms-3s | Best-effort universal search |
| `fetch_and_extract` | Direct HTTP + BeautifulSoup | ~1-3s | Extract clean markdown from any URL |
| `deep_research` | Hybrid + extraction | ~3-10s | Search then read top N result pages |

### Key Design Decisions

- **No API keys** — every tool uses free, public search interfaces
- **Tiered fallbacks** — if Google blocks, Brave delivers; if DDG fails, curl_cffi scrapes
- **Multi-engine merge** — `multi_search` queries engines in parallel and deduplicates
- **Lightweight** — primary path uses HTTP only (~2MB RAM); browser is last resort
- **Dockerized** — 381MB lite image or 1.6GB full image with Chromium

---

## Installation

### From PyPI (soon)

```bash
pip install verity-search-mcp
```

### From Source

```bash
# Clone
git clone git@github.com:MuhibNayem/Verity.git
cd Verity

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# Install with all dependencies
pip install -e ".[browser,dev]"

# Verify
free-search --help
```

### Docker

```bash
# Pull and run (lite image, ~381MB)
docker run --rm -i ghcr.io/muhibnayem/verity:latest --transport stdio

# Or build locally
git clone git@github.com:MuhibNayem/Verity.git
cd Verity
docker build -t verity:latest .
docker run --rm -p 3002:3002 verity:latest --transport sse --host 0.0.0.0 --port 3002
```

---

## Usage

### Standalone

```bash
# Activate venv first
source .venv/bin/activate

# stdio — for Claude, Chorus, Cursor
free-search --transport stdio

# SSE — Server-Sent Events
free-search --transport sse --host 0.0.0.0 --port 3002

# HTTP — Streamable HTTP
free-search --transport http --host 0.0.0.0 --port 3003
```

### Docker

```bash
# stdio (run interactively)
docker run --rm -i verity:latest --transport stdio

# SSE (daemon)
docker run --rm -d -p 3002:3002 verity:latest \
  --transport sse --host 0.0.0.0 --port 3002

# Full image with browser fallback
docker run --rm -d -p 3002:3002 verity:full \
  --transport sse --host 0.0.0.0 --port 3002
```

### Docker Compose

```bash
cd Verity

# Start SSE on port 3002 (recommended)
docker compose up -d

# Watch logs
docker compose logs -f

# Stop
docker compose down

# Other variants
docker compose --profile http up -d      # HTTP on 3003
docker compose --profile full up -d      # Full image on 3004
```

---

## Connect Your MCP Client

### Chorus-cli

**stdio:**
```bash
chorus mcp add verity --type stdio \
  --command free-search \
  --arg --transport --arg stdio

# Or with Docker
chorus mcp add verity-docker --type stdio \
  --command docker \
  --arg run --arg --rm --arg -i \
  --arg verity:latest \
  --arg --transport --arg stdio
```

**SSE:**
```bash
chorus mcp add verity-sse --type sse --url http://localhost:3002/sse
```

### Claude Desktop

Edit `~/Library/Application Support/Claude/claude_desktop_config.json` (macOS) or `%APPDATA%\Claude\claude_desktop_config.json` (Windows):

```json
{
  "mcpServers": {
    "verity": {
      "command": "free-search",
      "args": ["--transport", "stdio"]
    }
  }
}
```

With virtualenv:
```json
{
  "mcpServers": {
    "verity": {
      "command": "/path/to/Verity/.venv/bin/free-search",
      "args": ["--transport", "stdio"]
    }
  }
}
```

With Docker:
```json
{
  "mcpServers": {
    "verity": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "verity:latest", "--transport", "stdio"]
    }
  }
}
```

### Cursor

Open **Cursor Settings → MCP → Add Server**:

```json
{
  "mcpServers": {
    "verity": {
      "command": "free-search",
      "args": ["--transport", "stdio"]
    }
  }
}
```

Or add to `.cursor/mcp.json` in your project.

### SSE/HTTP Clients

Any client that speaks MCP over SSE or HTTP:

```python
from mcp import ClientSession
from mcp.client.sse import sse_client

async with sse_client("http://localhost:3002/sse") as (read, write):
    async with ClientSession(read, write) as session:
        await session.initialize()
        tools = await session.list_tools()
        result = await session.call_tool("web_search", {"query": "python asyncio"})
```

---

## Tool Reference

### `web_search(query, max_results=10)`

Search the web via DuckDuckGo. Fastest option for general queries.

```python
result = await web_search("machine learning tutorial", max_results=5)
# Returns markdown list with title, URL, and snippet
```

### `google_search(query, max_results=10)`

Tiered Google search. Tries Google (DDGS) → Brave → curl_cffi → nodriver → Playwright.

```python
result = await google_search("latest python release", max_results=5)
# Source tag shows which tier succeeded: "Google (DDGS)", "Brave (DDGS)", etc.
```

### `multi_search(query, max_results=10, engines="duckduckgo,google,brave")`

Query multiple engines in parallel and merge/deduplicate results.

```python
result = await multi_search(
    "docker best practices",
    max_results=5,
    engines="duckduckgo,google,brave,yahoo"
)
```

### `hybrid_search(query, max_results=10)`

Best-effort auto-selection. Tries DDG first, then cascades through Google tiers.

```python
result = await hybrid_search("rust vs go performance", max_results=5)
```

### `news_search(query, max_results=10)`

Recent news articles with source and date.

```python
result = await news_search("artificial intelligence", max_results=5)
```

### `image_search(query, max_results=10)`

Image URLs with dimensions and source pages.

```python
result = await image_search("mountain landscape", max_results=5)
```

### `fetch_and_extract(url)`

Fetch a webpage and extract clean markdown (removes ads, nav, scripts).

```python
result = await fetch_and_extract("https://en.wikipedia.org/wiki/Python_(programming_language)")
```

### `deep_research(query, max_results=5, fetch_content=True)`

Search + extract content from top results in one step.

```python
result = await deep_research("fastapi vs flask", max_results=3, fetch_content=True)
# Returns search results + extracted markdown from each page
```

---

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CHROME_BIN` | `/usr/bin/chromium` | Path to Chromium for nodriver |
| `CHROMIUM_FLAGS` | `--headless --no-sandbox...` | Flags passed to Chromium |
| `PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD` | `1` | Skip Playwright browser download (Docker) |

### Docker Compose Profiles

| Profile | Service | Port | Image | Use Case |
|---------|---------|------|-------|----------|
| *(default)* | `free-search` | 3002 | `latest` (381MB) | Standard SSE |
| `http` | `free-search-http` | 3003 | `latest` | HTTP transport |
| `full` | `free-search-full` | 3004 | `full` (1.6GB) | With browser fallback |

---

## Development

### Setup

```bash
git clone git@github.com:MuhibNayem/Verity.git
cd Verity
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[browser,dev]"
```

### Testing

```bash
# Run all tests
pytest tests/ -v

# Run with 2 retries for flaky network tests
pytest tests/ -v --reruns 2

# Run only unit tests (no network)
pytest tests/ -v -k "not Integration"

# Run specific test
pytest tests/test_search.py::TestGoogleSearch -v
```

### Linting

```bash
ruff check src/ tests/
ruff format src/ tests/
```

### Project Structure

```
Verity/
├── src/free_search_mcp/
│   ├── __init__.py
│   ├── server.py          # MCP tools & tiered fallbacks
│   └── extractor.py       # URL content extraction
├── tests/
│   └── test_search.py     # 40 test cases
├── Dockerfile             # Lite image (~381MB)
├── Dockerfile.full        # Full image (~1.6GB)
├── docker-compose.yml     # SSE + HTTP + Full profiles
├── pyproject.toml         # Dependencies & metadata
└── README.md
```

---

## Docker Reference

### Image Variants

| Tag | Size | Chromium | Playwright | Best For |
|-----|------|----------|------------|----------|
| `latest` | ~381MB | ❌ | ❌ | Production — HTTP-only tiers |
| `full` | ~1.6GB | ✅ | ✅ | When JS challenges require browser |

### Health Checks

All Docker Compose services include health checks:

```bash
# Check SSE health
curl -f http://localhost:3002/sse

# Check HTTP health
curl -f http://localhost:3003/mcp
```

### Building

```bash
# Lite image
docker build -t verity:latest .

# Full image
docker build -f Dockerfile.full -t verity:full .

# Multi-platform
docker buildx build --platform linux/amd64,linux/arm64 -t verity:latest .
```

---

## Troubleshooting

### "No results found from any source"

This means all search tiers were exhausted. Common causes:

1. **Rate limiting** — Your IP has been temporarily blocked by search engines.
   - **Fix:** Wait a few minutes, or use a different network.
   - **Fix:** Use `multi_search` with `engines="auto"` to cast a wider net.

2. **No internet access** (Docker)
   - **Fix:** Ensure Docker has network access: `docker run --rm verity:latest curl -I https://duckduckgo.com`

### "Google search unavailable"

Google backend returned empty. The tiered system fell through all layers.

- **Check:** Is your IP from a datacenter/VPN? Google blocks these more aggressively.
- **Fix:** Use `multi_search` which queries Brave simultaneously.
- **Fix:** Deploy the `full` Docker image with browser fallback.

### Playwright/nodriver not working

```bash
# System Chromium missing
which chromium

# For local dev, install Chromium
# macOS: brew install chromium
# Ubuntu: sudo apt install chromium-browser
# Arch: sudo pacman -S chromium
```

### Docker container exits immediately

```bash
# Check logs
docker compose logs -f

# Verify the image built correctly
docker run --rm verity:latest --help

# Check if port is already in use
lsof -i :3002
```

### Tests failing with "empty results"

The test suite hits live search APIs. Rate limits can cause intermittent failures.

```bash
# Tests retry flaky tests automatically
pytest tests/ -v --reruns 2

# Skip network tests
pytest tests/ -v -k "not TestDdgsBackends and not TestGoogleSearch"
```

---

## License

MIT © [Muhib Nayem](https://github.com/MuhibNayem)
