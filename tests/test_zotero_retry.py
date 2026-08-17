"""Zotero API 暫時性 5xx 錯誤的重試行為。

背景：2026-07-12（start=3200）與 2026-08-16（start=4500）兩次排程都因
Zotero API 回 502 Bad Gateway 而中斷。fetch_zotero_corpus 是流程第一步，
它一失敗整條 pipeline 就停住、當日推薦信完全不會寄出。
pyzotero 對 5xx 不會自動重試，因此在此層補上指數退避。
"""

from types import SimpleNamespace

import httpx
import pytest
from pyzotero import errors

from zotero_arxiv_daily.executor import retry_on_transient_error


def make_http_error(status: int) -> errors.HTTPError:
    """仿造 pyzotero.errors.error_handler 產生的訊息格式。"""
    return errors.HTTPError(
        f"\nCode: {status}\nURL: https://api.zotero.org/users/1/items\n"
        f"Method: GET\nResponse: <html>error</html>"
    )


def make_http_error_with_cause(status: int) -> errors.HTTPError:
    """訊息不含 Code: 時，仍應能從 __cause__ 的 httpx 回應取得狀態碼。"""
    request = httpx.Request("GET", "https://api.zotero.org/users/1/items")
    response = httpx.Response(status, request=request)
    cause = httpx.HTTPStatusError("server error", request=request, response=response)
    exc = errors.HTTPError("something went wrong")
    exc.__cause__ = cause
    return exc


class RecordingSleep:
    """收集退避秒數，避免測試真的睡著。"""

    def __init__(self):
        self.delays = []

    def __call__(self, seconds):
        self.delays.append(seconds)


def test_retries_on_502_then_succeeds():
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 3:
            raise make_http_error(502)
        return "corpus"

    sleep = RecordingSleep()
    result = retry_on_transient_error(flaky, max_attempts=5, base_delay=2.0, sleep=sleep)

    assert result == "corpus"
    assert len(calls) == 3
    # 指數退避：第一次等 2 秒，第二次等 4 秒
    assert sleep.delays == [2.0, 4.0]


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_retries_on_all_transient_5xx(status):
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise make_http_error(status)
        return "ok"

    assert retry_on_transient_error(flaky, sleep=RecordingSleep()) == "ok"
    assert len(calls) == 2


def test_extracts_status_from_exception_cause():
    """訊息沒有 Code: 欄位時，改由 __cause__ 的 httpx response 判斷。"""
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise make_http_error_with_cause(503)
        return "ok"

    assert retry_on_transient_error(flaky, sleep=RecordingSleep()) == "ok"
    assert len(calls) == 2


def test_retries_on_connection_failure():
    """CouldNotReachURLError 屬於網路暫時性失敗，同樣要重試。"""
    calls = []

    def flaky():
        calls.append(1)
        if len(calls) < 2:
            raise errors.CouldNotReachURLError("connection reset")
        return "ok"

    assert retry_on_transient_error(flaky, sleep=RecordingSleep()) == "ok"
    assert len(calls) == 2


def test_does_not_retry_on_404():
    """設定錯誤（如 user_id 打錯）應立刻失敗，不要浪費時間重試。"""
    calls = []

    def always_404():
        calls.append(1)
        raise errors.ResourceNotFoundError("\nCode: 404\n")

    with pytest.raises(errors.ResourceNotFoundError):
        retry_on_transient_error(always_404, sleep=RecordingSleep())

    assert len(calls) == 1


def test_does_not_retry_on_401():
    calls = []

    def always_401():
        calls.append(1)
        raise errors.UserNotAuthorisedError("\nCode: 403\n")

    with pytest.raises(errors.UserNotAuthorisedError):
        retry_on_transient_error(always_401, sleep=RecordingSleep())

    assert len(calls) == 1


def test_does_not_swallow_unrelated_exceptions():
    def boom():
        raise ValueError("not a zotero problem")

    with pytest.raises(ValueError, match="not a zotero problem"):
        retry_on_transient_error(boom, sleep=RecordingSleep())


def test_raises_after_exhausting_attempts():
    """重試用盡後應原樣拋出最後一個例外，讓 workflow 明確失敗。"""
    calls = []

    def always_502():
        calls.append(1)
        raise make_http_error(502)

    sleep = RecordingSleep()
    with pytest.raises(errors.HTTPError):
        retry_on_transient_error(always_502, max_attempts=4, base_delay=1.0, sleep=sleep)

    assert len(calls) == 4
    # 最後一次失敗後不應再睡
    assert sleep.delays == [1.0, 2.0, 4.0]


def test_fetch_zotero_corpus_retries_transient_failure(monkeypatch):
    """整合層：確認 fetch_zotero_corpus 真的走了重試路徑。"""
    from zotero_arxiv_daily import executor as executor_module

    item = {
        "data": {
            "title": "A Paper",
            "abstractNote": "an abstract",
            "dateAdded": "2026-01-01T00:00:00Z",
            "collections": ["COLKEY"],
        }
    }
    collection = {"key": "COLKEY", "data": {"name": "Inbox", "parentCollection": None}}

    attempts = []

    class FakeZotero:
        def __init__(self, *args, **kwargs):
            pass

        def collections(self):
            return "collections-query"

        def items(self, **kwargs):
            return "items-query"

        def everything(self, query):
            if query == "collections-query":
                return [collection]
            attempts.append(1)
            if len(attempts) < 3:
                raise make_http_error(502)
            return [item]

    monkeypatch.setattr(executor_module.zotero, "Zotero", FakeZotero)
    monkeypatch.setattr(executor_module.time, "sleep", lambda s: None)

    ex = executor_module.Executor.__new__(executor_module.Executor)
    ex.config = SimpleNamespace(zotero=SimpleNamespace(user_id="1", api_key="k"))

    corpus = ex.fetch_zotero_corpus()

    assert len(attempts) == 3
    assert [p.title for p in corpus] == ["A Paper"]
    assert corpus[0].paths == ["Inbox"]
