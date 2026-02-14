# Фаза 7: Embeddings — согласование провайдера

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Явно задокументировать и оформить зависимость от OpenAI для embeddings.

## Задачи

### 7.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] `src/knowledge/embeddings.py` — OpenAI text-embedding-3-small, 1536 dimensions
- [x] `src/knowledge/search.py` — pgvector cosine similarity + ILIKE fallback
- [x] `scripts/load_knowledge_base.py` — standalone, uses os.environ for OPENAI_API_KEY
- [x] `knowledge_base/` — 22 markdown файла

#### B. Анализ зависимостей
- [x] Текущее: OpenAI text-embedding-3-small, 1536 dimensions
- [x] pgvector поддерживает любые размерности

**Решение:** Оставить OpenAI embeddings, явно задокументировать, добавить в config.

#### C. Проверка архитектуры
- [x] EmbeddingGenerator уже имеет удобный интерфейс
- [x] Config через env variables — нужно добавить OPENAI_API_KEY в config.py

---

### 7.1 Добавить OPENAI_API_KEY в конфигурацию
- [x] Добавлен `OpenAISettings` в `src/config.py` (api_key, embedding_model, embedding_dimensions)
- [x] Добавлен в `Settings` как `openai: OpenAISettings`
- [x] Добавлена helper-функция `_get_embedding_config()` в embeddings.py

---

### 7.2 Добавить OPENAI_API_KEY в env-файлы
- [x] Добавлен в `.env.example` с комментарием
- [x] Комментарий объясняет зачем OpenAI при основном стеке Anthropic+Google

---

### 7.3 Обновить scripts/load_knowledge_base.py
- [x] Скрипт оставлен standalone (не часть основного приложения) — это нормально для утилитного скрипта

---

### 7.4 Документировать решение
- [x] Добавлена секция "Внешние API" в README.md
- [x] Таблица: Anthropic, Google Cloud STT/TTS, OpenAI (embeddings)
- [x] Объяснено что OpenAI опционален

---

### 7.5 Добавить fallback/валидацию при отсутствии ключа
- [x] В `src/knowledge/search.py` — если generator is None, возвращает пустой список
- [x] Логирует WARNING

---

## При завершении фазы
Все задачи выполнены. OpenAI embeddings задокументирован и оформлен в config.
