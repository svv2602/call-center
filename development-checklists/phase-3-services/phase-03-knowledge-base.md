# Фаза 3: База знаний (RAG)

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Реализовать RAG-pipeline для экспертных консультаций: pgvector для хранения embeddings, генерация векторов, tool `search_knowledge_base`, наполнение базы знаний (минимум 20 статей).

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить наличие pgvector расширения в PostgreSQL
- [ ] Изучить структуру knowledge base из `doc/development/phase-3-services.md`
- [ ] Проверить модели данных: knowledge_articles, knowledge_embeddings

**Команды для поиска:**
```bash
grep -rn "pgvector\|vector\|embedding" src/
grep -rn "knowledge\|knowledge_base\|RAG" src/
ls knowledge_base/ 2>/dev/null || echo "knowledge_base/ не существует"
```

#### B. Анализ зависимостей
- [ ] pgvector расширение в PostgreSQL
- [ ] Embedding модель (OpenAI text-embedding-3-small или аналог)
- [ ] Таблицы knowledge_articles, knowledge_embeddings
- [ ] Tool `search_knowledge_base` из канонического списка

**Новые абстракции:** Нет
**Новые env variables:** `EMBEDDING_MODEL`, `OPENAI_API_KEY` (для embeddings)
**Новые tools:** `search_knowledge_base`
**Миграции БД:** `004_add_knowledge_base.py` — knowledge_articles, knowledge_embeddings

#### C. Проверка архитектуры
- [ ] Векторный поиск: cosine similarity, top-5 результатов
- [ ] Chunking: статьи разбиваются на фрагменты для embedding
- [ ] При консультационном вопросе → search_knowledge_base → top-5 → LLM как контекст

**Референс-модуль:** `doc/technical/data-model.md` — секции knowledge_articles, knowledge_embeddings

**Цель:** Определить RAG-pipeline и стратегию наполнения.

**Заметки для переиспользования:** -

---

### 3.1 Миграция БД для базы знаний

- [ ] Создать `migrations/versions/004_add_knowledge_base.py`
- [ ] Таблица `knowledge_articles`: id, title, category (brands/guides/faq/comparisons), content, active, created_at, updated_at
- [ ] Таблица `knowledge_embeddings`: id, article_id, chunk_text, embedding (VECTOR(1536)), chunk_index
- [ ] Расширение pgvector: `CREATE EXTENSION IF NOT EXISTS vector`
- [ ] Индекс для векторного поиска: `ivfflat (embedding vector_cosine_ops)`

**Файлы:** `migrations/versions/004_add_knowledge_base.py`
**Заметки:** Размер вектора 1536 для text-embedding-3-small

---

### 3.2 Embedding pipeline

- [ ] Создать модуль `src/knowledge/embeddings.py`
- [ ] Chunking: разбиение статей на фрагменты (по абзацам или по ~500 токенов)
- [ ] Генерация embeddings через OpenAI API (или альтернативу)
- [ ] Сохранение в knowledge_embeddings
- [ ] Batch-обработка при начальной загрузке
- [ ] Обновление embeddings при изменении статьи

**Файлы:** `src/knowledge/embeddings.py`
**Заметки:** -

---

### 3.3 Векторный поиск

- [ ] Создать модуль `src/knowledge/search.py`
- [ ] Поиск: запрос → embedding → cosine similarity → top-N
- [ ] Параметры: query, category (фильтр), limit (default 5)
- [ ] SQL: `SELECT ... ORDER BY embedding <=> query_embedding LIMIT 5`
- [ ] Fallback: если pgvector недоступен → текстовый поиск (ILIKE)

**Файлы:** `src/knowledge/search.py`
**Заметки:** -

---

### 3.4 Tool: search_knowledge_base

- [ ] Добавить schema в `src/agent/tools.py`
- [ ] Параметры: `query` (string, required)
- [ ] Описание: "Поиск по базе знаний магазина (характеристики шин, рекомендации, FAQ)"
- [ ] Маршрутизация: → `GET /knowledge/search?query=...` или локальный pgvector
- [ ] Результат: top-5 фрагментов → Agent получает как контекст

**Файлы:** `src/agent/tools.py`, `src/knowledge/search.py`
**Заметки:** Каноническое имя: `search_knowledge_base`

---

### 3.5 Наполнение базы знаний

- [ ] Создать структуру контента:
  ```
  knowledge_base/
  ├── brands/           # Описание брендов
  ├── guides/           # Руководства по подбору
  ├── faq/              # Часто задаваемые вопросы
  └── comparisons/      # Сравнения
  ```
- [ ] Минимум 20 статей: бренды (5+), FAQ (5+), гайды (5+), сравнения (5+)
- [ ] Скрипт загрузки: `scripts/load_knowledge_base.py`
- [ ] Генерация embeddings для всех статей

**Файлы:** `knowledge_base/`, `scripts/load_knowledge_base.py`
**Заметки:** Первичное наполнение из существующих материалов магазина

---

### 3.6 Store API endpoint: GET /knowledge/search

- [ ] Реализовать в Store Client: `search_knowledge(query, category, limit)` → `GET /knowledge/search`
- [ ] Маппинг: id, title, category, content (фрагмент), relevance_score
- [ ] Обработка пустого результата

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
