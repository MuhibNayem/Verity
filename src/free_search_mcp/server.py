"""Free Search MCP Server — Multi-engine web search with tiered fallbacks.

No API keys required. Unlimited free web search.
Architecture:
  Tier 0: DDGS (DuckDuckGo) — primary, fastest
  Tier 1: DDGS backend="google" — Google-quality results
  Tier 2: DDGS backend="brave" — independent index
  Tier 3: curl_cffi direct Google scrape — raw control, no browser overhead
  Tier 4: nodriver CDP browser — lightweight real browser fallback
  Tier 5: Playwright — legacy full-browser fallback (if installed)
"""

from __future__ import annotations

import argparse
import asyncio
import re
from urllib.parse import unquote

from bs4 import BeautifulSoup
from mcp.server.fastmcp import FastMCP, Context
from mcp.types import ToolAnnotations

from .extractor import fetch_page

# ── Lifespan & Server Setup ────────────────────────────────────────────────

mcp = FastMCP(
    "free-search",
    instructions=(
        "Unlimited free web search MCP server. No API keys. "
        "Multi-engine: DuckDuckGo, Google, Brave, and more. "
        "Tiered fallbacks ensure maximum accuracy and availability. "
        "Also provides news search, image search, and webpage content extraction."
    ),
)


# ── Helpers ────────────────────────────────────────────────────────────────

READ_ONLY = ToolAnnotations(readOnlyHint=True, openWorldHint=True)


def _format_results(query: str, results: list[dict], source: str) -> str:
    if not results:
        return f"# Search: {query}\n\nNo results found from {source}."

    lines = [f"# Search: {query}  ({source})\n"]
    for i, r in enumerate(results, 1):
        title = r.get("title", "")
        href = r.get("href", r.get("url", ""))
        body = r.get("body", r.get("content", r.get("snippet", "")))
        lines.append(f"{i}. **{title}**")
        lines.append(f"   {href}")
        if body:
            lines.append(f"   > {body[:280]}...")
        lines.append("")
    return "\n".join(lines)


def _dedupe_results(results: list[dict]) -> list[dict]:
    """Deduplicate results by URL, keeping the first occurrence."""
    seen: set[str] = set()
    out: list[dict] = []
    for r in results:
        href = r.get("href", r.get("url", ""))
        norm = href.rstrip("/").lower()
        if norm and norm not in seen:
            seen.add(norm)
            out.append(r)
    return out


def _merge_results(result_lists: list[list[dict]], max_results: int = 10) -> list[dict]:
    """Interleave results from multiple engines and deduplicate."""
    merged: list[dict] = []
    # Round-robin interleave
    max_len = max((len(lst) for lst in result_lists), default=0)
    for i in range(max_len):
        for lst in result_lists:
            if i < len(lst):
                merged.append(lst[i])
    return _dedupe_results(merged)[:max_results]


# ── Internal: DDGS Search (all backends) ───────────────────────────────────

async def _ddgs_search(
    query: str,
    max_results: int = 10,
    backend: str = "duckduckgo",
) -> list[dict]:
    """Search via DDGS with specified backend. Returns empty list on failure."""
    try:
        from ddgs import DDGS
    except ImportError:
        return []

    try:
        with DDGS() as ddgs:
            raw = ddgs.text(query, max_results=max(max_results, 30), backend=backend)
            return raw  # ddgs already returns list[dict]
    except Exception:
        return []


# ── Internal: curl_cffi Google Scrape ──────────────────────────────────────

