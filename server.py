import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from mcp.server.fastmcp import Context, FastMCP

mcp = FastMCP("DuckDuckGo MCP Server")


class BookmarkStore:
    """In-memory хранилище закладок с автоинкрементом ID."""

    def __init__(self, persist_path: str | None = None):
        self._bookmarks: list[dict[str, Any]] = []
        self._next_id: int = 1
        self._persist_path = Path(persist_path) if persist_path else None

    def add(self, url: str, title: str, tags: list[str]) -> dict[str, Any]:
        for bookmark in self._bookmarks:
            if bookmark["url"] == url:
                bookmark["title"] = title
                bookmark["tags"] = tags
                bookmark["updated"] = True
                return bookmark.copy()

        bookmark = {
            "id": self._next_id,
            "url": url,
            "title": title,
            "tags": tags,
            "saved_at": datetime.now().isoformat(),
            "updated": False,
        }
        self._bookmarks.append(bookmark)
        self._next_id += 1
        return bookmark.copy()

    def search(self, query: str) -> list[dict[str, Any]]:
        if not query:
            return [b.copy() for b in self._bookmarks]

        q = query.lower()
        return [
            b.copy()
            for b in self._bookmarks
            if q in b["title"].lower()
            or q in b["url"].lower()
            or any(q in tag.lower() for tag in b["tags"])
        ]

    def get_by_tag(self, tag: str) -> list[dict[str, Any]]:
        t = tag.lower()
        return [
            b.copy() for b in self._bookmarks if t in [x.lower() for x in b["tags"]]
        ]

    def get_all(self) -> list[dict[str, Any]]:
        return [b.copy() for b in self._bookmarks]

    def clear(self) -> None:
        self._bookmarks.clear()
        self._next_id = 1

    def dump_json(self) -> None:
        if not self._persist_path:
            return
        self._persist_path.write_text(
            json.dumps(self._bookmarks, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def load_json(self) -> None:
        if not self._persist_path or not self._persist_path.exists():
            return
        content = self._persist_path.read_text(encoding="utf-8")
        parsed = json.loads(content)
        if isinstance(parsed, list):
            self._bookmarks = [b for b in parsed if isinstance(b, dict)]
            max_id = max((int(b.get("id", 0)) for b in self._bookmarks), default=0)
            self._next_id = max_id + 1


BOOKMARK_STORE = BookmarkStore()


def _normalize_tags(tags: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()

    for tag in tags:
        if not isinstance(tag, str):
            continue
        cleaned = tag.strip()
        if not cleaned:
            continue
        lowered = cleaned.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        unique.append(cleaned)

    return unique


def _flatten_related_topics(items: list[dict[str, Any]], limit: int) -> list[dict[str, str]]:
    result: list[dict[str, str]] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            if len(result) >= limit:
                return

            nested = node.get("Topics")
            if isinstance(nested, list):
                walk(nested)
                continue

            text = node.get("Text")
            url = node.get("FirstURL")
            if text and url:
                result.append({"text": text, "url": url})

    walk(items)
    return result[:limit]


async def _ddg_request(query: str, ctx: Context) -> dict[str, Any] | None:
    """Общий запрос к DuckDuckGo API с обработкой ошибок."""
    normalized_query = query.strip()[:500]
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            ctx.info(f"Запрос к DuckDuckGo: {normalized_query}")
            response = await client.get(
                "https://api.duckduckgo.com/",
                params={
                    "q": normalized_query,
                    "format": "json",
                    "no_html": 1,
                    "skip_disambig": 1,
                },
            )
            response.raise_for_status()
            return response.json()
        except httpx.TimeoutException:
            ctx.warning("Таймаут запроса к DuckDuckGo")
            return {"error": "Превышено время ожидания. Попробуйте позже."}
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                ctx.warning("Rate limit от DuckDuckGo")
                return {"error": "Слишком много запросов. Подождите минуту."}
            ctx.warning(f"HTTP ошибка: {e.response.status_code}")
            return {"error": f"Сервис временно недоступен ({e.response.status_code})"}
        except httpx.RequestError:
            ctx.warning("Ошибка сети")
            return {"error": "Ошибка сети. Проверьте подключение к интернету."}


@mcp.tool()
async def search(query: str, ctx: Context) -> dict[str, Any]:
    """Выполняет поиск через DuckDuckGo Instant Answer API.

    Args:
        query: Поисковый запрос
    """
    if not query or not query.strip():
        return {"error": "Query не может быть пустым"}

    normalized_query = query.strip()[:500]
    ctx.info(f"Поиск: {normalized_query}")

    data = await _ddg_request(normalized_query, ctx)
    await ctx.report_progress(1, 2)

    if not data:
        await ctx.report_progress(2, 2)
        return {"error": "Не удалось получить ответ от DuckDuckGo"}

    if "error" in data:
        await ctx.report_progress(2, 2)
        return data

    related = _flatten_related_topics(data.get("RelatedTopics", []), limit=5)
    abstract = (data.get("AbstractText") or "").strip()
    answer = (data.get("Answer") or "").strip()

    if not abstract and answer:
        abstract = answer

    response: dict[str, Any] = {
        "heading": data.get("Heading", ""),
        "abstract": abstract,
        "source": data.get("AbstractSource", ""),
        "url": data.get("AbstractURL", ""),
        "answer": answer,
        "image": data.get("Image", ""),
        "type": data.get("Type", ""),
        "related": related,
    }

    if not abstract and not answer:
        response["fallback"] = True
        ctx.warning("Пустой ответ, возвращаю related topics")

    await ctx.report_progress(2, 2)
    return response


@mcp.tool()
async def define(term: str, ctx: Context) -> dict[str, Any]:
    """Получает определение термина через DuckDuckGo."""
    if not term or not term.strip():
        return {"error": "Term не может быть пустым"}

    normalized_term = term.strip()[:500]
    ctx.info(f"Определение: {normalized_term}")

    data = await _ddg_request(f"define {normalized_term}", ctx)
    await ctx.report_progress(1, 2)

    if not data:
        await ctx.report_progress(2, 2)
        return {
            "term": normalized_term,
            "definition": "Определение не найдено. Попробуйте другой термин или используйте search().",
            "source": "",
            "url": "",
        }

    if "error" in data:
        await ctx.report_progress(2, 2)
        return data

    definition = (data.get("AbstractText") or "").strip() or (data.get("Answer") or "").strip()
    if not definition:
        ctx.warning("Определение не найдено")
        definition = "Определение не найдено. Попробуйте другой термин или используйте search()."

    result = {
        "term": normalized_term,
        "definition": definition,
        "source": data.get("AbstractSource", ""),
        "url": data.get("AbstractURL", ""),
    }

    await ctx.report_progress(2, 2)
    return result


@mcp.tool()
async def related_topics(
    query: str,
    ctx: Context,
    limit: int = 5,
) -> list[dict[str, str]] | dict[str, str]:
    """Возвращает связанные темы для исследовательского поиска."""
    if limit < 1 or limit > 20:
        return {"error": "limit должен быть в диапазоне 1..20"}

    if not query or not query.strip():
        return {"error": "Query не может быть пустым"}

    normalized_query = query.strip()[:500]
    ctx.info(f"Поиск связанных тем: {normalized_query}")

    data = await _ddg_request(normalized_query, ctx)
    await ctx.report_progress(1, 2)

    if not data:
        await ctx.report_progress(2, 2)
        ctx.warning("Связанные темы не найдены")
        return []

    if "error" in data:
        await ctx.report_progress(2, 2)
        return data

    topics = _flatten_related_topics(data.get("RelatedTopics", []), limit=limit)
    if not topics:
        ctx.warning("Связанные темы не найдены")

    await ctx.report_progress(2, 2)
    return topics


@mcp.tool()
async def save_bookmark(
    url: str,
    title: str,
    ctx: Context,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    """Сохраняет закладку в in-memory хранилище."""
    if not url.startswith("http://") and not url.startswith("https://"):
        return {"error": "Невалидный URL. Ожидается http:// или https://"}

    if not title or not title.strip():
        return {"error": "Title не может быть пустым"}

    normalized_tags = _normalize_tags(tags or [])
    ctx.info(f"Сохранение закладки: {url}")

    bookmark = BOOKMARK_STORE.add(url=url.strip(), title=title.strip(), tags=normalized_tags)
    await ctx.report_progress(1, 1)
    return bookmark


@mcp.tool()
async def search_bookmarks(query: str, ctx: Context) -> list[dict[str, Any]]:
    """Ищет закладки по title, url и tags (регистронезависимо)."""
    normalized_query = (query or "").strip()
    results = BOOKMARK_STORE.search(normalized_query)
    ctx.info(f"Найдено {len(results)} закладок")
    await ctx.report_progress(1, 1)
    return results


@mcp.resource("guide://search-tips")
def search_tips_resource() -> str:
    """Советы по использованию поиска."""
    return (
        "Советы по поисковым запросам:\n"
        "1. Используй конкретные ключевые слова, избегай общих фраз\n"
        "2. Для определений используй tool \"define\" вместо \"search\"\n"
        "3. Для изучения темы используй \"related_topics\" для расширения контекста\n"
        "4. DuckDuckGo Instant Answer лучше отвечает на фактологические запросы\n"
        "5. Запросы на английском языке дают больше результатов\n"
        "6. Для составных исследований используй prompt \"research\"\n"
        "7. Сохраняй полезные источники через \"save_bookmark\""
    )


@mcp.resource("bookmarks://all")
def bookmarks_all_resource() -> str:
    """Возвращает все сохраненные закладки в формате JSON."""
    return json.dumps(BOOKMARK_STORE.get_all(), ensure_ascii=False, indent=2)


@mcp.resource("bookmarks://tag/{tag}")
def bookmarks_by_tag_resource(tag: str) -> str:
    """Возвращает закладки по тегу (регистронезависимо)."""
    return json.dumps(BOOKMARK_STORE.get_by_tag(tag), ensure_ascii=False, indent=2)


@mcp.prompt()
def research_prompt(topic: str) -> str:
    """Шаблон для пошагового исследования темы."""
    return (
        f'Исследуй тему: "{topic}"\n\n'
        "Пошаговый план:\n"
        f'1. Вызови search("{topic}") для получения общего обзора\n'
        f'2. Вызови define("{topic}") для точного определения\n'
        f'3. Вызови related_topics("{topic}") для расширения контекста\n'
        "4. Изучи 2-3 наиболее релевантных связанных темы через search()\n"
        "5. Сохрани лучшие источники через save_bookmark()\n"
        "6. Сформируй структурированный ответ с разделами:\n"
        "   - Определение\n"
        "   - Основные факты\n"
        "   - Связанные темы\n"
        "   - Источники"
    )


@mcp.prompt()
def fact_check_prompt(claim: str) -> str:
    """Шаблон для проверки утверждения."""
    return (
        f'Проверь утверждение: "{claim}"\n\n'
        "План:\n"
        "1. Вызови search() с ключевыми словами из утверждения\n"
        "2. Вызови define() для уточнения ключевых терминов\n"
        "3. Сравни найденную информацию с утверждением\n"
        "4. Дай оценку: Подтверждено / Опровергнуто / Недостаточно данных\n"
        "5. Приведи источники"
    )


@mcp.prompt()
def summarize_prompt(url: str) -> str:
    """Шаблон для анализа веб-страницы."""
    return (
        f"Проанализируй страницу: {url}\n\n"
        "План:\n"
        "1. Определи тему страницы по URL\n"
        "2. Вызови search() с ключевыми словами из URL\n"
        "3. Вызови related_topics() для контекста\n"
        "4. Сохрани страницу в закладки через save_bookmark()\n"
        "5. Сформируй краткое описание темы на основе найденной информации"
    )


if __name__ == "__main__":
    transport = "stdio"
    if "--sse" in sys.argv:
        transport = "sse"
    mcp.run(transport=transport)
