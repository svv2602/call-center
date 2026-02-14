# Фаза 1: Dockerfile и сборка образа

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Создать multi-stage Dockerfile для call-processor, чтобы `docker build .` и `docker compose up` могли собрать образ. Сейчас `docker-compose.yml` содержит `build: .`, но Dockerfile отсутствует.

## Задачи

### 1.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `docker-compose.yml` — какие порты, volumes, env variables используются
- [x] Изучить `docker-compose.dev.yml` — как устроена dev-среда
- [x] Изучить `pyproject.toml` — зависимости, Python-версия, структура пакета
- [x] Изучить `src/main.py` — точка входа приложения (`python -m src.main`)
- [x] Изучить `.env.example` — все env variables для контейнера

**Команды для поиска:**
```bash
# Точка входа
grep -n "if __name__" src/main.py
# Все зависимости
grep -A 50 "dependencies" pyproject.toml
# Порты из compose
grep -n "ports:" docker-compose.yml
# Build контексты
grep -n "build:" docker-compose.yml
```

#### B. Анализ зависимостей
- [x] Нужен ли gRPC для Google STT (google-cloud-speech использует grpcio)?
- [x] Нужны ли системные библиотеки (libpq для asyncpg, etc.)?
- [x] Сколько сервисов используют `build: .` (call-processor, celery-worker, celery-beat)?

**Системные зависимости:** asyncpg требует libpq, grpcio уже содержит бинарные wheels
**Сервисы с `build: .`:** call-processor (строка 3), celery-worker (строка 109), celery-beat (строка 130)

#### C. Проверка архитектуры
- [x] Dockerfile должен поддерживать и call-processor, и celery (разный CMD)
- [x] Образ должен быть минимальным (slim/alpine + только runtime deps)
- [x] GOOGLE_APPLICATION_CREDENTIALS — монтируется как volume, не bake в образ

**Референс:** Стандартный multi-stage для Python (builder → runtime)

**Заметки для переиспользования:** Один Dockerfile для трёх сервисов (call-processor, celery-worker, celery-beat). CMD задаётся в docker-compose.yml для celery, default CMD — `python -m src.main`.

---

### 1.1 Создать Dockerfile (multi-stage build)
- [x] Создать `Dockerfile` в корне проекта
- [x] Stage 1 (builder): `python:3.12-slim`, установить build-зависимости, pip install
- [x] Stage 2 (runtime): `python:3.12-slim`, скопировать установленные пакеты из builder
- [x] Скопировать `src/`, `migrations/`, `alembic.ini`, `asterisk/`, `knowledge_base/`
- [x] Установить `EXPOSE 9092 8080`
- [x] Установить default `CMD ["python", "-m", "src.main"]`
- [x] Добавить `.dockerignore` для исключения `.venv`, `tests`, `.git`, `doc`, `__pycache__`

**Файлы:** `Dockerfile`, `.dockerignore`
**Заметки:** Не забыть `--no-cache-dir` для pip. Non-root user для безопасности.

---

### 1.2 Добавить .dockerignore
- [x] Создать `.dockerignore` с исключениями: `.venv/`, `.git/`, `__pycache__/`, `*.pyc`, `tests/`, `doc/`, `audit/`, `.pytest_cache/`, `*.egg-info/`, `.env*`, `development-checklists/`

**Файлы:** `.dockerignore`

---

### 1.3 Проверить сборку образа
- [x] Выполнить `docker build -t call-center-ai:test .`
- [x] Убедиться, что образ собирается без ошибок
- [x] Проверить размер образа (`docker images call-center-ai:test`) — 308MB (< 500MB)
- [x] Проверить, что `docker run --rm call-center-ai:test python -c "import src; print('OK')"` работает

**Заметки:** Целевой размер образа — менее 500MB.

---

### 1.4 Проверить docker compose build
- [x] Выполнить `docker compose build` (должен использовать новый Dockerfile)
- [x] Убедиться, что все три сервиса с `build: .` собираются: call-processor, celery-worker, celery-beat
- [x] Проверить, что celery-worker и celery-beat переопределяют CMD через `command:` в compose

**Заметки:** celery-worker использует `command: celery -A src.tasks.celery_app worker ...`, celery-beat — `command: celery -A src.tasks.celery_app beat ...`

---

## При завершении фазы

Выполни следующие действия:

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы:
   - [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add Dockerfile .dockerignore
   git commit -m "checklist(production-readiness): phase-1 dockerfile and build completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 2
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
