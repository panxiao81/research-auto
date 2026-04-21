from __future__ import annotations

import hashlib
import json
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import timedelta
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import arxiv
import psycopg
from bs4 import BeautifulSoup
from bs4.element import Tag

from research_auto.config import get_settings


# arXiv API ToU asks clients to identify themselves and avoid aggressive polling.
# We include a contact channel and enforce rate limits via arxiv.Client settings.
_arxiv_client: arxiv.Client | None = None
_arxiv_client_key: tuple[int, float, int] | None = None
BROWSER_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}


def get_user_agent() -> str:
    return f"research-auto/0.1 ({get_settings().arxiv_contact})"


@dataclass(slots=True)
class ArtifactRecord:
    artifact_kind: str
    label: str | None
    resolution_reason: str | None
    source_url: str
    resolved_url: str | None
    downloadable: bool
    mime_type: str | None = None


@dataclass(slots=True)
class ArxivCandidate:
    title: str
    pdf_url: str
    abs_url: str | None
    doi: str | None
    score: float


def fetch_html(url: str) -> str:
    request = Request(url, headers=BROWSER_HEADERS)
    with urlopen(request, timeout=60) as response:
        return response.read().decode("utf-8", errors="replace")


def resolve_detail_page(detail_url: str) -> list[ArtifactRecord]:
    html = fetch_html(detail_url)
    soup = BeautifulSoup(html, "html.parser")
    artifacts: list[ArtifactRecord] = []

    for anchor in _iter_detail_anchors(soup):
        href = anchor.get("href") or ""
        label = anchor.get_text(" ", strip=True) or None
        onclick = anchor.get("onclick") or ""
        if "downloadlink" in (anchor.get("class") or []):
            download_url = build_download_url(onclick)
            if not download_url:
                continue
            context = anchor.parent.get_text(" ", strip=True) if anchor.parent else ""
            artifacts.append(
                ArtifactRecord(
                    artifact_kind=classify_attachment(label or "", context),
                    label=label,
                    resolution_reason=None,
                    source_url=detail_url,
                    resolved_url=download_url,
                    downloadable=True,
                    mime_type=guess_mime_type(label),
                )
            )
            continue

        if not href or href.startswith("javascript:"):
            continue
        artifact = classify_external_link(href, label)
        if artifact is None:
            continue
        artifacts.append(artifact)

    artifacts.extend(expand_landing_artifacts(artifacts))
    return dedupe_artifacts(artifacts)


def expand_landing_artifacts(artifacts: list[ArtifactRecord]) -> list[ArtifactRecord]:
    expanded: list[ArtifactRecord] = []
    for artifact in artifacts:
        if artifact.artifact_kind not in {"publication", "doi"}:
            continue
        if not artifact.resolved_url:
            continue
        expanded.extend(resolve_landing_page(artifact.resolved_url))
    return expanded


def search_arxiv_fallback(title: str, doi: str | None) -> ArtifactRecord | None:
    candidates: list[ArxivCandidate] = []
    if doi:
        doi_candidates = query_arxiv(
            f'all:"{doi}"', expected_title=title, expected_doi=doi, max_results=5
        )
        candidates.extend(doi_candidates)
        if any(candidate.doi == doi for candidate in doi_candidates):
            return _best_arxiv_artifact(candidates, doi)
    candidates.extend(
        query_arxiv(
            f'ti:"{title}"', expected_title=title, expected_doi=doi, max_results=5
        )
    )
    return _best_arxiv_artifact(candidates, doi)


def _best_arxiv_artifact(
    candidates: list[ArxivCandidate], doi: str | None
) -> ArtifactRecord | None:
    if not candidates:
        return None

    deduped: dict[str, ArxivCandidate] = {}
    for candidate in candidates:
        deduped.setdefault(candidate.pdf_url, candidate)

    best = max(deduped.values(), key=lambda item: item.score, default=None)
    if best is None:
        return None
    if best.score < 0.72 and not (doi and best.doi == doi):
        return None

    return ArtifactRecord(
        artifact_kind="fallback_to_arxiv",
        label=best.abs_url or best.title,
        resolution_reason=None,
        source_url=best.abs_url or best.pdf_url,
        resolved_url=best.pdf_url,
        downloadable=True,
        mime_type="application/pdf",
    )


def apply_arxiv_fallback_reason(
    artifact: ArtifactRecord, reason: str
) -> ArtifactRecord:
    return ArtifactRecord(
        artifact_kind=artifact.artifact_kind,
        label=artifact.label,
        resolution_reason=reason,
        source_url=artifact.source_url,
        resolved_url=artifact.resolved_url,
        downloadable=artifact.downloadable,
        mime_type=artifact.mime_type,
    )


