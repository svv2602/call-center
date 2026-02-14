# Фаза 3: База знаний (RAG)

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Реализовать RAG-pipeline для экспертных консультаций: pgvector для хранения embeddings, генерация векторов, tool `search_knowledge_base`, наполнение базы знаний (минимум 20 статей).

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить наличие pgvector расширения в PostgreSQL
- [x] Изучить структуру knowledge base из `doc/development/phase-3-services.md`
- [x] Проверить модели данных: knowledge_articles, knowledge_embeddings

**Команды для поиска:**
```bash
grep -rn "pgvector\|vector\|embedding" src/
grep -rn "knowledge\|knowledge_base\|RAG" src/
ls knowledge_base/ 2>/dev/null || echo "knowledge_base/ не существует"
```

#### B. Анализ зависимостей
- [x] pgvector расширение в PostgreSQL
- [x] Embedding модель (OpenAI text-embedding-3-small или аналог)
- [x] Таблицы knowledge_articles, knowledge_embeddings
- [x] Tool `search_knowledge_base` из канонического списка

**Новые абстракции:** Нет
**Новые env variables:** `EMBEDDING_MODEL`, `OPENAI_API_KEY` (для embeddings)
**Новые tools:** `search_knowledge_base`
**Миграции БД:** `004_add_knowledge_base.py` — knowledge_articles, knowledge_embeddings

#### C. Проверка архитектуры
- [x] Векторный поиск: cosine similarity, top-5 результатов
- [x] Chunking: статьи разбиваются на фрагменты для embedding
- [x] При консультационном вопросе → search_knowledge_base → top-5 → LLM как контекст

**Референс-модуль:** `doc/technical/data-model.md` — секции knowledge_articles, knowledge_embeddings

**Цель:** Определить RAG-pipeline и стратегию наполнения.

**Заметки для переиспользования:** -

---

### 3.1 Миграция БД для базы знаний

- [x] Создать `migrations/versions/004_add_knowledge_base.py`
- [x] Таблица `knowledge_articles`: id, title, category (brands/guides/faq/comparisons), content, active, created_at, updated_at
- [x] Таблица `knowledge_embeddings`: id, article_id, chunk_text, embedding (VECTOR(1536)), chunk_index
- [x] Расширение pgvector: `CREATE EXTENSION IF NOT EXISTS vector`
- [x] Индекс для векторного поиска: `ivfflat (embedding vector_cosine_ops)`

**Файлы:** `migrations/versions/004_add_knowledge_base.py`
**Заметки:** Размер вектора 1536 для text-embedding-3-small

---

### 3.2 Embedding pipeline

- [x] Создать модуль `src/knowledge/embeddings.py`
- [x] Chunking: разбиение статей на фрагменты (по абзацам или по ~500 токенов)
- [x] Генерация embeddings через OpenAI API (или альтернативу)
- [x] Сохранение в knowledge_embeddings
- [x] Batch-обработка при начальной загрузке
- [x] Обновление embeddings при изменении статьи

**Файлы:** `src/knowledge/embeddings.py`
**Заметки:** -

---

### 3.3 Векторный поиск

- [x] Создать модуль `src/knowledge/search.py`
- [x] Поиск: запрос → embedding → cosine similarity → top-N
- [x] Параметры: query, category (фильтр), limit (default 5)
- [x] SQL: `SELECT ... ORDER BY embedding <=> query_embedding LIMIT 5`
- [x] Fallback: если pgvector недоступен → текстовый поиск (ILIKE)

**Файлы:** `src/knowledge/search.py`
**Заметки:** -

---

### 3.4 Tool: search_knowledge_base

- [x] Добавить schema в `src/agent/tools.py`
- [x] Параметры: `query` (string, required)
- [x] Описание: "Поиск по базе знаний магазина (характеристики шин, рекомендации, FAQ)"
- [x] Маршрутизация: → `GET /knowledge/search?query=...` или локальный pgvector
- [x] Результат: top-5 фрагментов → Agent получает как контекст

**Файлы:** `src/agent/tools.py`, `src/knowledge/search.py`
**Заметки:** Каноническое имя: `search_knowledge_base`

---

### 3.5 Наполнение базы знаний

- [x] Создать структуру контента:
  ```
  knowledge_base/
  ├── brands/           # Описание брендов
  ├── guides/           # Руководства по подбору
  ├── faq/              # Часто задаваемые вопросы
  └── comparisons/      # Сравнения
  ```
- [x] Минимум 20 статей: бренды (5+), FAQ (5+), гайды (5+), сравнения (5+)
- [x] Скрипт загрузки: `scripts/load_knowledge_base.py`
- [x] Генерация embeddings для всех статей

**Файлы:** `knowledge_base/`, `scripts/load_knowledge_base.py`
**Заметки:** Первичное наполнение из существующих материалов магазина

---

### 3.6 Store API endpoint: GET /knowledge/search

- [x] Реализовать в Store Client: `search_knowledge(query, category, limit)` → `GET /knowledge/search`
- [x] Маппинг: id, title, category, content (фрагмент), relevance_score
- [x] Обработка пустого результата

**Файлы:** `src/store_client/client.py`
**Заметки:** Можно использовать как локальный pgvector, так и Store API

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-3-services): phase-3 knowledge base completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-04-complex-scenarios.md`
