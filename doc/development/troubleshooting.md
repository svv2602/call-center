# Troubleshooting Guide

## 1. AudioSocket

### 1.1 "Connection refused" на порт 9092

**Симптом:** Asterisk логирует `AudioSocket connection to 127.0.0.1:9092 failed`.

**Причины и решения:**

| Причина | Проверка | Решение |
|---------|----------|---------|
| Call Processor не запущен | `ss -tlnp \| grep 9092` | Запустить сервис |
| Порт занят другим процессом | `lsof -i :9092` | Остановить конфликтующий процесс |
| Firewall блокирует | `iptables -L -n \| grep 9092` | Открыть порт |
| Неправильный IP в dialplan | Проверить `extensions.conf` | Исправить IP/порт |

### 1.2 AudioSocket подключается, но нет аудио

**Симптом:** TCP-соединение установлено, UUID получен, но аудио-пакеты не приходят.

**Решения:**
- Проверить формат аудио в dialplan: `Set(CHANNEL(audioreadformat)=slin16)` и `Set(CHANNEL(audiowriteformat)=slin16)`
- Убедиться что `Answer()` вызван ДО `AudioSocket()`
- Проверить логи Call Processor: `grep "audio packet" logs/` — должны быть пакеты типа `0x10`

### 1.3 Аудио искажено / шумы

**Симптом:** STT не распознаёт речь, клиент слышит искажённый голос.

**Решения:**
- Проверить sample rate: должно быть 16kHz в обоих направлениях
- Проверить byte order: PCM должен быть little-endian, signed 16-bit
- Проверить что не происходит двойная конвертация кодеков в Asterisk

## 2. Google Cloud STT

### 2.1 "Permission denied" / Authentication error

**Симптом:** `google.auth.exceptions.DefaultCredentialsError`

**Решения:**
- Проверить путь: `echo $GOOGLE_APPLICATION_CREDENTIALS`
- Проверить что файл JSON валидный: `python -c "import json; json.load(open('secrets/gcp-key.json'))"`
- Проверить роли Service Account: нужна `Cloud Speech-to-Text User`
- Проверить что Speech-to-Text API включён в проекте: `gcloud services list --enabled | grep speech`

### 2.2 STT не распознаёт речь / пустые транскрипты

**Симптом:** `is_final=True` приходит, но `text=""` или бессмыслица.

**Решения:**
- Проверить что аудио доходит до STT: логировать размер чанков (`DEBUG: audio chunk {len} bytes`)
- Проверить формат: Google STT ожидает LINEAR16, 16kHz, mono
- Проверить `language_code`: для украинского/русского используем мультиязычную конфигурацию (см. architecture.md)
- Тест: записать WAV из AudioSocket и отправить вручную через `gcloud ml speech recognize`

### 2.3 "Exceeded maximum allowed stream duration"

**Симптом:** STT-сессия обрывается после ~5 минут.

**Причина:** Google STT ограничивает streaming-сессию ~305 секундами.

**Решение:** Автоматический restart сессии:

```python
# В STT модуле: при приближении к лимиту — graceful restart
if stream_duration > 280:  # 280 сек, с запасом до лимита 305
    await self.restart_stream()
```

### 2.4 Высокая задержка STT (>500ms)

**Решения:**
- Проверить расположение: использовать ближайший Google Cloud регион (`europe-west1` для Украины)
- Проверить сеть: `ping speech.googleapis.com`
- Уменьшить размер аудио-чанков (отправлять чаще, меньшими порциями: 100ms вместо 200ms)
- Проверить `interim_results=True` — промежуточные результаты ускоряют воспринимаемую задержку

## 3. Google Cloud TTS

### 3.1 "Voice not found" для украинского

**Симптом:** `google.api_core.exceptions.InvalidArgument: Voice not found`

**Решения:**
- Проверить доступные голоса: `gcloud ml speech voices list --filter="languageCodes:uk-UA"`
- Использовать правильное имя: `uk-UA-Standard-A`, `uk-UA-Wavenet-A`, `uk-UA-Neural2-A`
- Убедиться что TTS API включён в проекте

### 3.2 TTS-аудио не воспроизводится в Asterisk

**Решения:**
- Проверить формат: TTS должен возвращать LINEAR16, 16kHz (совпадает с AudioSocket)
- Проверить что аудио-байты обёрнуты в пакет AudioSocket (тип `0x10`, правильная длина)
- Логировать размер TTS-ответа: если 0 байт — проблема на стороне TTS

## 4. Claude API (LLM)

### 4.1 "Rate limit exceeded"

**Симптом:** `anthropic.RateLimitError`

