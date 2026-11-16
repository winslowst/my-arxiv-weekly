"""Read-only journal metadata sources for the weekly email report.

Fetches metadata only (title / authors / journal / DOI / publication date):
no full text, no bulk harvesting, no repository writes. Uses ``requests``
(already in requirements.txt) and the Python standard library only.

Sources:
  * OpenAlex  -- free API key recommended since 2026-02 (polite pool removed)
  * Elsevier Scopus Search -- optional, needs APIKey + institutional entitlement

A failed source raises ``SourceUnavailable`` so the weekly report can skip it
and still send the email with the remaining sources.
"""

import logging
import re
import time
from datetime import timedelta

try:
    import requests
except ImportError:
    # CI installs requirements.txt; a local dry-run may not have it. Treat the
    # journal sources as unavailable instead of breaking the whole report.
    requests = None

OPENALEX_URL = "https://api.openalex.org/works"
SCOPUS_URL = "https://api.elsevier.com/content/search/scopus"

log = logging.getLogger(__name__)


class SourceUnavailable(Exception):
    """One data source failed; the report should skip it, not abort."""


def normalize_title(title):
    """Lowercase and strip everything but letters/digits/CJK for dedup."""
    return re.sub(r"[^a-z0-9\u4e00-\u9fff]+", "", (title or "").lower())


def format_authors(names):
    names = [n for n in (names or []) if n]
    if not names:
        return "Unknown"
    return names[0] if len(names) == 1 else names[0] + " et.al."


def parse_date(value):
    match = re.search(r"\d{4}-\d{2}-\d{2}", str(value or ""))
    return match.group(0) if match else None


def clean_doi(value):
    match = re.search(r"10\.\d{4,9}/\S+", str(value or ""))
    return match.group(0).rstrip(".,);").lower() if match else None


def dedup_key(record):
    """DOI wins, then arXiv id, then a normalized title."""
    doi = (record.get("doi") or "").lower()
    if doi:
        return "doi:" + doi
    arxiv_id = (record.get("arxiv_id") or "").lower()
    if arxiv_id:
        return "arxiv:" + arxiv_id
    return "title:" + normalize_title(record.get("title"))


def make_record(source, topic, title, authors, date_str, date_basis, url,
                journal=None, doi=None, arxiv_id=None, code_url=None):
    record = {
        "source": source,
        "topic": topic,
        "title": (title or "").strip(),
        "authors": authors,
        "date": date_str,
        "date_basis": date_basis,
        "journal": journal,
        "doi": doi,
        "arxiv_id": arxiv_id,
        "code_url": code_url,
        "url": url or ("https://doi.org/" + doi if doi else ""),
    }
    record["dedup_key"] = dedup_key(record)
    return record


def _window_filter(start, end):
    """OpenAlex / Scopus date filters are inclusive; the report window is not."""
    return start.isoformat(), (end - timedelta(days=1)).isoformat()


def fetch_openalex(query, start, end, topic, api_key=None,
                   max_results=30, timeout=20):
    """Search OpenAlex works published inside [start, end)."""
    if requests is None:
        raise SourceUnavailable("requests is not installed (pip install -r requirements.txt)")
    if end <= start:
        return []
    from_date, to_date = _window_filter(start, end)
    params = {
        "search": query,
        "filter": "from_publication_date:%s,to_publication_date:%s" % (from_date, to_date),
        "per_page": max(1, min(int(max_results), 100)),
        # relevance_score is only meaningful together with `search`; the
        # publication-date window above still bounds the result set.
        "sort": "relevance_score:desc",
    }
    if api_key:
        # never log params: they may contain the api key
        params["api_key"] = api_key
    try:
        response = requests.get(OPENALEX_URL, params=params, timeout=timeout,
                                 headers={"Accept": "application/json"})
    except requests.RequestException as exc:
        raise SourceUnavailable("OpenAlex request failed (%s)" % type(exc).__name__) from None
    if response.status_code == 429:
        raise SourceUnavailable("OpenAlex rate limited (HTTP 429)")
    if response.status_code >= 400:
        raise SourceUnavailable("OpenAlex HTTP %s" % response.status_code)
    try:
        payload = response.json()
    except ValueError:
        raise SourceUnavailable("OpenAlex returned non-JSON") from None

    records = []
    for work in payload.get("results", []) or []:
        date_str = parse_date(work.get("publication_date"))
        if not date_str or not (start.isoformat() <= date_str < end.isoformat()):
            continue
        location = work.get("primary_location") or {}
        journal = (location.get("source") or {}).get("display_name")
        authors = [(a.get("author") or {}).get("display_name")
                   for a in (work.get("authorships") or [])]
        doi = clean_doi(work.get("doi"))
        url = location.get("landing_page_url") or work.get("doi") or work.get("id") or ""
        records.append(make_record(
            "openalex", topic, work.get("title") or work.get("display_name"),
            format_authors(authors), date_str, "publication_date", url,
            journal=journal, doi=doi))
    return records


def fetch_elsevier(query, start, end, topic, api_key=None,
                   max_results=30, timeout=20, view="BASIC"):
    """Search Elsevier Scopus metadata published inside [start, end).

    Scopus needs an APIKey and usually an institutional entitlement; without
    either it may return only a BASIC view. This source is OFF by default.
    """
    if requests is None:
        raise SourceUnavailable("requests is not installed (pip install -r requirements.txt)")
    if not api_key:
        raise SourceUnavailable("ELSEVIER_API_KEY not set")
    if end <= start:
        return []
    params = {
        "query": 'TITLE-ABS-KEY("%s")' % query,
        "view": view,
        "count": max(1, min(int(max_results), 200)),
    }
    headers = {"X-ELS-APIKey": api_key, "Accept": "application/json"}

    response = None
    for attempt in range(3):
        try:
            response = requests.get(SCOPUS_URL, params=params, headers=headers,
                                    timeout=timeout)
        except requests.RequestException as exc:
            raise SourceUnavailable("Elsevier request failed (%s)" % type(exc).__name__) from None
        if response.status_code == 429 and attempt < 2:
            try:
                delay = min(float(response.headers.get("Retry-After", 2)), 10.0)
            except (TypeError, ValueError):
                delay = 2.0
            time.sleep(delay)
            continue
        break
    if response is None:
        raise SourceUnavailable("Elsevier no response")
    if response.status_code == 429:
        raise SourceUnavailable("Elsevier rate limited (HTTP 429)")
    if response.status_code >= 400:
        raise SourceUnavailable("Elsevier HTTP %s" % response.status_code)
    try:
        payload = response.json()
    except ValueError:
        raise SourceUnavailable("Elsevier returned non-JSON") from None

    records = []
    entries = (payload.get("search-results") or {}).get("entry") or []
    for entry in entries:
        date_str = parse_date(entry.get("prism:coverDate"))
        if not date_str or not (start.isoformat() <= date_str < end.isoformat()):
            continue
        doi = clean_doi(entry.get("prism:doi"))
        url = entry.get("prism:url") or ("https://doi.org/" + doi if doi else "")
        records.append(make_record(
            "elsevier", topic, entry.get("dc:title"),
            format_authors([entry.get("dc:creator")]), date_str, "coverDate", url,
            journal=entry.get("prism:publicationName"), doi=doi))
    return records
