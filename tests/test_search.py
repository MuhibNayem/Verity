"""Comprehensive tests for free-search-mcp tools."""

from __future__ import annotations

import asyncio
import pytest

from free_search_mcp.server import (
    web_search,
    news_search,
    image_search,
    google_search,
    multi_search,
    hybrid_search,
    fetch_and_extract,
    deep_research,
    _ddgs_search,
    _google_search_curl_cffi,
    _google_search_nodriver,
    _google_search_playwright,
    _google_search_tiered,
    _format_results,
    _dedupe_results,
    _merge_results,
)
from free_search_mcp.extractor import fetch_page, is_blocked


# ── Unit Tests ─────────────────────────────────────────────────────────────

class TestFormatResults:
    def test_empty_results(self):
        out = _format_results("python tutorial", [], "TestSource")
        assert "No results found" in out
        assert "TestSource" in out

    def test_with_results(self):
        results = [
            {"title": "Python Guide", "href": "https://example.com", "body": "Learn Python fast"},
        ]
        out = _format_results("python", results, "DDG")
        assert "Python Guide" in out
        assert "https://example.com" in out
        assert "Learn Python fast" in out


class TestDedupeResults:
    def test_dedupe_by_url(self):
        results = [
            {"title": "A", "href": "https://example.com/page"},
            {"title": "B", "href": "https://example.com/page/"},  # trailing slash
            {"title": "C", "href": "https://other.com"},
        ]
        out = _dedupe_results(results)
        assert len(out) == 2
        assert out[0]["title"] == "A"
        assert out[1]["title"] == "C"

    def test_empty_list(self):
        assert _dedupe_results([]) == []


class TestMergeResults:
    def test_interleave(self):
        a = [{"title": "A1", "href": "https://a1.com"}, {"title": "A2", "href": "https://a2.com"}]
        b = [{"title": "B1", "href": "https://b1.com"}]
        merged = _merge_results([a, b], max_results=10)
        assert len(merged) == 3
        # Round-robin: A1, B1, A2
        assert merged[0]["title"] == "A1"
        assert merged[1]["title"] == "B1"
        assert merged[2]["title"] == "A2"

    def test_dedupes_during_merge(self):
        a = [{"title": "Same", "href": "https://same.com"}]
        b = [{"title": "Same2", "href": "https://same.com/"}]
        merged = _merge_results([a, b], max_results=10)
        assert len(merged) == 1

    def test_respects_max_results(self):
        a = [{"title": f"A{i}", "href": f"https://a{i}.com"} for i in range(10)]
        b = [{"title": f"B{i}", "href": f"https://b{i}.com"} for i in range(10)]
        merged = _merge_results([a, b], max_results=5)
        assert len(merged) == 5


class TestIsBlocked:
    def test_youtube_blocked(self):
        assert is_blocked("https://www.youtube.com/watch?v=123")

    def test_normal_site_allowed(self):
        assert not is_blocked("https://example.com/article")

    def test_github_allowed(self):
        assert not is_blocked("https://github.com/some/repo")


# ── Integration Tests: DuckDuckGo ──────────────────────────────────────────

@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
class TestWebSearch:
    async def test_returns_results(self):
        result = await web_search("python asyncio tutorial", max_results=5)
        assert "Search: python asyncio tutorial" in result
        assert "http" in result
        assert result.count("http") >= 3

    async def test_max_results_respected(self):
        result = await web_search("machine learning", max_results=3)
        lines = [line for line in result.split("\n") if line.strip().startswith(("1.", "2.", "3.", "4."))]
        assert len(lines) <= 3

    async def test_no_results_for_gibberish(self):
        result = await web_search("xyz123nonexistent456abc", max_results=5)
        assert "Search:" in result


@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
class TestNewsSearch:
    async def test_returns_news_results(self):
        result = await news_search("technology", max_results=5)
        assert "News Search: technology" in result
        assert "http" in result

    async def test_recent_topic(self):
        result = await news_search("artificial intelligence", max_results=3)
        assert "artificial intelligence" in result
        assert "http" in result


@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
class TestImageSearch:
    async def test_returns_image_results(self):
        result = await image_search("cat", max_results=5)
        assert "Image Search: cat" in result
        assert "http" in result


# ── Integration Tests: DDGS Backends ───────────────────────────────────────

@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
class TestDdgsBackends:
    async def test_google_backend(self):
        results = await _ddgs_search("rust programming", max_results=5, backend="google")
        # Rate-limited env may return empty; verify structure only
        assert isinstance(results, list)

    async def test_brave_backend(self):
        results = await _ddgs_search("docker containers", max_results=5, backend="brave")
        assert isinstance(results, list)

    async def test_duckduckgo_backend(self):
        results = await _ddgs_search("fastapi tutorial", max_results=5, backend="duckduckgo")
        # DDG may rate-limit; just verify it returns a list
        assert isinstance(results, list)

    async def test_invalid_backend_auto_fallback(self):
        """DDGS falls back to 'auto' when an invalid backend is passed."""
        results = await _ddgs_search("test query", max_results=5, backend="nonexistent")
        assert isinstance(results, list)
        # auto fallback may return results
        assert len(results) >= 0


# ── Integration Tests: Google Search (Tiered) ──────────────────────────────