async def _google_search_curl_cffi(query: str, max_results: int = 10) -> list[dict]:
    """Scrape Google via curl_cffi (impersonates Chrome TLS/HTTP2). No browser needed."""
    try:
        from curl_cffi import requests
    except ImportError:
        return []

    try:
        search_url = (
            f"https://www.google.com/search?"
            f"q={query.replace(' ', '+')}"
            f"&num={min(max_results + 5, 20)}"
            f"&hl=en"
        )
        resp = requests.get(search_url, impersonate="chrome120", timeout=15)
        if resp.status_code != 200:
            return []

        soup = BeautifulSoup(resp.text, "lxml")
        results: list[dict] = []

        # Google SERP selectors (evolves over time; try multiple strategies)
        selectors = [
            "div.g",  # classic
            "div[data-sokoban-container]",  # newer
            "div[data-ved] div:has(h3)",  # semantic
        ]

        for sel in selectors:
            items = soup.select(sel)
            if items:
                for item in items[:max_results]:
                    title_el = item.select_one("h3")
                    if not title_el:
                        continue

                    # Find the link — may be wrapped in <a> around or near the h3
                    link_el = item.select_one("a[href]")
                    if not link_el:
                        # Try parent or sibling <a>
                        link_el = title_el.find_parent("a")

                    href = ""
                    if link_el:
                        href = link_el.get("href", "")
                        # Google wraps external links: /url?q=https://...&sa=...
                        if href.startswith("/url?q="):
                            href = href.split("/url?q=")[1].split("&")[0]
                            href = unquote(href)
                        elif href.startswith("/search"):
                            continue  # internal Google link

                    snippet_el = item.select_one(
                        "div.VwiC3b, span.aCOpRe, div.s, div[data-sncf], div[style*='-webkit-line-clamp']"
                    )
                    snippet = snippet_el.get_text(strip=True) if snippet_el else ""

                    if title_el and href and href.startswith("http"):
                        results.append({
                            "title": title_el.get_text(strip=True),
                            "href": href,
                            "body": snippet,
                        })
                break  # Stop after first successful selector

        return _dedupe_results(results)[:max_results]
    except Exception:
        return []


# ── Internal: nodriver Google Scrape ───────────────────────────────────────

async def _google_search_nodriver(query: str, max_results: int = 10) -> list[dict]:
    """Scrape Google via nodriver (CDP-minimal browser). ~3-5x lighter than Playwright."""
    try:
        import nodriver as uc
    except ImportError:
        return []

    results: list[dict] = []
    browser = None

    try:
        browser = await uc.start(headless=True)
        tab = await browser.get(
            f"https://www.google.com/search?"
            f"q={query.replace(' ', '+')}"
            f"&num={min(max_results + 5, 20)}"
            f"&hl=en"
        )
        await tab.sleep(2.5)

        # Try multiple selectors
        selectors = ["div.g", "div[data-sokoban-container]", "h3"]
        for sel in selectors:
            items = await tab.select_all(sel)
            if items:
                for item in items[:max_results]:
                    if sel == "h3":
                        # When selecting h3 directly, find parent container
                        title = await item.get_text()
                        parent_a = await item.query_selector("xpath::..")
                        if not parent_a:
                            continue
                        href = await parent_a.get_attribute("href") or ""
                        # Try to find snippet in grandparent
                        snippet = ""
                    else:
                        title_el = await item.query_selector("h3")
                        if not title_el:
                            continue
                        title = await title_el.get_text()

                        link_el = await item.query_selector("a")
                        href = await link_el.get_attribute("href") if link_el else ""

                        snippet_el = await item.query_selector(
                            "div.VwiC3b, span.aCOpRe, div.s"
                        )
                        snippet = await snippet_el.get_text() if snippet_el else ""

                    if href.startswith("/url?q="):
                        href = href.split("/url?q=")[1].split("&")[0]
                        href = unquote(href)
                    elif href.startswith("/search"):
                        continue

                    if title and href and href.startswith("http"):
                        results.append({"title": title, "href": href, "body": snippet})
                break

    except Exception:
        pass
    finally:
        if browser:
            try:
                browser.stop()
            except Exception:
                pass

    return _dedupe_results(results)[:max_results]


# ── Internal: Playwright Google Scrape (legacy) ────────────────────────────

