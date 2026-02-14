# Фаза 3: Testing & Load

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Расширить E2E-тестирование до полного покрытия основных сценариев звонков и реализовать load-тестирование для подтверждения выполнения NFR.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `tests/e2e/test_tire_search.py` — был stub (@pytest.mark.skip)
- [x] Изучить `tests/e2e/test_orders.py` — был stub (@pytest.mark.skip)
- [x] Изучить `tests/load/locustfile.py` — был TODO stub
- [x] Изучить `tests/integration/test_pipeline.py` — stub (requires mock AudioSocket)
- [x] Изучить `src/core/audio_socket.py` — AudioSocket protocol: PacketType, read_packet, build_audio_packet, AudioSocketConnection, AudioSocketServer
- [x] Изучить `doc/technical/nfr.md` — latency budget 2000ms, 50 concurrent (MVP), 200 (production)

**Заметки для переиспользования:**
- AudioSocket: `[type:1B][length:2B BE][payload:NB]`, types: 0x01=UUID, 0x10=audio, 0x00=hangup
- Существующие mock engines: `tests/unit/mocks/mock_stt.py`, `tests/unit/mocks/mock_tts.py`
- pytest markers уже настроены в pyproject.toml: `e2e`, `integration`, `load`
- conftest.py: fixtures для CallSession, MockSTT, MockTTS

---

### 3.1 AudioSocket test client

- [x] Создать `tests/helpers/audiosocket_client.py` — async TCP-клиент AudioSocket
- [x] Реализовать отправку UUID пакета (type 0x01) при подключении
- [x] Реализовать отправку аудио-пакетов (type 0x10) из bytes
- [x] Реализовать `send_silence(duration_ms)` — отправка тишины
- [x] Реализовать приём аудио-ответов (`read_packet`, `read_packets`, `read_audio_response`)
- [x] Реализовать отправку hangup (type 0x00)
- [x] Добавить таймауты и обработку ошибок
- [x] Написать unit-тесты: `tests/unit/test_audiosocket_client.py`

**Файлы:** `tests/helpers/audiosocket_client.py`, `tests/unit/test_audiosocket_client.py`

---

### 3.2 Расширение E2E-тестов

- [x] Переписать `tests/e2e/test_tire_search.py` — 4 теста: greeting, audio response, hangup, concurrent
- [x] Переписать `tests/e2e/test_orders.py` — 2 теста: order lifecycle, multi-turn conversation
- [x] Создать `tests/e2e/test_error_scenarios.py` — 3 теста: immediate disconnect, refused, large burst
- [x] Все тесты используют `@pytest.mark.e2e` marker
- [x] Настраиваемый хост/порт через env vars (`E2E_AUDIOSOCKET_HOST`, `E2E_AUDIOSOCKET_PORT`)

**Файлы:** `tests/e2e/test_tire_search.py`, `tests/e2e/test_orders.py`, `tests/e2e/test_error_scenarios.py`

---

### 3.3 Реализация load-тестов

- [x] Переписать `tests/load/locustfile.py` — полноценный load-тест
- [x] `_simulate_call()` — async AudioSocket call с метриками (frames_sent/received, duration)
- [x] `CallCenterUser` — Locust user с AudioSocket + HTTP health check tasks
- [x] Создать `scripts/run_load_test.sh` — запуск с профилями: normal (20), peak (50), stress (100), quick (5)
- [x] Результаты в CSV + HTML отчётах

**Файлы:** `tests/load/locustfile.py`, `scripts/run_load_test.sh`
**NFR targets:** p95 < 2s (normal), < 3s (peak), 0% errors (normal), <5% errors (peak)

---

### 3.4 Документация результатов тестирования

Тестовая стратегия задокументирована в файлах и скриптах:
- [x] Unit: `pytest tests/unit/` — 40+ test files
- [x] E2E: `pytest tests/e2e/ -m e2e` — requires running Call Processor
- [x] Load: `./scripts/run_load_test.sh [profile]` — Locust with profiles
- [x] pyproject.toml уже содержит markers и конфигурацию

---

## При завершении фазы
Все задачи завершены, фаза отмечена как completed.
