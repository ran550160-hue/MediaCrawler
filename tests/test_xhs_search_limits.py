from __future__ import annotations

import pytest

import config
import media_platform.xhs.core as xhs_core
from media_platform.xhs.core import XiaoHongShuCrawler
from media_platform.xhs.exception import DataFetchError


class FakeXhsClient:
    def __init__(self, items):
        self.items = items

    async def get_note_by_keyword(self, **kwargs):
        return {"has_more": True, "items": self.items}


@pytest.fixture(autouse=True)
def restore_config():
    original_max_notes = config.CRAWLER_MAX_NOTES_COUNT
    original_start_page = config.START_PAGE
    original_sleep = config.CRAWLER_MAX_SLEEP_SEC
    original_keywords = config.KEYWORDS
    original_concurrency = config.MAX_CONCURRENCY_NUM
    try:
        yield
    finally:
        config.CRAWLER_MAX_NOTES_COUNT = original_max_notes
        config.START_PAGE = original_start_page
        config.CRAWLER_MAX_SLEEP_SEC = original_sleep
        config.KEYWORDS = original_keywords
        config.MAX_CONCURRENCY_NUM = original_concurrency


async def _noop(*args, **kwargs):
    return None


def _items(count):
    return [
        {
            "id": f"n{idx}",
            "xsec_source": "pc_search",
            "xsec_token": f"token-{idx}",
            "model_type": "note",
        }
        for idx in range(1, count + 1)
    ]


def _configure_search(max_notes):
    config.CRAWLER_MAX_NOTES_COUNT = max_notes
    config.START_PAGE = 1
    config.CRAWLER_MAX_SLEEP_SEC = 0
    config.KEYWORDS = "AI编程副业"
    config.MAX_CONCURRENCY_NUM = 4


@pytest.mark.asyncio
async def test_xhs_search_slices_detail_tasks_before_fetching(monkeypatch):
    _configure_search(max_notes=1)
    updated = []
    detail_calls = []
    crawler = XiaoHongShuCrawler()
    crawler.xhs_client = FakeXhsClient(_items(20))

    async def fake_get_detail(note_id, xsec_source, xsec_token, semaphore):
        detail_calls.append(note_id)
        return {"note_id": note_id, "xsec_token": xsec_token}

    async def fake_update(note):
        updated.append(note)

    monkeypatch.setattr(crawler, "get_note_detail_async_task", fake_get_detail)
    monkeypatch.setattr(crawler, "get_notice_media", _noop)
    monkeypatch.setattr(crawler, "batch_get_note_comments", _noop)
    monkeypatch.setattr(xhs_core.xhs_store, "update_xhs_note", fake_update)

    await crawler.search()

    assert detail_calls == ["n1"]
    assert [note["note_id"] for note in updated] == ["n1"]


@pytest.mark.asyncio
async def test_xhs_search_skips_single_detail_exception(monkeypatch):
    _configure_search(max_notes=2)
    updated = []
    crawler = XiaoHongShuCrawler()

    class FakePagedXhsClient:
        def __init__(self):
            self.page = 0

        async def get_note_by_keyword(self, **kwargs):
            self.page += 1
            if self.page == 1:
                return {"has_more": True, "items": _items(2)}
            return {"has_more": True, "items": _items(3)[2:]}

    crawler.xhs_client = FakePagedXhsClient()

    async def fake_get_detail(note_id, xsec_source, xsec_token, semaphore):
        if note_id == "n1":
            raise RuntimeError("captcha on one note")
        return {"note_id": note_id, "xsec_token": xsec_token}

    async def fake_update(note):
        updated.append(note)

    monkeypatch.setattr(crawler, "get_note_detail_async_task", fake_get_detail)
    monkeypatch.setattr(crawler, "get_notice_media", _noop)
    monkeypatch.setattr(crawler, "batch_get_note_comments", _noop)
    monkeypatch.setattr(xhs_core.xhs_store, "update_xhs_note", fake_update)

    await crawler.search()

    updated_ids = [note["note_id"] for note in updated]
    assert "n1" not in updated_ids
    assert "n2" in updated_ids


@pytest.mark.asyncio
async def test_xhs_search_raises_when_all_detail_tasks_fail(monkeypatch):
    _configure_search(max_notes=2)
    crawler = XiaoHongShuCrawler()
    crawler.xhs_client = FakeXhsClient(_items(20))

    async def fake_get_detail(note_id, xsec_source, xsec_token, semaphore):
        raise RuntimeError("captcha")

    monkeypatch.setattr(crawler, "get_note_detail_async_task", fake_get_detail)

    with pytest.raises(DataFetchError):
        await crawler.search()


@pytest.mark.asyncio
async def test_xhs_client_headers_use_mac_user_agent_and_platform(monkeypatch):
    crawler = XiaoHongShuCrawler()
    crawler.browser_context = object()
    crawler.context_page = object()

    async def fake_convert_browser_context_cookies(browser_context, urls=None):
        return "web_session=fake", {"web_session": "fake"}

    monkeypatch.setattr(xhs_core.utils, "convert_browser_context_cookies", fake_convert_browser_context_cookies)

    client = await crawler.create_xhs_client(httpx_proxy=None)

    assert client.headers["user-agent"] == crawler.user_agent
    assert "Macintosh; Intel Mac OS X" in client.headers["user-agent"]
    assert client.headers["sec-ch-ua-platform"] == '"macOS"'