async def _google_search_playwright(query: str, max_results: int = 10) -> list[dict]:
    """Scrape Google via Playwright. Kept as ultimate fallback. Returns empty list on failure."""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return []

    results: list[dict] = []

    async with async_playwright() as p:
        try:
            browser = await p.chromium.launch(headless=True)
        except Exception:
            return []

        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1920, "height": 1080},
            locale="en-US",
        )

        await context.add_init_script("""
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
            window.chrome = { runtime: {} };
        """)

        page = await context.new_page()

        try:
            search_url = (
                f"https://www.google.com/search?"
                f"q={query.replace(' ', '+')}"
                f"&num={min(max_results + 5, 20)}"
                f"&hl=en"
            )
            await page.goto(search_url, wait_until="domcontentloaded", timeout=20000)
            await asyncio.sleep(0.5)
            await page.wait_for_selector("div.g, div[data-sokoban-container]", timeout=10000)

            items = await page.query_selector_all("div.g")
            for item in items[:max_results]:
                title_el = await item.query_selector("h3")
                link_el = await item.query_selector("a")
                snippet_el = await item.query_selector("div.VwiC3b, span.aCOpRe, div.s")

                title = await title_el.inner_text() if title_el else ""
                href = await link_el.get_attribute("href") if link_el else ""
                snippet = await snippet_el.inner_text() if snippet_el else ""

                if title and href and href.startswith("http") and not href.startswith("https://www.google.com"):
                    results.append({"title": title, "href": href, "body": snippet})

        except Exception:
            pass
        finally:
            await browser.close()

    return results


# ── Tiered Google Search ───────────────────────────────────────────────────

async def _google_search_tiered(query: str, max_results: int = 10, ctx: Context | None = None) -> tuple[list[dict], str]:
    """Tiered Google search: ddgs → curl_cffi → nodriver → playwright.
    Returns (results, source_name).
    """
    # Tier 1: DDGS backend="google" (fastest, most reliable)
    if ctx:
        await ctx.info("Google search: trying DDGS backend=google...")
    results = await _ddgs_search(query, max_results, backend="google")
    if results:
        return results, "Google (DDGS)"

    # Tier 2: DDGS backend="brave" (independent index, good quality)
    if ctx:
        await ctx.info("Google search: trying DDGS backend=brave...")
    results = await _ddgs_search(query, max_results, backend="brave")
    if results:
        return results, "Brave (DDGS)"

    # Tier 3: curl_cffi direct scrape (lightweight, no browser)
    if ctx:
        await ctx.info("Google search: trying curl_cffi direct scrape...")
    results = await _google_search_curl_cffi(query, max_results)
    if results:
        return results, "Google (curl_cffi)"

    # Tier 4: nodriver CDP browser (~3-5x lighter than Playwright)
    if ctx:
        await ctx.info("Google search: trying nodriver CDP browser...")
    results = await _google_search_nodriver(query, max_results)
    if results:
        return results, "Google (nodriver)"

    # Tier 5: Playwright legacy fallback
    if ctx:
        await ctx.info("Google search: trying Playwright legacy fallback...")
    results = await _google_search_playwright(query, max_results)
    if results:
        return results, "Google (Playwright)"

    return [], ""


# ── MCP Tools ──────────────────────────────────────────────────────────────

@mcp.tool(annotations=READ_ONLY)
async def web_search(query: str, max_results: int = 10, ctx: Context | None = None) -> str:
    """Search the web via DuckDuckGo (fast, free, no API keys).

    Best for general-purpose queries. Returns title, URL, and snippet.
    """
    if ctx:
        await ctx.info(f"web_search: '{query[:60]}...'")

    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: ddgs package not installed. Run: pip install ddgs"

    try:
        with DDGS() as ddgs:
            raw = ddgs.text(query, max_results=max(max_results, 30))
            results = raw
    except Exception as exc:
        return f"DuckDuckGo search failed: {exc}"

    return _format_results(query, results[:max_results], "DuckDuckGo")