def infer_arxiv_fallback_reason(
    artifacts: list[ArtifactRecord], *, detail_access_failed: bool = False
) -> str:
    if detail_access_failed:
        return "detail_page_access_failed"
    if not artifacts:
        return "no_links_on_detail_page"

    kinds = {artifact.artifact_kind for artifact in artifacts}
    downloadable_pdf_kinds = {
        "direct_pdf",
        "publisher_pdf",
        "preprint",
        "attachment_pdf",
        "fallback_to_arxiv",
    }
    if kinds & {"doi", "publication"} and not kinds & downloadable_pdf_kinds:
        return "landing_page_without_accessible_pdf"
    if kinds <= {"slides", "poster", "attachment_file", "video"}:
        return "non_paper_links_only"
    return "no_accessible_paper_pdf"


def query_arxiv(
    search_query: str,
    *,
    expected_title: str,
    expected_doi: str | None,
    max_results: int,
) -> list[ArxivCandidate]:
    cached_payload = get_cached_arxiv_query(search_query)
    if cached_payload is not None:
        return parse_arxiv_feed(
            cached_payload.encode("utf-8"),
            expected_title=expected_title,
            expected_doi=expected_doi,
        )

    client = get_arxiv_client()
    search = arxiv.Search(query=search_query, max_results=max_results)
    candidates = parse_arxiv_results(
        client.results(search), expected_title=expected_title, expected_doi=expected_doi
    )
    set_cached_arxiv_query(search_query, serialize_arxiv_candidates(candidates))
    return candidates


def get_arxiv_client() -> arxiv.Client:
    global _arxiv_client
    global _arxiv_client_key

    settings = get_settings()
    key = (
        max(1, settings.arxiv_page_size),
        max(0.0, settings.arxiv_delay_seconds),
        max(0, settings.arxiv_num_retries),
    )

    if _arxiv_client is None or _arxiv_client_key != key:
        _arxiv_client = arxiv.Client(
            page_size=key[0],
            delay_seconds=key[1],
            num_retries=key[2],
        )
        _arxiv_client_key = key

    return _arxiv_client


def get_cached_arxiv_query(search_query: str) -> str | None:
    settings = get_settings()
    cleanup_expired_arxiv_cache(settings.database_url)
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                select response_body
                from arxiv_query_cache
                where query_key = %s
                  and expires_at > now()
                """,
                (arxiv_query_key(search_query),),
            )
            row = cur.fetchone()
    return row[0] if row else None


def set_cached_arxiv_query(search_query: str, response_body: str) -> None:
    settings = get_settings()
    expires_in = timedelta(hours=settings.arxiv_cache_ttl_hours)
    with psycopg.connect(settings.database_url) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                insert into arxiv_query_cache (query_key, search_query, response_body, expires_at)
                values (%s, %s, %s, now() + %s)
                on conflict (query_key) do update
                set response_body = excluded.response_body,
                    search_query = excluded.search_query,
                    fetched_at = now(),
                    expires_at = excluded.expires_at
                """,
                (
                    arxiv_query_key(search_query),
                    search_query,
                    response_body,
                    expires_in,
                ),
            )
        conn.commit()


def cleanup_expired_arxiv_cache(database_url: str) -> None:
    # Keep cleanup cheap: only trigger occasionally during normal use.
    if int(time.time()) % 17 != 0:
        return
    with psycopg.connect(database_url) as conn:
        with conn.cursor() as cur:
            cur.execute("delete from arxiv_query_cache where expires_at <= now()")
        conn.commit()


def arxiv_query_key(search_query: str) -> str:
    return hashlib.sha256(search_query.encode("utf-8")).hexdigest()


def parse_arxiv_feed(
    payload: bytes, *, expected_title: str, expected_doi: str | None
) -> list[ArxivCandidate]:
    if payload.startswith(b"["):
        return parse_arxiv_json_cache(
            payload, expected_title=expected_title, expected_doi=expected_doi
        )

    root = ET.fromstring(payload)
    ns = {"atom": "http://www.w3.org/2005/Atom"}
    expected_normalized = normalize_for_match(expected_title)
    results: list[ArxivCandidate] = []

    for entry in root.findall("atom:entry", ns):
        title = (entry.findtext("atom:title", default="", namespaces=ns) or "").strip()
        if not title:
            continue
        pdf_url = None
        abs_url = entry.findtext("atom:id", default=None, namespaces=ns)
        doi = None

        for link in entry.findall("atom:link", ns):
            if link.get("title") == "pdf":
                pdf_url = link.get("href")
            elif link.get("title") == "doi":
                doi_href = link.get("href")
                doi = extract_doi(doi_href)

        if not pdf_url:
            continue

        normalized_title = normalize_for_match(title)
        score = title_similarity(expected_normalized, normalized_title)
        if expected_doi and doi == expected_doi:
            score = max(score, 0.99)

        results.append(
            ArxivCandidate(
                title=title,
                pdf_url=normalize_arxiv_url(pdf_url),
                abs_url=abs_url,
                doi=doi,
                score=score,
            )
        )

    return results


