"""Content extraction from URLs — httpx + BeautifulSoup + markdownify."""

from __future__ import annotations

import re
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from markdownify import markdownify as md

DEFAULT_TIMEOUT = 15.0
MAX_CONTENT_LENGTH = 80_000

# Domains that block simple scraping
BLOCKED_PATTERNS = [
    r"^https?://(www\.)?youtube\.com",
    r"^https?://(www\.)?facebook\.com",
    r"^https?://(www\.)?instagram\.com",
    r"^https?://(www\.)?twitter\.com",
    r"^https?://(www\.)?x\.com",
    r"^https?://(www\.)?linkedin\.com",
]

# Selectors to remove before extraction
NOISE_SELECTORS = [
    "nav", "header", "footer", "aside", ".sidebar", ".advertisement",
    ".ads", ".cookie-banner", ".newsletter", "script", "style", "noscript",
    "iframe", ".social-share", ".comments", "[role='banner']", "[role='navigation']",
]


def is_blocked(url: str) -> bool:
    return any(re.search(p, url, re.I) for p in BLOCKED_PATTERNS)


def _extract_title(soup: BeautifulSoup) -> str:
    for selector in ["h1", "title", "meta[property='og:title']"]:
        el = soup.select_one(selector)
        if el:
            return el.get_text(strip=True) or el.get("content", "")
    return ""


def _extract_description(soup: BeautifulSoup) -> str:
    for selector in [
        "meta[name='description']",
        "meta[property='og:description']",
    ]:
        el = soup.select_one(selector)
        if el:
            return el.get("content", "")
    return ""


def _clean_soup(soup: BeautifulSoup) -> None:
    for sel in NOISE_SELECTORS:
        for tag in soup.select(sel):
            tag.decompose()


def _normalize_url(url: str, base: str) -> str:
    if url.startswith(("http://", "https://")):
        return url
    return urljoin(base, url)


def _is_internal_link(href: str, base_netloc: str) -> bool:
    parsed = urlparse(href)
    return parsed.netloc == "" or parsed.netloc == base_netloc


async def fetch_page(
    url: str,
    timeout: float = DEFAULT_TIMEOUT,
    max_length: int = MAX_CONTENT_LENGTH,
    include_links: bool = False,
) -> dict:
    """Fetch a URL and extract structured content.

    Returns dict with keys: url, title, description, markdown, success, error.
    """
    if is_blocked(url):
        return {
            "url": url,
            "title": "",
            "description": "",
            "markdown": "",
            "success": False,
            "error": "Domain blocks automated fetching.",
        }

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
    }

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(timeout), follow_redirects=True) as client:
            resp = await client.get(url, headers=headers)
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "").lower()
            if "application/pdf" in content_type:
                return {
                    "url": url,
                    "title": "PDF Document",
                    "description": "",
                    "markdown": f"[PDF document: {url}]",
                    "success": True,
                    "error": None,
                }

            html = resp.text
            soup = BeautifulSoup(html, "lxml")

            title = _extract_title(soup)
            description = _extract_description(soup)
            base_netloc = urlparse(url).netloc

            # Clean noise
            _clean_soup(soup)

            # Extract main content
            main = soup.select_one("main, article, [role='main'], .content, .post, .entry")
            if not main:
                main = soup.body or soup

            # Convert to markdown
            markdown = md(str(main), heading_style="ATX", strip=["a"] if not include_links else [])
            markdown = re.sub(r"\n{3,}", "\n\n", markdown).strip()

            # Truncate if too long
            if len(markdown) > max_length:
                markdown = markdown[:max_length] + f"\n\n[Content truncated at {max_length} chars]"

            # Extract links if requested
            links: list[dict] = []
            if include_links:
                for a in main.find_all("a", href=True):
                    href = _normalize_url(a["href"], url)
                    if _is_internal_link(href, base_netloc):
                        links.append({"text": a.get_text(strip=True)[:80], "url": href})

            return {
                "url": url,
                "title": title,
                "description": description,
                "markdown": markdown,
                "links": links if include_links else None,
                "success": True,
                "error": None,
            }

    except httpx.TimeoutException:
        return {"url": url, "title": "", "description": "", "markdown": "", "success": False, "error": "Request timed out."}
    except httpx.HTTPStatusError as e:
        return {"url": url, "title": "", "description": "", "markdown": "", "success": False, "error": f"HTTP {e.response.status_code}"}
    except Exception as exc:
        return {"url": url, "title": "", "description": "", "markdown": "", "success": False, "error": f"{type(exc).__name__}: {exc}"}