@mcp.tool(annotations=READ_ONLY)
async def news_search(query: str, max_results: int = 10, ctx: Context | None = None) -> str:
    """Search news via DuckDuckGo.

    Returns recent news articles with title, URL, source, and date.
    """
    if ctx:
        await ctx.info(f"news_search: '{query[:60]}...'")

    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: ddgs package not installed."

    try:
        with DDGS() as ddgs:
            raw = ddgs.news(query, max_results=max(max_results, 30))
            results = []
            for r in raw:
                results.append({
                    "title": r.get("title", ""),
                    "href": r.get("url", r.get("href", "")),
                    "body": r.get("body", r.get("snippet", "")),
                    "date": r.get("date", ""),
                    "source": r.get("source", ""),
                })
    except Exception as exc:
        return f"News search failed: {exc}"

    if not results:
        return f"# News Search: {query}\n\nNo news results found."

    lines = [f"# News Search: {query}  (DuckDuckGo)\n"]
    for i, r in enumerate(results[:max_results], 1):
        date = r.get("date", "")
        source = r.get("source", "")
        meta = f"  ({source}, {date})" if source or date else ""
        lines.append(f"{i}. **{r['title']}**{meta}")
        lines.append(f"   {r['href']}")
        if r.get("body"):
            lines.append(f"   > {r['body'][:280]}...")
        lines.append("")
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def image_search(query: str, max_results: int = 10, ctx: Context | None = None) -> str:
    """Search images via DuckDuckGo.

    Returns image URLs with titles and source pages.
    """
    if ctx:
        await ctx.info(f"image_search: '{query[:60]}...'")

    try:
        from ddgs import DDGS
    except ImportError:
        return "Error: ddgs package not installed."

    try:
        with DDGS() as ddgs:
            raw = ddgs.images(query, max_results=max(max_results, 30))
            results = []
            for r in raw:
                results.append({
                    "title": r.get("title", ""),
                    "href": r.get("image", r.get("url", "")),
                    "source": r.get("source", ""),
                    "width": r.get("width", ""),
                    "height": r.get("height", ""),
                })
    except Exception as exc:
        return f"Image search failed: {exc}"

    if not results:
        return f"# Image Search: {query}\n\nNo image results found."

    lines = [f"# Image Search: {query}  (DuckDuckGo)\n"]
    for i, r in enumerate(results[:max_results], 1):
        dims = f"{r['width']}×{r['height']}" if r.get("width") and r.get("height") else ""
        lines.append(f"{i}. **{r['title']}**  {dims}")
        lines.append(f"   Image: {r['href']}")
        if r.get("source"):
            lines.append(f"   Source page: {r['source']}")
        lines.append("")
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def google_search(query: str, max_results: int = 10, ctx: Context | None = None) -> str:
    """Search Google via tiered fallbacks (DDGS → curl_cffi → nodriver → Playwright).

    Prioritizes the fastest working method. Returns high-quality Google-like results.
    """
    if ctx:
        await ctx.info(f"google_search: '{query[:60]}...'")

    results, source = await _google_search_tiered(query, max_results, ctx)
    if not results:
        return (
            f"# Google Search: {query}\n\n"
            "Google search unavailable. All tiers exhausted:\n"
            "- DDGS backend=google\n"
            "- DDGS backend=brave\n"
            "- curl_cffi direct scrape\n"
            "- nodriver CDP browser\n"
            "- Playwright legacy fallback\n\n"
            "Possible reasons: IP blocked, rate limited, or required packages not installed.\n"
            "Try `web_search` (DuckDuckGo) instead."
        )

    return _format_results(query, results, source)


@mcp.tool(annotations=READ_ONLY)
async def multi_search(
    query: str,
    max_results: int = 10,
    engines: str = "duckduckgo,google,brave",
    ctx: Context | None = None,
) -> str:
    """Search multiple engines in parallel and merge/deduplicate results.

    Engines: duckduckgo, google, brave, yahoo, yandex (comma-separated).
    Uses DDGS under the hood for each backend.
    """
    if ctx:
        await ctx.info(f"multi_search: '{query[:60]}...' engines={engines}")

    backend_list = [b.strip().lower() for b in engines.split(",")]
    backend_list = [b for b in backend_list if b]  # remove empty

    if not backend_list:
        return "Error: no engines specified."

    # Run searches in parallel
    tasks = []
    for backend in backend_list:
        tasks.append(_ddgs_search(query, max_results=max_results * 2, backend=backend))

    raw_results = await asyncio.gather(*tasks, return_exceptions=True)

    result_lists: list[list[dict]] = []
    source_counts: dict[str, int] = {}
    for backend, res in zip(backend_list, raw_results):
        if isinstance(res, list) and res:
            result_lists.append(res)
            source_counts[backend] = len(res)

    if not result_lists:
        return f"# Multi-Search: {query}\n\nNo results found from any engine."

    merged = _merge_results(result_lists, max_results=max_results)

    lines = [f"# Multi-Search: {query}  ({', '.join(source_counts.keys())})\n"]
    lines.append(f"Engines queried: {len(backend_list)} | Results merged: {len(merged)}\n")
    for i, r in enumerate(merged, 1):
        title = r.get("title", "")
        href = r.get("href", r.get("url", ""))
        body = r.get("body", r.get("snippet", ""))
        lines.append(f"{i}. **{title}**")
        lines.append(f"   {href}")
        if body:
            lines.append(f"   > {body[:280]}...")
        lines.append("")
    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def hybrid_search(query: str, max_results: int = 10, ctx: Context | None = None) -> str:
    """Best-effort search: tries engines in order of quality until results are found.

    Order: DDGS → Google (DDGS) → Brave (DDGS) → curl_cffi → nodriver → Playwright.
    """
    if ctx:
        await ctx.info(f"hybrid_search: '{query[:60]}...'")

    # Tier 0: DuckDuckGo
    try:
        from ddgs import DDGS
        with DDGS() as ddgs:
            raw = ddgs.text(query, max_results=max(max_results, 30))
            if raw:
                return _format_results(query, raw[:max_results], "DuckDuckGo")
    except Exception as exc:
        if ctx:
            await ctx.debug(f"DDG failed: {exc}")

    # Tier 1-5: Google tiered fallbacks
    if ctx:
        await ctx.info("Falling back to Google tiered search...")
    results, source = await _google_search_tiered(query, max_results, ctx)
    if results:
        return _format_results(query, results, source)

    return f"# Search: {query}\n\nNo results found from any source."


