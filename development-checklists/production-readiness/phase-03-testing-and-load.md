# Фаза 3: Testing & Load

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Расширить E2E-тестирование до полного покрытия основных сценариев звонков и реализовать load-тестирование для подтверждения выполнения NFR. После этой фазы должна быть уверенность, что система выдерживает заявленную нагрузку.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `tests/e2e/test_tire_search.py` — существующий E2E-тест поиска шин
- [ ] Изучить `tests/e2e/test_orders.py` — существующий E2E-тест заказов
- [ ] Изучить `tests/load/locustfile.py` — stub для load-тестов
- [ ] Изучить `tests/integration/test_pipeline.py` — интеграционный тест pipeline
- [ ] Изучить `doc/technical/nfr.md` — нефункциональные требования (latency, throughput)
- [ ] Изучить `src/core/audio_socket.py` — протокол AudioSocket для симуляции клиента

**Команды для поиска:**
```bash
# Существующие тесты
ls tests/e2e/ tests/integration/ tests/load/
# Fixtures и conftest
find tests/ -name "conftest.py"
# Mock-объекты
grep -rn "Mock\|patch\|fixture" tests/e2e/
# NFR targets
grep -n "p95\|latency\|concurrent\|throughput" doc/technical/nfr.md
```

#### B. Анализ зависимостей
- [ ] Определить: нужен ли AudioSocket test client для E2E (симуляция звонка)
- [ ] Определить: использовать ли SIPp или кастомный TCP-клиент для load tests
- [ ] Определить: нужен ли отдельный pytest marker для E2E-тестов

**Новые абстракции:** -
**Новые env variables:** -
**Новые tools:** -
**Миграции БД:** -

#### C. Проверка архитектуры
- [ ] E2E-тесты должны быть идемпотентны (не оставлять мусор в БД)
- [ ] Load-тесты должны запускаться против staging (не production)
- [ ] Метрики load-теста должны сохраняться для сравнения между прогонами

**Референс-модуль:** `tests/e2e/test_tire_search.py`, `tests/integration/test_pipeline.py`

**Цель:** Понять существующую тестовую инфраструктуру и спроектировать расширение.

**Заметки для переиспользования:** -

---

### 3.1 AudioSocket test client

Для полноценного E2E-тестирования нужен TCP-клиент, симулирующий AudioSocket-соединение от Asterisk.

- [ ] Создать `tests/helpers/audiosocket_client.py` — async TCP-клиент AudioSocket протокола
- [ ] Реализовать отправку UUID пакета (type 0x01) при подключении
- [ ] Реализовать отправку аудио-пакетов (type 0x10) из WAV-файлов (16kHz, 16-bit PCM LE)
- [ ] Реализовать приём аудио-ответов от сервера
- [ ] Реализовать отправку hangup (type 0x00)
- [ ] Добавить таймауты и обработку ошибок
- [ ] Создать тестовые WAV-файлы: `tests/fixtures/audio/` с записями фраз на украинском
- [ ] Написать unit-тесты для самого клиента

**Файлы:** `tests/helpers/audiosocket_client.py`, `tests/fixtures/audio/`
**Заметки:** Формат AudioSocket: `[type:1B][length:2B BE][payload:NB]`. Аудио: 16kHz, 16-bit signed linear PCM, little-endian.

---

### 3.2 Расширение E2E-тестов

- [ ] Создать `tests/e2e/test_full_call_flow.py` — полный цикл звонка через AudioSocket test client
- [ ] Тест-сценарий: подключение → приветствие бота → запрос шин → получение результатов → hangup
- [ ] Тест-сценарий: подключение → запрос несуществующего товара → graceful ответ
- [ ] Тест-сценарий: подключение → запрос оператора → transfer_to_operator
- [ ] Создать `tests/e2e/test_fitting_booking.py` — бронирование шиномонтажа
- [ ] Создать `tests/e2e/test_knowledge_base.py` — консультация через RAG
- [ ] Создать `tests/e2e/test_error_scenarios.py` — таймауты, обрыв соединения, circuit breaker
- [ ] Добавить pytest marker `@pytest.mark.e2e` для всех E2E-тестов
- [ ] Обновить `pyproject.toml` — добавить marker и настройки для E2E
- [ ] Все E2E-тесты должны работать против staging-окружения (из фазы 2)

**Файлы:** `tests/e2e/`, `pyproject.toml`
**Заметки:** E2E-тесты зависят от staging. Если STT/TTS мокированы в staging, тесты проверяют pipeline без реального аудио.

---

### 3.3 Реализация load-тестов

- [ ] Дописать `tests/load/locustfile.py` — полноценный load-тест
- [ ] Реализовать `AudioSocketUser(User)` — кастомный Locust user с TCP-соединением
- [ ] Реализовать сценарий: concurrent calls с случайными запросами из предопределённого набора
- [ ] Добавить профили нагрузки в `tests/load/profiles/`:
  - `normal.json` — 20 concurrent calls, 30 min, ramp-up 5 min
  - `peak.json` — 50 concurrent calls, 15 min, ramp-up 3 min
  - `stress.json` — 100+ concurrent calls, 10 min (поиск точки деградации)
- [ ] Добавить сбор метрик: latency (p50, p95, p99), error rate, throughput (calls/min)
- [ ] Создать `scripts/run_load_test.sh` — запуск load-теста с выбранным профилем
- [ ] Добавить интеграцию с Prometheus: push метрик load-теста для визуализации в Grafana
- [ ] Создать Grafana dashboard для load-тестов: `grafana/dashboards/load-test.json`

**Файлы:** `tests/load/`, `scripts/run_load_test.sh`, `grafana/dashboards/`
**Заметки:** NFR targets: p95 < 2s (normal), p95 < 3s (peak), 0% errors (normal), <5% errors (peak).

---

### 3.4 Документация результатов тестирования

- [ ] Создать `doc/testing/test-strategy.md` — описание стратегии тестирования (unit → integration → E2E → load)
- [ ] Задокументировать как запускать каждый тип тестов
- [ ] Задокументировать ожидаемые результаты и пороги для load-тестов
- [ ] Добавить раздел в README.md о тестировании
- [ ] Создать шаблон `doc/testing/load-test-report-template.md` для отчётов по load-тестам

**Файлы:** `doc/testing/`
**Заметки:** Отчёт по load-тесту должен включать: дату, профиль, результаты (p50/p95/p99/errors), вывод (pass/fail).

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
   git commit -m "checklist(production-readiness): phase-3 testing and load completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 4
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
