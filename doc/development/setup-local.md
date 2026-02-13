# Настройка локального окружения для разработки

## Пререквизиты

| Софт | Версия | Установка |
|------|--------|-----------|
| Python | 3.12+ | `sudo apt install python3.12 python3.12-venv` |
| Docker | 24+ | [docs.docker.com](https://docs.docker.com/engine/install/) |
| Docker Compose | v2+ | Входит в Docker Desktop / `docker compose` |
| Git | 2.40+ | `sudo apt install git` |
| SIPp (опционально) | 3.7+ | `sudo apt install sip-tester` — для E2E тестов |

## Шаг 1: Клонирование и виртуальное окружение

```bash
git clone <repo-url> ~/call-center
cd ~/call-center

python3.12 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev,test]"
```

## Шаг 2: Запуск инфраструктуры (Docker)

```bash
# PostgreSQL + Redis + тестовый Asterisk
docker compose -f docker-compose.dev.yml up -d
```

**docker-compose.dev.yml** запускает:

| Сервис | Порт | Назначение |
|--------|------|------------|
| `postgres` | 5432 | БД (pgvector) |
| `redis` | 6379 | Кэш сессий |
| `asterisk` | 5060 (SIP), 8088 (ARI) | Тестовая АТС |

Проверка:

```bash
# PostgreSQL
docker compose -f docker-compose.dev.yml exec postgres psql -U app -d callcenter -c "SELECT 1"

# Redis
docker compose -f docker-compose.dev.yml exec redis redis-cli ping

# Asterisk ARI
curl -u ari_user:ari_password http://localhost:8088/ari/asterisk/info
```

## Шаг 3: Настройка переменных окружения

```bash
cp .env.example .env.local
```

Отредактировать `.env.local`:

```bash
# БД (локальный Docker)
DATABASE_URL=postgresql://app:devpassword@localhost:5432/callcenter

# Redis (локальный Docker)
REDIS_URL=redis://localhost:6379/0

# Google Cloud STT/TTS
# Вариант A: реальный ключ (нужен для тестирования STT/TTS)
GOOGLE_APPLICATION_CREDENTIALS=./secrets/gcp-dev-key.json

# Вариант B: заглушка (для разработки без STT/TTS)
# USE_MOCK_STT=true
# USE_MOCK_TTS=true

# Anthropic Claude
ANTHROPIC_API_KEY=sk-ant-dev-...

# Asterisk ARI (локальный Docker)
ASTERISK_ARI_URL=http://localhost:8088/ari
ASTERISK_ARI_USER=ari_user
ASTERISK_ARI_PASSWORD=ari_password

# Store API (локальная заглушка)
STORE_API_URL=http://localhost:3000/api/v1
STORE_API_KEY=dev-key

# Настройки разработки
LOG_LEVEL=DEBUG
AUDIOSOCKET_HOST=127.0.0.1
AUDIOSOCKET_PORT=9092
```

## Шаг 4: Запуск Call Processor

```bash
# Загрузить переменные окружения (безопасный способ)
set -a; . ./.env.local; set +a

# Запустить
python -m src.main

# Или с автоперезапуском при изменении кода
pip install watchfiles
watchfiles "python -m src.main" src/
```

Проверка:

```bash
# Health check
curl http://localhost:8080/health

# AudioSocket слушает
ss -tlnp | grep 9092
```

## Шаг 5: Тестовый звонок

### Вариант A: SIP-клиент (рекомендуется)

Установить любой SIP-клиент:

- **Linphone** (Desktop, Linux/macOS/Windows) — [linphone.org](https://www.linphone.org/)
- **Zoiper** (Desktop + Mobile) — [zoiper.com](https://www.zoiper.com/)
- **MicroSIP** (Windows, лёгкий) — [microsip.org](https://www.microsip.org/)

Настройка SIP-аккаунта:

| Параметр | Значение |
|----------|----------|
| SIP Server | `localhost:5060` |
| Username | `1001` |
| Password | `1001` |
| Transport | UDP |

После регистрации — позвонить на номер `100`.

### Вариант B: SIPp (автоматический)

```bash
# Простой тестовый звонок с WAV-файлом
sipp -sn uac -d 10000 -s 100 127.0.0.1:5060 -m 1
```

### Вариант C: Без Asterisk (прямой AudioSocket)

Для отладки pipeline без телефонии — подключиться напрямую к AudioSocket:

```bash
# Утилита для тестирования (в tests/tools/)
python tests/tools/audiosocket_client.py --wav tests/fixtures/sample_call.wav
```

## Работа с Mock-сервисами

Для разработки без реальных API-ключей Google/Anthropic:

```bash
# В .env.local
USE_MOCK_STT=true    # STT возвращает заготовленные фразы
USE_MOCK_TTS=true    # TTS возвращает тишину (или файл из fixtures)
USE_MOCK_LLM=true    # LLM возвращает шаблонные ответы
```

Mock-сервисы полезны для:
- Разработки UI/API без расхода на облачные сервисы
- Отладки AudioSocket и pipeline
- CI/CD тестов (не зависят от внешних сервисов)

## Запуск тестов

```bash
# Все тесты
pytest tests/

# Только unit
pytest tests/unit/

# Только интеграционные (нужен Docker)
pytest tests/integration/

# С покрытием
pytest tests/ --cov=src --cov-report=html
open htmlcov/index.html

# Конкретный тест
pytest tests/unit/test_audio_socket.py -v
```

## Полезные команды

```bash
# Линтинг
ruff check src/
ruff format src/

# Проверка типов
mypy src/ --strict

# Миграции БД
alembic upgrade head        # применить
alembic revision -m "..."   # создать новую

# Логи Asterisk
docker compose -f docker-compose.dev.yml exec asterisk asterisk -rvvv

# Логи всех сервисов
docker compose -f docker-compose.dev.yml logs -f
```

## Структура тестовых данных

```
tests/
├── fixtures/
│   ├── sample_call.wav          # Тестовый аудиофайл (украинская речь)
│   ├── sample_call_russian.wav  # Тестовый аудиофайл (русская речь)
│   ├── store_api_responses/     # Моки ответов Store API
│   │   ├── search_tires.json
│   │   ├── check_availability.json
│   │   └── ...
│   └── transcripts/             # Примеры транскрипций для тестов агента
│       ├── tire_search.json
│       └── order_status.json
├── tools/
│   ├── audiosocket_client.py    # Утилита для тестирования AudioSocket
│   └── generate_test_audio.py   # Генератор тестового аудио через TTS
├── unit/
├── integration/
└── e2e/
```

## Troubleshooting локальной среды

| Проблема | Решение |
|----------|---------|
| `pg_isready` fails | `docker compose -f docker-compose.dev.yml restart postgres` |
| Port 9092 busy | `lsof -i :9092` — найти и остановить процесс |
| Google auth error | Проверить `GOOGLE_APPLICATION_CREDENTIALS` путь; или включить `USE_MOCK_STT=true` |
| Asterisk не стартует | `docker compose -f docker-compose.dev.yml logs asterisk` |
| Redis connection refused | `docker compose -f docker-compose.dev.yml restart redis` |