@mcp.tool(annotations=READ_ONLY)
async def fetch_and_extract(url: str, ctx: Context | None = None) -> str:
    """Fetch a webpage and extract clean markdown content.

    Removes ads, navigation, scripts, and boilerplate.
    """
    if ctx:
        await ctx.info(f"fetch_and_extract: {url[:80]}...")

    result = await fetch_page(url, include_links=False)

    if not result["success"]:
        return f"# Extraction Failed\n\nURL: {url}\nError: {result['error']}"

    lines = [
        f"# {result['title']}",
        f"**URL:** {result['url']}",
    ]
    if result.get("description"):
        lines.append(f"**Description:** {result['description']}")
    lines.append("")

    markdown = result["markdown"]
    if len(markdown) > 8000:
        lines.append(markdown[:8000])
        lines.append(f"\n... [{len(markdown) - 8000} more chars]")
    else:
        lines.append(markdown)

    return "\n".join(lines)


@mcp.tool(annotations=READ_ONLY)
async def deep_research(
    query: str,
    max_results: int = 5,
    fetch_content: bool = True,
    ctx: Context | None = None,
) -> str:
    """Search + extract content from top results in one step.

    Performs a hybrid search, then fetches and extracts markdown
    from the top N result pages.
    """
    if ctx:
        await ctx.info(f"deep_research: '{query[:60]}...'")

    search_result = await hybrid_search(query, max_results=max_results, ctx=ctx)
    lines = [search_result, ""]

    if not fetch_content:
        return "\n".join(lines)

    urls = re.findall(r"^   (https?://\S+)", search_result, re.MULTILINE)
    urls = list(dict.fromkeys(urls))[:max_results]

    if not urls:
        lines.append("No URLs to fetch.")
        return "\n".join(lines)

    lines.append("---")
    lines.append("## Fetched Content\n")

    fetch_tasks = [fetch_page(u, include_links=False) for u in urls]
    fetched = await asyncio.gather(*fetch_tasks)

    for i, (url, page) in enumerate(zip(urls, fetched), 1):
        if page["success"]:
            lines.append(f"### {i}. {page['title'] or url}")
            lines.append(f"{url}\n")
            content = page["markdown"]
            if len(content) > 4000:
                lines.append(content[:4000])
                lines.append(f"\n... [{len(content) - 4000} more chars]\n")
            else:
                lines.append(content + "\n")
        else:
            lines.append(f"### {i}. Failed to fetch: {url}")
            lines.append(f"Error: {page['error']}\n")

    return "\n".join(lines)


# ── Entry Point ────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Free Search MCP Server")
    parser.add_argument("--transport", choices=["stdio", "sse", "http"], default="stdio")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=3002)
    args = parser.parse_args()

    if args.transport == "stdio":
        mcp.run(transport="stdio")
    elif args.transport == "sse":
        mcp.run(transport="sse", host=args.host, port=args.port)
    elif args.transport == "http":
        mcp.run(transport="streamable-http", host=args.host, port=args.port)


if __name__ == "__main__":
    main()
