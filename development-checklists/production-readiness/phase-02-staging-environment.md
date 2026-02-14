# Фаза 2: Staging Environment

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Создать полноценное staging-окружение, максимально приближённое к production. Staging должен подниматься одной командой и позволять тестировать всю цепочку: AudioSocket → STT → LLM → TTS → Store API без внешних зависимостей.

## Задачи

### 2.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `docker-compose.yml` — production-конфигурация сервисов
- [ ] Изучить `docker-compose.dev.yml` — dev-конфигурация (что уже есть для локальной разработки)
- [ ] Изучить `mock-store-api/` — существующий мок Store API
- [ ] Изучить `.env.example` — все env variables проекта
- [ ] Изучить `.github/workflows/ci.yml` — текущий deploy-staging stub
- [ ] Изучить `asterisk/` — конфигурация Asterisk (если есть)

**Команды для поиска:**
```bash
# Docker compose файлы
ls docker-compose*.yml
# Mock сервисы
ls mock-store-api/
# Asterisk config
ls asterisk/
# Env variables
cat .env.example
# CI staging job
grep -A 20 "deploy-staging" .github/workflows/ci.yml
```

#### B. Анализ зависимостей
- [ ] Определить: нужен ли mock Asterisk или использовать реальный в Docker
- [ ] Определить: как мокать STT/TTS для staging (реальные Google API или mock)
- [ ] Определить: нужен ли отдельный PostgreSQL instance или shared с dev

**Новые абстракции:** -
**Новые env variables:** -
**Новые tools:** -
**Миграции БД:** -

#### C. Проверка архитектуры
- [ ] Staging должен быть изолирован от production данных
- [ ] Все сервисы должны использовать staging-specific credentials
- [ ] Healthcheck на каждом сервисе

**Референс-модуль:** `docker-compose.dev.yml`

**Цель:** Спроектировать staging-окружение, которое максимально близко к production но безопасно для тестирования.

**Заметки для переиспользования:** -

---

### 2.1 Создание docker-compose.staging.yml

- [ ] Создать `docker-compose.staging.yml` на основе `docker-compose.yml` с staging-настройками
- [ ] Включить все сервисы: call-processor, postgres, redis, store-api-mock, prometheus, grafana, alertmanager
- [ ] Добавить Asterisk контейнер (если возможно) или документировать как подключить внешний
- [ ] Настроить отдельные порты чтобы не конфликтовать с dev-окружением
- [ ] Добавить healthcheck для каждого сервиса
- [ ] Добавить named volumes для persistent data
- [ ] Добавить resource limits (memory, CPU) приближённые к production

**Файлы:** `docker-compose.staging.yml`
**Заметки:** Использовать `docker-compose.yml` как базу, переопределить env variables для staging.

---

### 2.2 Конфигурация staging-окружения

- [ ] Создать `.env.staging` с staging-specific переменными
- [ ] Настроить STT/TTS: использовать реальные Google API с тестовыми credentials или mock-режим
- [ ] Настроить LLM: использовать Claude API с лимитами для staging (max tokens, rate limit)
- [ ] Настроить Store API mock: seed с тестовыми данными (шины, заказы, станции шиномонтажа)
- [ ] Создать `scripts/seed_staging.py` — заполнение staging БД тестовыми данными
- [ ] Добавить в seed: тестовые статьи knowledge base, тестовые операторы, тестовые звонки

**Файлы:** `.env.staging`, `scripts/seed_staging.py`
**Заметки:** В .env.staging НЕ хранить реальные production credentials. Добавить в .gitignore если содержит секреты.

---

### 2.3 Smoke-тесты для staging

- [ ] Создать `scripts/smoke_test_staging.sh` — проверка что все сервисы поднялись
- [ ] Проверять: `/health` — 200 OK
- [ ] Проверять: `/health/ready` — все зависимости UP
- [ ] Проверять: Store API mock отвечает на `/api/v1/tires/search`
- [ ] Проверять: Redis PING → PONG
- [ ] Проверять: PostgreSQL `SELECT 1` через call-processor
- [ ] Проверять: Prometheus targets are UP
- [ ] Добавить цветной вывод: зелёный (OK), красный (FAIL), жёлтый (WARN)

**Файлы:** `scripts/smoke_test_staging.sh`
**Заметки:** Скрипт должен возвращать exit code 0 если все проверки прошли, 1 если есть failures.

---

### 2.4 Интеграция staging в CI/CD

- [ ] Обновить `.github/workflows/ci.yml` — заменить stub deploy-staging на реальный job
- [ ] Добавить шаг: `docker compose -f docker-compose.staging.yml up -d`
- [ ] Добавить шаг: `scripts/smoke_test_staging.sh` после поднятия окружения
- [ ] Добавить шаг: teardown staging после тестов (`docker compose down -v`)
- [ ] Настроить staging deploy только для `main` ветки
- [ ] Добавить timeout для staging job (10 минут max)

**Файлы:** `.github/workflows/ci.yml`
**Заметки:** GitHub Actions поддерживает Docker Compose через `services` или через shell commands.

---

## При завершении фазы

Выполни следующие действия:

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы:
   - [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(production-readiness): phase-2 staging environment completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 3
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
