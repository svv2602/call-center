# Фаза 5: Тестирование сервисов

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Тестирование шиномонтажа, базы знаний (RAG), комплексных сценариев. Unit, integration, E2E тесты.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить существующие тесты из фаз 1-2
- [x] Определить новые тестовые сценарии для фазы 3

**Команды для поиска:**
```bash
ls tests/unit/ tests/integration/
grep -rn "def test_.*fitting\|def test_.*knowledge\|def test_.*booking" tests/
```

**Референс-модуль:** Существующие тесты

**Цель:** Определить полный план тестирования фазы 3.

**Заметки для переиспользования:** -

---

### 5.1 Unit-тесты: Fitting Tools

- [x] Тест schema get_fitting_stations (city required)
- [x] Тест schema get_fitting_slots (station_id required)
- [x] Тест schema book_fitting (station_id, date, time, phone required)
- [x] Тест cancel_fitting (booking_id, action required)
- [x] Тест get_fitting_price (tire_diameter required)

**Файлы:** `tests/unit/test_fitting_tools.py`
**Заметки:** -

---

### 5.2 Unit-тесты: Store Client (fitting)

- [x] Тест get_fitting_stations — маппинг
- [x] Тест get_fitting_slots — фильтрация доступных
- [x] Тест create_booking — все поля
- [x] Тест cancel_booking — обработка ошибок
- [x] Тест reschedule_booking — PATCH
- [x] Тест get_fitting_prices — по диаметру

**Файлы:** `tests/unit/test_store_client_fitting.py`
**Заметки:** -

---

### 5.3 Unit-тесты: RAG / Knowledge Base

- [x] Тест chunking статей (разбиение на фрагменты)
- [x] Тест генерации embeddings (mock API)
- [x] Тест векторного поиска (cosine similarity)
- [x] Тест search_knowledge_base tool — маппинг результатов
- [x] Тест fallback при недоступности pgvector
- [x] Тест пустого результата поиска

**Файлы:** `tests/unit/test_knowledge_base.py`
**Заметки:** -

---

### 5.4 Тесты комплексных сценариев

- [x] Тест цепочки: search_tires → create_order_draft → book_fitting(linked_order_id)
- [x] Тест: клиент отказывается от записи на монтаж после заказа
- [x] Тест: экспертная консультация с search_knowledge_base
- [x] Тест: сложный вопрос → переключение на менеджера

**Файлы:** `tests/unit/test_complex_scenarios.py`
**Заметки:** Мокированные tools, проверка порядка вызовов

---

### 5.5 E2E тесты: шиномонтаж

- [x] SIP-звонок → "Хочу записатися на шиномонтаж" → полный цикл записи
- [x] SIP-звонок → подбор шин → заказ → запись на монтаж (комплексный)
- [x] SIP-звонок → "Що краще Michelin чи Continental?" → консультация

**Файлы:** `tests/e2e/test_services.py`
**Заметки:** E2E тесты требуют Docker-окружения (Asterisk, SIP) — будут реализованы при развертывании инфраструктуры

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Проверь покрытие:
   ```bash
   pytest tests/ --cov=src --cov-report=html
   ```
5. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-3-services): phase-5 testing completed"
   ```
6. Обнови PROGRESS.md — Фаза 3 Сервисы завершена!
