from __future__ import annotations
import re
import requests
from urllib.parse import urlparse, urlunparse, parse_qsl, urlencode
from typing import Any, Dict, List, Tuple

from app.config.settings import settings
from app.shared.utils import get_logger

_LOG = get_logger(__name__)

def _canonicalize_url(url: str) -> str:
    u = (url or "").strip()
    if not u: return ""
    try:
        p = urlparse(u)
        query = [(k, v) for (k, v) in parse_qsl(p.query, keep_blank_values=True) if not k.lower().startswith("utm_")]
        clean = p._replace(fragment="", query=urlencode(query, doseq=True))
        return urlunparse(clean)
    except Exception: return u

def _domain_from_url(url: str) -> str:
    try:
        host = (urlparse(url).netloc or "").lower()
        if host.startswith("www."): host = host[4:]
        return host
    except Exception: return ""

def _trust_score(url: str, *, domain_counts: Dict[str, int]) -> float:
    domain = _domain_from_url(url)
    base = 0.45
    if not domain: base = 0.3
    elif domain.endswith(".gov"): base = 0.95
    elif domain.endswith(".edu"): base = 0.90
    elif domain in {"wikipedia.org", "en.wikipedia.org"}: base = 0.85
    elif domain.endswith(".org"): base = 0.65
    rep = domain_counts.get(domain, 1)
    boost = min(0.1, 0.03 * max(0, rep - 1))
    return float(max(0.0, min(1.0, base + boost)))

def search_duckduckgo(query: str, max_results: int) -> List[Dict[str, str]]:
    try:
        from duckduckgo_search import DDGS
        links: List[Dict[str, str]] = []
        with DDGS() as ddgs:
            results = list(ddgs.text(query, max_results=max_results))
            for r in results:
                link = (r.get("href") or r.get("url") or "").strip()
                if not link: continue
                title = (r.get("title") or "Untitled").strip() or "Untitled"
                links.append({"title": title, "link": link})
        return links
    except Exception: return []

def clean_text(text: str) -> str:
    import bleach
    from bs4 import BeautifulSoup
    
    # 1. Use BeautifulSoup to get clean text and handle basic HTML entities
    soup = BeautifulSoup(text, "html.parser")
    # Remove script and style elements
    for script in soup(["script", "style"]):
        script.decompose()
    
    raw_text = soup.get_text(separator=' ')
    
    # 2. Use bleach for another layer of sanitization (no tags allowed)
    safe_text = bleach.clean(raw_text, tags=[], attributes={}, strip=True)
    
    # 3. Normalize whitespace
    cleaned = re.sub(r'\s+', ' ', safe_text)
    
    # 4. Strip malicious instruction patterns (Injection Defense)
    from app.security.prompt_guard import _INJECTION_PATTERNS
    for pattern in _INJECTION_PATTERNS:
        cleaned = re.sub(pattern, '[REDACTED]', cleaned)
    
    return cleaned.strip()

async def extract_main_text(url: str, max_chars: int) -> str:
    try:
        from bs4 import BeautifulSoup
        import httpx
        headers = {"User-Agent": "Mozilla/5.0 (compatible; LocalAIAssistant/1.0; +https://localhost)"}
        async with httpx.AsyncClient(timeout=12.0) as client:
            response = await client.get(url, headers=headers)
            response.raise_for_status()
            soup = BeautifulSoup(response.text, "html.parser")
            paragraphs = soup.find_all("p")
            full_text = ' '.join([clean_text(p.get_text()) for p in paragraphs])
            return full_text[:max_chars]
    except Exception: return ""

async def get_web_context(
    query: str,
    *,
    max_results: int = 5,
    page_max_chars: int = 1800,
    context_max_chars: int = 4000,
) -> Tuple[str, List[Dict[str, str]]]:
    q = (query or "").strip()
    if not q or max_results <= 0: return "", []
    links = search_duckduckgo(q, max_results=max_results)
    if not links: return "", []

    cleaned: List[Dict[str, str]] = []
    seen: set[str] = set()
    for e in links:
        link = _canonicalize_url(e.get("link") or "")
        if not link or link in seen: continue
        seen.add(link)
        cleaned.append({"title": (e.get("title") or "Untitled").strip(), "link": link})

    domain_counts: Dict[str, int] = {}
    for e in cleaned:
        d = _domain_from_url(e["link"])
        if d: domain_counts[d] = domain_counts.get(d, 0) + 1
    for e in cleaned:
        e["trust"] = _trust_score(e["link"], domain_counts=domain_counts)
    cleaned.sort(key=lambda e: float(e.get("trust") or 0.0), reverse=True)
    links = cleaned[:max_results]

    texts: List[str] = []
    for entry in links:
        content = await extract_main_text(entry["link"], max_chars=page_max_chars)
        if content: texts.append(content)
        if len(" ".join(texts)) >= context_max_chars: break
    return clean_text(" ".join(texts))[:context_max_chars], links
