from __future__ import annotations

import asyncio
import hashlib
import json

from playwright.async_api import async_playwright

from research_auto.domain.records import AuthorCandidate, CrawlResult, PaperCandidate


async def crawl_track(
    track_url: str, *, headless: bool = True
) -> tuple[CrawlResult, str]:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(headless=headless)
        page = await browser.new_page()
        await page.goto(track_url, wait_until="domcontentloaded")
        await page.wait_for_load_state("networkidle")

        raw_candidates = await _extract_accepted_papers(page)
        html = await page.content()
        paper_candidates: list[PaperCandidate] = []
        seen_titles: set[str] = set()
        for item in raw_candidates:
            title = (item.get("title") or "").strip()
            if not title:
                continue
            normalized = normalize_title(title)
            if normalized in seen_titles:
                continue
            seen_titles.add(normalized)
            paper_candidates.append(
                PaperCandidate(
                    title=title,
                    detail_url=item.get("detail_url"),
                    pdf_url=item.get("pdf_url"),
                    abstract=item.get("abstract"),
                    session_name=item.get("session_name"),
                    authors=[
                        AuthorCandidate(name=name)
                        for name in item.get("authors", [])
                        if name
                    ],
                )
            )

        await browser.close()
        return CrawlResult(
            discovered=len(paper_candidates), paper_candidates=paper_candidates
        ), html


async def _extract_accepted_papers(page) -> list[dict[str, object]]:
    accepted = page.locator("h3", has_text="Accepted Papers").first
    table = accepted.locator("xpath=following-sibling::*[1]").first
    rows = table.locator("tbody tr")
    row_count = await rows.count()
    candidates: list[dict[str, object]] = []

    for index in range(row_count):
        row = rows.nth(index)
        trigger = row.locator("[data-event-modal]").first
        if await trigger.count() == 0:
            continue
        event_id = await trigger.get_attribute("data-event-modal")
        if not event_id:
            continue
        authors = await row.locator('a[href*="/profile/"]').evaluate_all(
            "elements => elements.map(el => el.textContent?.trim()).filter(Boolean)"
        )
        row_links = await row.locator("a[href]").evaluate_all(
            "elements => elements.map(el => ({ text: (el.textContent || '').trim(), href: el.href }))"
        )
        await trigger.click()
        modal = page.locator(f"#modal-{event_id}")
        await modal.wait_for(state="visible", timeout=10000)
        title = await _safe_text(
            modal.locator(".event-title strong").first
        ) or await _safe_text(trigger)
        paragraphs = await modal.locator(".modal-body p").all_text_contents()
        abstract = (
            "\n\n".join(text.strip() for text in paragraphs if text.strip()) or None
        )
        session_name = await _safe_text(modal.locator(".modal-header a.navigate").first)
        modal_links = await modal.locator("a[href]").evaluate_all(
            "elements => elements.map(el => ({ text: (el.textContent || '').trim(), href: el.href }))"
        )
        detail_url = next(
            (item["href"] for item in modal_links if "/details/" in item["href"]), None
        )
        pdf_url = next(
            (
                item["href"]
                for item in [*row_links, *modal_links]
                if item["href"].endswith(".pdf")
            ),
            None,
        )
        candidates.append(
            {
                "title": title,
                "authors": authors,
                "detail_url": detail_url,
                "pdf_url": pdf_url,
                "abstract": abstract,
                "session_name": session_name,
                "event_id": event_id,
            }
        )
        close_button = modal.locator(".close").first
        if await close_button.count() > 0:
            await close_button.click()
            await modal.wait_for(state="hidden", timeout=10000)
    return json.loads(json.dumps(candidates))


async def _safe_text(locator) -> str | None:
    if await locator.count() == 0:
        return None
    text = await locator.text_content()
    return text.strip() if text and text.strip() else None


def normalize_title(title: str) -> str:
    return " ".join(title.lower().split())


def checksum_text(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def crawl_track_sync(
    track_url: str, *, headless: bool = True
) -> tuple[CrawlResult, str]:
    return asyncio.run(crawl_track(track_url, headless=headless))
