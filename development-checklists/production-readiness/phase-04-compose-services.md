# Фаза 4: Docker Compose — внешние сервисы

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
`docker-compose.yml` ссылается на хосты `store-api:3000` и `asterisk:8088`, но таких сервисов в compose нет. Нужно добавить mock/stub сервисы для разработки.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `docker-compose.yml` строки 10, 14 — ссылки на store-api и asterisk
- [x] Изучить `src/store_client/client.py` — какие endpoints Store API вызываются
- [x] Изучить `src/core/asterisk_ari.py` — как используется Asterisk ARI
- [x] Изучить `asterisk/extensions.conf` — конфигурация Asterisk dialplan
- [x] Изучить `doc/development/api-specification.md` — контракт Store API

#### B. Анализ зависимостей
- [x] Store API: нужен mock-сервер, который отвечает на все endpoints из api-specification.md
- [x] Asterisk: нужен ли в dev-compose или достаточно документации?
- [x] Решить: mock в compose (отдельный контейнер) или stub встроенный в call-processor

#### C. Проверка архитектуры
- [x] Mock store-api должен возвращать реалистичные данные для тестирования
- [x] call-processor должен gracefully работать без Asterisk (уже реализовано: AudioSocket слушает TCP)

---

### 4.1 Создать mock Store API сервис
- [x] Создать директорию `mock-store-api/`
- [x] Создать `mock-store-api/app.py` — FastAPI-приложение с mock-ответами
- [x] Реализовать endpoints: `GET /api/v1/tires/search`, `GET /api/v1/tires/{id}/availability`
- [x] Реализовать endpoints: `POST /api/v1/orders`, `GET /api/v1/orders/{id}`, `POST /api/v1/orders/{id}/confirm`
- [x] Реализовать endpoints: `GET /api/v1/fitting/stations`, `GET /api/v1/fitting/slots`, `POST /api/v1/fitting/book`
- [x] Реализовать `GET /api/v1/health` для readiness probe
- [x] Добавить Bearer token проверку (STORE_API_KEY)
- [x] Данные: статические JSON-ответы с реалистичными украинскими шинами

---

### 4.2 Добавить Dockerfile для mock Store API
- [x] Создать `mock-store-api/Dockerfile` (простой: python:3.12-slim + fastapi + uvicorn)
- [x] Создать `mock-store-api/requirements.txt` (fastapi, uvicorn)

---

### 4.3 Добавить store-api в docker-compose.yml
- [x] Добавить сервис `store-api` в `docker-compose.yml`
- [x] Указать `build: ./mock-store-api`
- [x] Указать port `3002:3000`
- [x] Добавить healthcheck
- [x] Добавить `depends_on` для call-processor: `store-api: condition: service_healthy`

---

### 4.4 Обновить docker-compose.dev.yml
- [x] Добавить store-api сервис в dev compose
- [x] Привязать port 3000 для локальной разработки

---

### 4.5 Документировать Asterisk как внешнюю зависимость
- [x] Добавить комментарий в `docker-compose.yml` о том, что Asterisk запускается отдельно
- [x] Изменить `ARI_URL` default на localhost (для работы без Asterisk в dev)
- [x] Добавить секцию в README.md: "Asterisk (опционально для dev)"

---

### 4.6 Проверить docker compose up
- [x] Запустить `docker compose up --build store-api`
- [x] Проверить `curl http://localhost:3002/api/v1/health` — mock store-api отвечает
- [x] Проверить `curl http://localhost:3002/api/v1/tires/search?season=winter` — mock данные

---

## При завершении фазы
Все задачи выполнены. Mock Store API создан и работает.
