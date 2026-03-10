import httpx
import pytest
import respx

import server
from server import BookmarkStore


class DummyContext:
    def __init__(self) -> None:
        self.infos: list[str] = []
        self.warnings: list[str] = []
        self.progress: list[tuple[int, int]] = []

    def info(self, message: str) -> None:
        self.infos.append(message)

    def warning(self, message: str) -> None:
        self.warnings.append(message)

    async def report_progress(self, current: int, total: int) -> None:
        self.progress.append((current, total))


@pytest.fixture
def ctx() -> DummyContext:
    return DummyContext()


@pytest.fixture
def store() -> BookmarkStore:
    s = BookmarkStore()
    yield s
    s.clear()


@pytest.fixture(autouse=True)
def reset_global_store() -> None:
    server.BOOKMARK_STORE.clear()


@pytest.fixture
def mock_ddg_response() -> dict:
    return {
        "Heading": "Python (programming language)",
        "AbstractText": "Python is a high-level programming language.",
        "AbstractSource": "Wikipedia",
        "AbstractURL": "https://en.wikipedia.org/wiki/Python_(programming_language)",
        "Answer": "",
        "AnswerType": "",
        "Image": "https://example.com/python.png",
        "Type": "A",
        "RelatedTopics": [
            {"Text": "Guido van Rossum", "FirstURL": "https://example.com/guido"},
            {"Text": "CPython", "FirstURL": "https://example.com/cpython"},
            {
                "Name": "Implementations",
                "Topics": [
                    {"Text": "PyPy", "FirstURL": "https://example.com/pypy"}
                ],
            },
        ],
    }


@pytest.fixture
def mock_empty_response() -> dict:
    return {
        "Heading": "",
        "AbstractText": "",
        "AbstractSource": "",
        "AbstractURL": "",
        "Answer": "",
        "AnswerType": "",
        "Image": "",
        "Type": "",
        "RelatedTopics": [],
    }


@respx.mock
@pytest.mark.asyncio
async def test_search_returns_valid_structure(ctx: DummyContext, mock_ddg_response: dict) -> None:
    respx.get("https://api.duckduckgo.com/").mock(
        return_value=httpx.Response(200, json=mock_ddg_response)
    )

    result = await server.search("Python", ctx)

    assert isinstance(result, dict)
    assert {"heading", "abstract", "url", "related"}.issubset(result.keys())


@pytest.mark.asyncio
async def test_search_empty_query(ctx: DummyContext) -> None:
    result = await server.search("", ctx)
    assert "error" in result


@respx.mock
@pytest.mark.asyncio
async def test_search_long_query_truncated(ctx: DummyContext, mock_ddg_response: dict) -> None:
    long_query = "x" * 700
    captured_query = {"value": ""}

    def handler(request: httpx.Request) -> httpx.Response:
        captured_query["value"] = request.url.params.get("q", "")
        return httpx.Response(200, json=mock_ddg_response)

    respx.get("https://api.duckduckgo.com/").mock(side_effect=handler)

    await server.search(long_query, ctx)

    assert len(captured_query["value"]) == 500


@respx.mock
@pytest.mark.asyncio
async def test_search_empty_api_response(ctx: DummyContext, mock_empty_response: dict) -> None:
    respx.get("https://api.duckduckgo.com/").mock(
        return_value=httpx.Response(200, json=mock_empty_response)
    )

    result = await server.search("unknown_query", ctx)

    assert result.get("fallback") is True or result.get("related") == []


@respx.mock
@pytest.mark.asyncio
async def test_define_known_term(ctx: DummyContext, mock_ddg_response: dict) -> None:
    respx.get("https://api.duckduckgo.com/").mock(
        return_value=httpx.Response(200, json=mock_ddg_response)
    )

    result = await server.define("API", ctx)

    assert result["term"] == "API"
    assert result["definition"]


@respx.mock
@pytest.mark.asyncio
async def test_define_unknown_term(ctx: DummyContext, mock_empty_response: dict) -> None:
    respx.get("https://api.duckduckgo.com/").mock(
        return_value=httpx.Response(200, json=mock_empty_response)
    )

    result = await server.define("xyznonexistent123", ctx)

    assert "Определение не найдено" in result["definition"]


@respx.mock
@pytest.mark.asyncio
async def test_related_topics_respects_limit(ctx: DummyContext, mock_ddg_response: dict) -> None:
    respx.get("https://api.duckduckgo.com/").mock(
        return_value=httpx.Response(200, json=mock_ddg_response)
    )

    result = await server.related_topics("Python", ctx, limit=3)

    assert isinstance(result, list)
    assert len(result) <= 3


@respx.mock
@pytest.mark.asyncio
async def test_related_topics_handles_subcategories(ctx: DummyContext) -> None:
    nested_response = {
        "RelatedTopics": [
            {
                "Name": "Implementations",
                "Topics": [
                    {"Text": "PyPy", "FirstURL": "https://example.com/pypy"},
                    {"Text": "Jython", "FirstURL": "https://example.com/jython"},
                ],
            }
        ]
    }

    respx.get("https://api.duckduckgo.com/").mock(
        return_value=httpx.Response(200, json=nested_response)
    )

    result = await server.related_topics("python impl", ctx, limit=5)

    assert isinstance(result, list)
    assert any(item["text"] == "PyPy" for item in result)


@pytest.mark.asyncio
async def test_save_bookmark_success(ctx: DummyContext) -> None:
    result = await server.save_bookmark(
        "https://docs.python.org", "Python Docs", ctx, tags=["python", "docs"]
    )

    assert result["id"] == 1
    assert result["url"].startswith("https://")
    assert result["title"] == "Python Docs"
    assert result["tags"] == ["python", "docs"]
    assert "saved_at" in result
    assert result["updated"] is False


@pytest.mark.asyncio
async def test_save_bookmark_invalid_url(ctx: DummyContext) -> None:
    result = await server.save_bookmark("ftp://invalid", "Invalid", ctx)
    assert result["error"] == "Невалидный URL. Ожидается http:// или https://"


@pytest.mark.asyncio
async def test_save_duplicate_updates(ctx: DummyContext) -> None:
    first = await server.save_bookmark(
        "https://example.com", "Old title", ctx, tags=["old"]
    )
    second = await server.save_bookmark(
        "https://example.com", "New title", ctx, tags=["new"]
    )

    assert first["id"] == second["id"]
    assert second["title"] == "New title"
    assert second["tags"] == ["new"]
    assert second["updated"] is True


@pytest.mark.asyncio
async def test_search_bookmarks_by_tag(ctx: DummyContext) -> None:
    await server.save_bookmark(
        "https://docs.python.org", "Python Docs", ctx, tags=["python", "docs"]
    )
    await server.save_bookmark("https://example.com", "Example", ctx, tags=["misc"])

    result = await server.search_bookmarks("PYTHON", ctx)

    assert len(result) == 1
    assert result[0]["title"] == "Python Docs"