**Решения:**
- Проверить текущие лимиты: [console.anthropic.com](https://console.anthropic.com)
- Внедрить exponential backoff (уже в архитектуре)
- При пиковой нагрузке: переключение на оператора вместо retry
- Запросить повышение лимитов у Anthropic

### 4.2 LLM "галлюцинирует" / выдумывает цены

**Симптом:** Бот называет цены или наличие, не вызывая tool.

**Решения:**
- Усилить системный промпт: "НІКОЛИ не називай ціни або наявність без виклику інструменту"
- Проверить что tools корректно переданы в API-запрос
- Добавить post-processing проверку: если ответ содержит "грн" но нет tool_call — алерт

### 4.3 Высокая задержка LLM (>1.5s TTFT)

**Решения:**
- Использовать streaming ответов (`stream=True`) — первый токен приходит быстрее
- Сократить системный промпт (чем короче — тем быстрее)
- Сократить историю диалога (последние 10 turns, не все)
- Для простых запросов (статус заказа) — использовать Claude Haiku

## 5. Asterisk

### 5.1 SIP-регистрация не работает

**Решения:**
- Проверить логи: `asterisk -rvvv`, затем `sip show peers` или `pjsip show endpoints`
- Проверить firewall: порты 5060 (SIP), 10000-20000 (RTP)
- Проверить credentials в `sip.conf` / `pjsip.conf`

### 5.2 Переключение на оператора не работает

**Симптом:** `transfer_to_operator()` вызван, но клиент не попадает в очередь.

**Решения:**
- Проверить ARI доступность: `curl -u user:pass http://localhost:8088/ari/asterisk/info`
- Проверить что контекст `transfer-to-operator` существует: `dialplan show transfer-to-operator`
- Проверить что очередь `operators` создана: `queue show operators`
- Проверить логи ARI в Call Processor

### 5.3 Эхо во время разговора

**Решения:**
- Включить echo cancellation в Asterisk: `echocancel=yes` в `sip.conf`
- Проверить что не происходит feedback loop (аудио TTS попадает обратно в STT)
- В pipeline: mute STT на время воспроизведения TTS (или реализовать echo cancellation на уровне приложения)

## 6. PostgreSQL

### 6.1 "Connection refused" / "Too many connections"

**Решения:**
- Проверить статус: `docker compose exec postgres pg_isready`
- Проверить max_connections: `SHOW max_connections;` (дефолт 100)
- Использовать connection pooling (PgBouncer или asyncpg pool)
- Проверить что Call Processor корректно закрывает соединения

### 6.2 Медленные запросы к логам

**Решения:**
- Проверить индексы: `\di` в psql
- Для таблицы `calls`: индексы на `started_at`, `caller_id`, `customer_id`
- Для `knowledge_embeddings`: IVFFLAT индекс на vector-колонке
- `EXPLAIN ANALYZE` для медленных запросов

## 7. Redis

### 7.1 "OOM command not allowed"

**Симптом:** Redis отказывает в записи из-за нехватки памяти.

**Решения:**
- Проверить использование: `redis-cli info memory`
- Проверить TTL на сессиях: `redis-cli ttl call_session:<uuid>`
- Увеличить `maxmemory` в redis.conf
- Если TTL не установлен — баг, все сессии должны иметь TTL 1800 сек

## 8. Общие проблемы

### 8.1 Высокая общая задержка ответа (>2 сек)

**Диагностика:**

```bash
# Проверить метрики в Prometheus/Grafana
# Или в логах:
grep "latency" logs/call_processor.log | tail -20
```

**Бюджет задержки (target 2000ms):**

| Этап | Бюджет | Как измерить |
|------|--------|-------------|
| AudioSocket → STT | 50ms | `stt_start_latency_ms` |
| STT распознавание | 200-500ms | `stt_latency_ms` |
| LLM (TTFT) | 500-1000ms | `llm_latency_ms` |
| TTS синтез | 300-500ms | `tts_latency_ms` |
| TTS → AudioSocket | 50ms | `tts_delivery_latency_ms` |
| **Итого** | **1100-2100ms** | `total_response_latency_ms` |

**Если превышает бюджет:**
- STT > 500ms → проверить сеть до Google, уменьшить chunk size
- LLM > 1000ms → включить streaming, сократить промпт/историю
- TTS > 500ms → включить streaming TTS (по предложениям), использовать кэш

### 8.2 Утечка памяти

**Диагностика:**

```bash
# Мониторинг RSS процесса
ps aux | grep call_processor
# Или в Prometheus: process_resident_memory_bytes
```

**Типичные причины:**
- Сессии не очищаются из Redis (проверить TTL)
- Аудио-буферы не освобождаются при hangup
- Историю диалога в LLM не обрезают (растёт с каждым turn)
