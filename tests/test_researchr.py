from __future__ import annotations

import asyncio

from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from research_auto.infrastructure.crawlers import researchr


class _AsyncPlaywrightContext:
    def __init__(self, playwright) -> None:
        self.playwright = playwright

    async def __aenter__(self):
        return self.playwright

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeBrowser:
    def __init__(self, page) -> None:
        self.page = page
        self.closed = False

    async def new_page(self):
        return self.page

    async def close(self) -> None:
        self.closed = True


class _FakeChromium:
    def __init__(self, browser) -> None:
        self.browser = browser

    async def launch(self, *, headless: bool):
        return self.browser


class _FakePlaywright:
    def __init__(self, browser) -> None:
        self.chromium = _FakeChromium(browser)


class _FakePage:
    def __init__(self) -> None:
        self.goto_calls: list[tuple[str, str]] = []
        self.wait_for_load_state_calls: list[str] = []

    async def goto(self, url: str, wait_until: str):
        self.goto_calls.append((url, wait_until))

    async def wait_for_load_state(self, state: str):
        self.wait_for_load_state_calls.append(state)
        raise PlaywrightTimeoutError("network still busy")

    async def content(self) -> str:
        return "<html></html>"


def test_crawl_track_keeps_results_when_networkidle_times_out(monkeypatch) -> None:
    page = _FakePage()
    browser = _FakeBrowser(page)

    monkeypatch.setattr(
        researchr,
        "async_playwright",
        lambda: _AsyncPlaywrightContext(_FakePlaywright(browser)),
    )

    async def _fake_extract_accepted_papers(page_arg):
        assert page_arg is page
        return [
            {
                "title": "Paper A",
                "authors": ["Ada"],
                "detail_url": "https://example.com/details/a",
                "pdf_url": "https://example.com/a.pdf",
                "abstract": "Abstract A",
                "session_name": "Session A",
            }
        ]

    monkeypatch.setattr(researchr, "_extract_accepted_papers", _fake_extract_accepted_papers)

    result, html = asyncio.run(researchr.crawl_track("https://example.com/track"))

    assert result.discovered == 1
    assert result.paper_candidates[0].title == "Paper A"
    assert html == "<html></html>"
    assert page.goto_calls == [("https://example.com/track", "domcontentloaded")]
    assert page.wait_for_load_state_calls == ["networkidle"]
    assert browser.closed is True


class _FakeRowModal:
    def __init__(self, *, event_id: str, title: str | None = None, timeout: bool = False) -> None:
        self.event_id = event_id
        self.title = title
        self.timeout = timeout
        self.closed = False

    def locator(self, selector: str):
        if selector == ".event-title strong":
            return _FakeTextLocator(self.title)
        if selector == ".modal-body p":
            return _FakeParagraphLocator(["Abstract"])
        if selector == ".modal-header a.navigate":
            return _FakeTextLocator("Session")
        if selector == "a[href]":
            return _FakeLinksLocator([
                {"text": "Details", "href": f"https://example.com/details/{self.event_id}"},
                {"text": "PDF", "href": f"https://example.com/{self.event_id}.pdf"},
            ])
        if selector == ".close":
            return _FakeCloseLocator(self)
        raise AssertionError(f"unexpected modal selector: {selector}")

    async def wait_for(self, *, state: str, timeout: int):
        if self.timeout and state == "visible":
            raise PlaywrightTimeoutError(f"modal {self.event_id} timeout")
        return None


class _FakeTrigger:
    def __init__(self, *, event_id: str, title: str) -> None:
        self.event_id = event_id
        self.title = title

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return 1

    async def get_attribute(self, name: str):
        assert name == "data-event-modal"
        return self.event_id

    async def click(self) -> None:
        return None

    async def text_content(self) -> str:
        return self.title


class _FakeTextLocator:
    def __init__(self, text: str | None) -> None:
        self.text = text

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return 0 if self.text is None else 1

    async def text_content(self) -> str | None:
        return self.text


class _FakeParagraphLocator:
    def __init__(self, paragraphs: list[str]) -> None:
        self.paragraphs = paragraphs

    async def all_text_contents(self) -> list[str]:
        return self.paragraphs


class _FakeLinksLocator:
    def __init__(self, links: list[dict[str, str]]) -> None:
        self.links = links

    async def evaluate_all(self, script: str):
        return self.links


class _FakeCloseLocator:
    def __init__(self, modal: _FakeRowModal) -> None:
        self.modal = modal

    @property
    def first(self):
        return self

    async def count(self) -> int:
        return 1

    async def click(self) -> None:
        self.modal.closed = True


class _FakeRow:
    def __init__(self, *, event_id: str, title: str) -> None:
        self.event_id = event_id
        self.title = title

    def locator(self, selector: str):
        if selector == "[data-event-modal]":
            return _FakeTrigger(event_id=self.event_id, title=self.title)
        if selector == 'a[href*="/profile/"]':
            return _FakeLinksLocator(["Ada"])
        if selector == "a[href]":
            return _FakeLinksLocator([
                {"text": "PDF", "href": f"https://example.com/{self.event_id}.pdf"}
            ])
        raise AssertionError(f"unexpected row selector: {selector}")


class _FakeRows:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self._rows = rows

    async def count(self) -> int:
        return len(self._rows)

    def nth(self, index: int) -> _FakeRow:
        return self._rows[index]


class _FakeTable:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self.rows = rows

    @property
    def first(self):
        return self

    def locator(self, selector: str):
        assert selector == "tbody tr"
        return _FakeRows(self.rows)


class _FakeAcceptedHeader:
    def __init__(self, rows: list[_FakeRow]) -> None:
        self.rows = rows

    @property
    def first(self):
        return self

    def locator(self, selector: str):
        assert selector == "xpath=following-sibling::*[1]"
        return _FakeTable(self.rows)


class _FakeExtractPage:
    def __init__(self) -> None:
        self.rows = [
            _FakeRow(event_id="ok", title="Paper A"),
            _FakeRow(event_id="slow", title="Paper B"),
        ]
        self.modals = {
            "ok": _FakeRowModal(event_id="ok", title="Paper A"),
            "slow": _FakeRowModal(event_id="slow", title="Paper B", timeout=True),
        }

    def locator(self, selector: str, has_text: str | None = None):
        if selector == "h3":
            assert has_text == "Accepted Papers"
            return _FakeAcceptedHeader(self.rows)
        if selector.startswith("#modal-"):
            return self.modals[selector.removeprefix("#modal-")]
        raise AssertionError(f"unexpected page selector: {selector}")


def test_extract_accepted_papers_skips_rows_that_timeout_after_partial_progress() -> None:
    candidates = asyncio.run(researchr._extract_accepted_papers(_FakeExtractPage()))

    assert [candidate["title"] for candidate in candidates] == ["Paper A"]
