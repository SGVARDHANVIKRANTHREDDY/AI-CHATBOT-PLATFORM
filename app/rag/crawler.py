from __future__ import annotations
import asyncio
import time
from typing import List, Dict, Any, Optional, Set
from dataclasses import dataclass, field
from app.shared.utils import get_logger
from app.security.content_safety import ContentSafetyFilter

_LOG = get_logger(__name__)

# ─── Rate & Safety Limits ─────────────────────────────────────────
DEFAULT_CRAWL_FREQUENCY_SECONDS = 3600  # 1 hour between crawls
DEFAULT_DOCUMENT_SIZE_LIMIT = 500_000   # 500KB max per document
DEFAULT_SOURCE_WHITELIST: List[str] = [
    "docs.python.org",
    "arxiv.org",
    "github.com",
    "en.wikipedia.org",
    "redis.io",
    "fastapi.tiangolo.com",
]

@dataclass
class CrawlConfig:
    """Configuration for knowledge crawling with safety limits."""
    crawl_frequency: int = DEFAULT_CRAWL_FREQUENCY_SECONDS
    source_whitelist: List[str] = field(default_factory=lambda: DEFAULT_SOURCE_WHITELIST.copy())
    document_size_limit: int = DEFAULT_DOCUMENT_SIZE_LIMIT
    max_pages_per_crawl: int = 20
    enabled_sources: List[str] = field(default_factory=lambda: ["rss", "docs"])


class KnowledgeCrawler:
    """
    Crawls, validates, and ingests knowledge from allowed sources.
    Respects rate limits and content size restrictions.
    """
    
    def __init__(self, config: Optional[CrawlConfig] = None):
        self.config = config or CrawlConfig()
        self._last_crawl: Dict[str, float] = {}  # source -> timestamp
        self._crawled_urls: Set[str] = set()
        self.safety_filter = ContentSafetyFilter()

    def _is_whitelisted(self, url: str) -> bool:
        """Only crawl from approved domains."""
        return any(domain in url for domain in self.config.source_whitelist)

    def _is_rate_limited(self, source: str) -> bool:
        """Returns True if we should skip this source due to frequency limits."""
        last = self._last_crawl.get(source, 0)
        return (time.time() - last) < self.config.crawl_frequency

    async def crawl_url(self, url: str) -> Optional[Dict[str, Any]]:
        """Crawl a single URL with safety checks."""
        if not self._is_whitelisted(url):
            _LOG.warning(f"URL not whitelisted, skipping: {url}")
            return None
            
        if url in self._crawled_urls:
            _LOG.debug(f"Already crawled: {url}")
            return None
            
        domain = url.split("/")[2] if "/" in url else url
        if self._is_rate_limited(domain):
            _LOG.info(f"Rate limited for domain: {domain}")
            return None

        _LOG.info(f"Crawling: {url}")
        try:
            # Simulated crawl — in production, use aiohttp
            content = f"[Simulated content from {url}]"
            
            if len(content) > self.config.document_size_limit:
                _LOG.warning(f"Document too large ({len(content)} bytes): {url}")
                content = content[:self.config.document_size_limit]
            
            self._crawled_urls.add(url)
            self._last_crawl[domain] = time.time()
            
            return {
                "url": url,
                "content": content,
                "domain": domain,
                "size": len(content),
                "timestamp": time.time()
            }
        except Exception as e:
            _LOG.error(f"Crawl failed for {url}: {e}")
            return None

    async def crawl_batch(self, urls: List[str]) -> List[Dict[str, Any]]:
        """Crawl multiple URLs with limits and safety filtering."""
        results = []
        for url in urls[:self.config.max_pages_per_crawl]:
            result = await self.crawl_url(url)
            if result:
                doc = {"text": result["content"], "source": result["url"], "domain": result["domain"]}
                verdict = self.safety_filter.scan(doc)
                if verdict.rejected:
                    _LOG.warning("Crawled doc rejected by safety filter: %s", url)
                    continue
                if verdict.quarantined:
                    _LOG.warning("Crawled doc quarantined: %s", url)
                    continue
                results.append(result)
        return results