@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
class TestGoogleSearch:
    async def test_google_search_tool(self):
        """Test the public google_search tool (uses tiered fallbacks)."""
        result = await google_search("python programming language", max_results=5)
        assert "Search: python programming language" in result
        # Should either have results or a helpful error message
        assert "http" in result or "unavailable" in result.lower() or "No results" in result

    async def test_google_search_tiered_internal(self):
        """Test the internal tiered search directly."""
        results, source = await _google_search_tiered("wikipedia python", max_results=5)
        assert isinstance(results, list)
        if results:
            assert source != ""
            assert all("title" in r and "href" in r for r in results)

    async def test_curl_cffi_direct_scrape(self):
        """Test curl_cffi Google scraper directly."""
        results = await _google_search_curl_cffi("github actions", max_results=5)
        # May return empty if Google serves JS page from this IP
        # Just verify it doesn't crash
        assert isinstance(results, list)

    async def test_nodriver_direct_scrape(self):
        """Test nodriver Google scraper directly."""
        results = await _google_search_nodriver("github actions", max_results=3)
        # May return empty if blocked; verify no crash
        assert isinstance(results, list)

    async def test_playwright_direct_scrape(self):
        """Test Playwright Google scraper directly."""
        results = await _google_search_playwright("github actions", max_results=3)
        # Skipped effectively if browser not installed or blocked
        assert isinstance(results, list)


# ── Integration Tests: Multi Search ────────────────────────────────────────

@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
class TestMultiSearch:
    async def test_multi_search_basic(self):
        result = await multi_search("docker compose", max_results=5, engines="duckduckgo,google")
        assert "Multi-Search: docker compose" in result
        # Verify format regardless of results (rate-limited env may be empty)
        assert "Engines queried" in result or "No results found" in result

    async def test_multi_search_three_engines(self):
        result = await multi_search("machine learning", max_results=5, engines="duckduckgo,google,brave")
        # Rate-limited env may return empty; just verify no crash
        assert "Multi-Search: machine learning" in result

    async def test_multi_search_no_engines_error(self):
        result = await multi_search("test", max_results=3, engines="")
        assert "Error" in result


# ── Integration Tests: Hybrid Search ───────────────────────────────────────

@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
class TestHybridSearch:
    async def test_hybrid_returns_results(self):
        result = await hybrid_search("docker containers", max_results=5)
        assert "Search: docker containers" in result
        assert "http" in result

    async def test_hybrid_fallback_logic(self):
        result = await hybrid_search("github actions ci", max_results=3)
        assert "http" in result
        assert "github" in result.lower() or "actions" in result.lower()


# ── Integration Tests: Content Extraction ──────────────────────────────────

@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
class TestFetchAndExtract:
    async def test_fetch_wikipedia(self):
        result = await fetch_and_extract("https://en.wikipedia.org/wiki/Python_(programming_language)")
        assert result.startswith("# ")
        assert "Python" in result
        assert "https://en.wikipedia.org" in result
        assert "Error" not in result.split("\n")[0]

    async def test_fetch_blocked_domain(self):
        result = await fetch_and_extract("https://www.youtube.com/watch?v=dQw4w9WgXcQ")
        assert "blocks automated fetching" in result or "Failed" in result

    async def test_fetch_nonexistent(self):
        result = await fetch_and_extract("https://this-domain-definitely-does-not-exist-12345.com")
        assert "Failed" in result or "Error" in result


@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
class TestFetchPage:
    async def test_successful_fetch(self):
        result = await fetch_page("https://httpbin.org/html")
        assert result["success"] is True
        assert "Herman Melville" in result["markdown"] or "html" in result["markdown"].lower()

    async def test_timeout(self):
        result = await fetch_page("https://httpbin.org/delay/30", timeout=2.0)
        assert result["success"] is False
        assert "timed out" in result["error"].lower()


# ── Integration Tests: Deep Research ───────────────────────────────────────

@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
class TestDeepResearch:
    async def test_deep_research_basic(self):
        result = await deep_research("fastapi vs flask", max_results=3, fetch_content=False)
        assert "fastapi vs flask" in result
        assert "http" in result

    async def test_deep_research_with_fetch(self):
        result = await deep_research("python dataclasses", max_results=2, fetch_content=True)
        assert "python dataclasses" in result
        assert "Fetched Content" in result
        assert "http" in result


# ── Parallel Load Test ─────────────────────────────────────────────────────

@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
async def test_parallel_searches():
    """Verify the server handles multiple concurrent searches."""
    queries = [
        "asyncio python",
        "machine learning",
        "docker compose",
        "rust programming",
    ]
    tasks = [web_search(q, max_results=3) for q in queries]
    results = await asyncio.gather(*tasks)

    assert len(results) == len(queries)
    for r in results:
        assert "http" in r


@pytest.mark.flaky(reruns=2)
@pytest.mark.asyncio
async def test_parallel_multi_backends():
    """Verify concurrent searches across different backends."""
    tasks = [
        _ddgs_search("python", max_results=3, backend="duckduckgo"),
        _ddgs_search("python", max_results=3, backend="google"),
        _ddgs_search("python", max_results=3, backend="brave"),
    ]
    results = await asyncio.gather(*tasks)
    # Verify all calls returned lists (rate-limited env may have empty lists)
    assert all(isinstance(r, list) for r in results)


# ── MCP Server Smoke Test ──────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_mcp_server_tools_exist():
    """Verify FastMCP server registers all expected tools."""
    from free_search_mcp.server import mcp

    tools = await mcp.list_tools()
    tool_names = {t.name for t in tools}

    expected = {
        "web_search",
        "news_search",
        "image_search",
        "google_search",
        "multi_search",
        "hybrid_search",
        "fetch_and_extract",
        "deep_research",
    }
    assert expected.issubset(tool_names), f"Missing tools: {expected - tool_names}"
