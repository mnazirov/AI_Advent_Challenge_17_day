# DuckDuckGo MCP Server

DuckDuckGo MCP Server — MCP-сервер для поиска информации в интернете, спроектированный для ассистентов.

## Возможности

### Tools

| Tool | Описание |
|---|---|
| `search(query)` | Основной поиск через DuckDuckGo Instant Answer API с fallback на related topics |
| `define(term)` | Получение определения термина через запрос `define <term>` |
| `related_topics(query, limit)` | Получение связанных тем (включая вложенные подкатегории) |
| `save_bookmark(url, title, tags)` | Сохранение закладки в in-memory хранилище с дедупликацией по URL |
| `search_bookmarks(query)` | Поиск закладок по title/url/tags (регистронезависимо) |

### Resources

| Resource | Описание |
|---|---|
| `guide://search-tips` | Статические советы по эффективному поиску |
| `bookmarks://all` | JSON со всеми сохранёнными закладками |
| `bookmarks://tag/{tag}` | JSON с закладками по тегу (case-insensitive) |

### Prompts

| Prompt | Описание |
|---|---|
| `research_prompt(topic)` | Пошаговый план исследования темы через несколько tools |
| `fact_check_prompt(claim)` | Шаблон проверки утверждения и оценки достоверности |
| `summarize_prompt(url)` | Шаблон анализа страницы и формирования краткого описания |

## Быстрый старт

```bash
git clone ...
cd duckduckgo-mcp-server
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python server.py
```

## Тестирование

```bash
# Юнит-тесты
pytest tests/ -v

# Интерактивная отладка через MCP Inspector
mcp dev server.py
```

## Подключение к Claude Desktop

```json
{
  "mcpServers": {
    "duckduckgo": {
      "command": "/path/to/venv/bin/python",
      "args": ["/path/to/server.py"]
    }
  }
}
```

## Примеры использования

1. Поиск информации по запросу
- Вызов: `search("Python programming language")`
- Результат: общий обзор темы, источник, ссылка, связанные темы.

2. Определение термина
- Вызов: `define("API")`
- Результат: точное определение с источником.

3. Исследование темы (через prompt research)
- Вызов: `research_prompt("Model Context Protocol")`
- Результат: пошаговый исследовательский план с использованием `search`, `define`, `related_topics`, `save_bookmark`.

4. Сохранение и поиск закладок
- Вызовы:
  - `save_bookmark("https://example.com", "Example", ["reference"])`
  - `search_bookmarks("reference")`
- Результат: сохранение источников и быстрый поиск по тегам/названиям/URL.

## Архитектура

```text
Assistant
       │
       ▼
  MCP Protocol (stdio/sse)
       │
       ▼
┌──────────────────────────┐
│   DuckDuckGo MCP Server  │
│                          │
│  Tools:                  │
│  ├── search              │
│  ├── define              │
│  ├── related_topics      │
│  ├── save_bookmark       │
│  └── search_bookmarks    │
│                          │
│  Resources:              │
│  ├── guide://search-tips │
│  ├── bookmarks://all     │
│  └── bookmarks://tag/{t} │
│                          │
│  Prompts:                │
│  ├── research_prompt     │
│  ├── fact_check_prompt   │
│  └── summarize_prompt    │
│                          │
│  Storage:                │
│  └── BookmarkStore       │
│      (in-memory)         │
└──────────┬───────────────┘
           │
           ▼
   DuckDuckGo Instant
     Answer API
   (api.duckduckgo.com)
```

## Запуск

- Основной транспорт (`stdio`): `python server.py`
- Альтернативный транспорт (`sse`): `python server.py --sse`

## Примечания

- API-ключи не требуются.
- Все данные закладок хранятся в памяти процесса.
- Обработка ошибок API включает timeout, 429, HTTP-ошибки и сетевые ошибки.