def parse_arxiv_results(
    results: Any, *, expected_title: str, expected_doi: str | None
) -> list[ArxivCandidate]:
    expected_normalized = normalize_for_match(expected_title)
    parsed: list[ArxivCandidate] = []

    for result in results:
        title = (result.title or "").strip()
        if not title:
            continue
        pdf_url = (
            normalize_arxiv_url(str(result.pdf_url))
            if getattr(result, "pdf_url", None)
            else None
        )
        if not pdf_url:
            continue
        doi = _extract_doi_from_result(result)
        normalized_title = normalize_for_match(title)
        score = title_similarity(expected_normalized, normalized_title)
        if expected_doi and doi == expected_doi:
            score = max(score, 0.99)

        parsed.append(
            ArxivCandidate(
                title=title,
                pdf_url=pdf_url,
                abs_url=str(getattr(result, "entry_id", None))
                if getattr(result, "entry_id", None)
                else None,
                doi=doi,
                score=score,
            )
        )

    return parsed


def _extract_doi_from_result(result: Any) -> str | None:
    doi = getattr(result, "doi", None)
    if doi:
        return str(doi)
    links = getattr(result, "links", None) or []
    for link in links:
        title = str(getattr(link, "title", "") or "").lower()
        href = str(getattr(link, "href", "") or "")
        if title == "doi" and href:
            extracted = extract_doi(href)
            if extracted:
                return extracted
    return None


def serialize_arxiv_candidates(candidates: list[ArxivCandidate]) -> str:
    payload = [
        {
            "title": candidate.title,
            "pdf_url": candidate.pdf_url,
            "abs_url": candidate.abs_url,
            "doi": candidate.doi,
            "score": candidate.score,
        }
        for candidate in candidates
    ]
    return json.dumps(payload, ensure_ascii=False)


def parse_arxiv_json_cache(
    payload: bytes, *, expected_title: str, expected_doi: str | None
) -> list[ArxivCandidate]:
    try:
        data = json.loads(payload.decode("utf-8", errors="replace"))
    except json.JSONDecodeError:
        return []

    expected_normalized = normalize_for_match(expected_title)
    results: list[ArxivCandidate] = []
    for row in data:
        title = str(row.get("title", "") or "").strip()
        pdf_url = str(row.get("pdf_url", "") or "").strip()
        abs_url = row.get("abs_url")
        doi = row.get("doi")
        cached_score = row.get("score")
        if not title or not pdf_url:
            continue

        normalized_title = normalize_for_match(title)
        score = (
            float(cached_score)
            if isinstance(cached_score, (float, int))
            else title_similarity(expected_normalized, normalized_title)
        )
        if expected_doi and doi == expected_doi:
            score = max(score, 0.99)

        results.append(
            ArxivCandidate(
                title=title,
                pdf_url=normalize_arxiv_url(pdf_url),
                abs_url=str(abs_url) if abs_url else None,
                doi=str(doi) if doi else None,
                score=score,
            )
        )
    return results


def resolve_landing_page(url: str) -> list[ArtifactRecord]:
    parsed = urlparse(url)
    host = parsed.netloc.lower()
    if host.endswith("doi.org"):
        return resolve_doi_landing(url)
    if "link.springer.com" in host or "springer.com" in host:
        return resolve_generic_html_landing(url, source_kind="publication")
    if "openreview.net" in host:
        return resolve_openreview_landing(url)
    return resolve_generic_html_landing(url, source_kind="publication")


def resolve_doi_landing(url: str) -> list[ArtifactRecord]:
    doi = extract_doi(url)
    try:
        final_url, _html = fetch_html_with_final_url(url)
    except HTTPError:
        return heuristic_doi_artifacts(doi)
    except Exception:
        return heuristic_doi_artifacts(doi)
    if final_url == url:
        return heuristic_doi_artifacts(doi)
    return resolve_landing_page(final_url)


def fetch_html_with_final_url(url: str) -> tuple[str, str]:
    request = Request(url, headers=BROWSER_HEADERS)
    with urlopen(request, timeout=60) as response:
        html = response.read().decode("utf-8", errors="replace")
        return response.geturl(), html


def resolve_generic_html_landing(url: str, *, source_kind: str) -> list[ArtifactRecord]:
    try:
        final_url, html = fetch_html_with_final_url(url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    artifacts: list[ArtifactRecord] = []

    meta_pdf = soup.select_one('meta[name="citation_pdf_url"]')
    if meta_pdf and meta_pdf.get("content"):
        artifacts.append(
            ArtifactRecord(
                artifact_kind="publisher_pdf",
                label="citation_pdf_url",
                resolution_reason=None,
                source_url=final_url,
                resolved_url=absolutize_url(final_url, meta_pdf.get("content")),
                downloadable=True,
                mime_type="application/pdf",
            )
        )

    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        text = anchor.get_text(" ", strip=True)
        href_lower = href.lower()
        text_lower = text.lower()
        if not href:
            continue
        if (
            href_lower.endswith(".pdf")
            or "download pdf" in text_lower
            or text_lower == "pdf"
        ):
            artifacts.append(
                ArtifactRecord(
                    artifact_kind="publisher_pdf",
                    label=text or None,
                    resolution_reason=None,
                    source_url=final_url,
                    resolved_url=absolutize_url(final_url, href),
                    downloadable=True,
                    mime_type="application/pdf",
                )
            )

    return dedupe_artifacts(artifacts)


def resolve_openreview_landing(url: str) -> list[ArtifactRecord]:
    try:
        final_url, html = fetch_html_with_final_url(url)
    except Exception:
        return []

    soup = BeautifulSoup(html, "html.parser")
    artifacts: list[ArtifactRecord] = []
    for anchor in soup.select("a[href]"):
        href = anchor.get("href") or ""
        text = anchor.get_text(" ", strip=True)
        if not href:
            continue
        if (
            href.startswith("/pdf?")
            or text.lower() == "pdf"
            or href.lower().endswith(".pdf")
        ):
            artifacts.append(
                ArtifactRecord(
                    artifact_kind="publisher_pdf",
                    label=text or None,
                    resolution_reason=None,
                    source_url=final_url,
                    resolved_url=absolutize_url(final_url, href),
                    downloadable=True,
                    mime_type="application/pdf",
                )
            )
    return dedupe_artifacts(artifacts)


def heuristic_doi_artifacts(doi: str | None) -> list[ArtifactRecord]:
    if not doi:
        return []
    artifacts: list[ArtifactRecord] = []
    if doi.startswith("10.1145/"):
        artifacts.append(
            ArtifactRecord(
                artifact_kind="publication",
                label="ACM landing",
                resolution_reason=None,
                source_url=f"https://doi.org/{doi}",
                resolved_url=f"https://dl.acm.org/doi/{doi}",
                downloadable=False,
                mime_type=None,
            )
        )
    elif doi.startswith("10.1007/"):
        springer_url = f"https://link.springer.com/article/{doi}"
        artifacts.append(
            ArtifactRecord(
                artifact_kind="publication",
                label="Springer landing",
                resolution_reason=None,
                source_url=f"https://doi.org/{doi}",
                resolved_url=springer_url,
                downloadable=False,
                mime_type=None,
            )
        )
        artifacts.extend(
            resolve_generic_html_landing(springer_url, source_kind="publication")
        )
    return dedupe_artifacts(artifacts)


def _iter_detail_anchors(soup: BeautifulSoup) -> list[Tag]:
    title = soup.find("h2")
    if title is None:
        return list(soup.select("a[href]"))

    anchors: list[Tag] = []
    seen: set[int] = set()
    for element in title.next_elements:
        if isinstance(element, Tag):
            if element.get_text(" ", strip=True) == "Session Program":
                break
            if element.name == "a" and element.get("href"):
                marker = id(element)
                if marker in seen:
                    continue
                seen.add(marker)
                anchors.append(element)
    return anchors


def build_download_url(onclick: str) -> str | None:
    match = re.search(
        r'serverInvokeDownloadCompatible\("([^"]+)","([^"]+)",\s*\[(.*?)\],"([^"]*)",\s*this,\s*"(\d+)"\)',
        onclick,
    )
    if not match:
        return None

    base_url, action_key, raw_params, _, _ = match.groups()
    params: list[tuple[str, str]] = [
        ("action-call-with-get-request-type", "1"),
        (action_key, "1"),
        ("__ajax_runtime_request__", "1"),
    ]
    for name, value in re.findall(
        r'"name"\s*:\s*"([^"]+)",\s*"value"\s*:\s*"([^"]*)"', raw_params
    ):
        params.append((name, value))
    return f"{base_url}?{urlencode(params)}"


def classify_attachment(label: str, context: str) -> str:
    haystack = f"{label} {context}".lower()
    if any(token in haystack for token in ["presentation", "slides", "slide", "ppt"]):
        return "slides"
    if "poster" in haystack:
        return "poster"
    if label.lower().endswith(".pdf"):
        return "attachment_pdf"
    return "attachment_file"


def classify_external_link(href: str, label: str | None) -> ArtifactRecord | None:
    parsed = urlparse(href)
    host = parsed.netloc.lower()
    lower_label = "" if looks_like_url(label) else (label or "").lower()

    if "youtube.com" in host or "youtube-nocookie.com" in host:
        return ArtifactRecord("video", label, None, href, href, False, None)
    if "arxiv.org" in host:
        return ArtifactRecord(
            "preprint",
            label,
            None,
            href,
            normalize_arxiv_url(href),
            True,
            "application/pdf",
        )
    if host.endswith("doi.org") or lower_label == "doi":
        return ArtifactRecord("doi", label, None, href, href, False, None)
    if "publication" in lower_label:
        return ArtifactRecord("publication", label, None, href, href, False, None)
    if href.lower().endswith(".pdf"):
        return ArtifactRecord(
            "direct_pdf", label, None, href, href, True, "application/pdf"
        )
    if lower_label in {"pre-print", "preprint"}:
        return ArtifactRecord("preprint", label, None, href, href, False, None)
    return None


def normalize_arxiv_url(url: str) -> str:
    match = re.search(r"arxiv\.org/(?:abs|pdf)/([^?#/]+)", url)
    if not match:
        return url
    paper_id = match.group(1).removesuffix(".pdf")
    return f"https://arxiv.org/pdf/{paper_id}.pdf"


def normalize_for_match(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def title_similarity(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    left_tokens = set(left.split())
    right_tokens = set(right.split())
    if not left_tokens or not right_tokens:
        return 0.0
    overlap = len(left_tokens & right_tokens) / max(len(left_tokens), len(right_tokens))
    exact_bonus = 1.0 if left == right else 0.0
    return max(overlap, exact_bonus)


def dedupe_artifacts(artifacts: list[ArtifactRecord]) -> list[ArtifactRecord]:
    seen: set[tuple[str, str, str | None]] = set()
    result: list[ArtifactRecord] = []
    for artifact in artifacts:
        key = (artifact.artifact_kind, artifact.source_url, artifact.resolved_url)
        if key in seen:
            continue
        seen.add(key)
        result.append(artifact)
    return result


def pick_best_urls(artifacts: list[ArtifactRecord]) -> tuple[str | None, str | None]:
    pdf_priority = [
        "direct_pdf",
        "publisher_pdf",
        "preprint",
        "fallback_to_arxiv",
        "attachment_pdf",
    ]
    landing_priority = ["publication", "doi"]
    best_pdf = next(
        (
            a.resolved_url
            for kind in pdf_priority
            for a in artifacts
            if a.artifact_kind == kind and a.resolved_url
        ),
        None,
    )
    best_landing = next(
        (
            a.resolved_url
            for kind in landing_priority
            for a in artifacts
            if a.artifact_kind == kind and a.resolved_url
        ),
        None,
    )
    return best_pdf, best_landing


def download_artifact(url: str, label: str | None) -> dict[str, Any]:
    preferred_label = None if looks_like_url(label) else label
    file_name = safe_file_name(
        preferred_label or Path(urlparse(url).path).name or "artifact.bin"
    )
    request = Request(url, headers={"User-Agent": get_user_agent()})
    with urlopen(request, timeout=120) as response:
        payload = response.read()
        content_type = response.headers.get_content_type()

    checksum = hashlib.sha256(payload).hexdigest()
    return {
        "content": payload,
        "file_name": file_name,
        "checksum_sha256": checksum,
        "byte_size": len(payload),
        "mime_type": content_type,
    }


def guess_mime_type(label: str | None) -> str | None:
    if label and label.lower().endswith(".pdf"):
        return "application/pdf"
    return None


def safe_file_name(file_name: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9._-]+", "-", file_name).strip("-")
    return sanitized or "artifact.bin"


def looks_like_url(value: str | None) -> bool:
    if not value:
        return False
    return value.startswith("http://") or value.startswith("https://")


def extract_doi(url: str | None) -> str | None:
    if not url:
        return None
    match = re.search(r"(?:doi\.org/)?(10\.\d{4,9}/[^?#\s]+)", url)
    if not match:
        return None
    return match.group(1)


def absolutize_url(base_url: str, href: str | None) -> str | None:
    if not href:
        return None
    return urljoin(base_url, unescape(href))
