# Фаза 5: Тестирование сервисов

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Тестирование шиномонтажа, базы знаний (RAG), комплексных сценариев. Unit, integration, E2E тесты.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить существующие тесты из фаз 1-2
- [ ] Определить новые тестовые сценарии для фазы 3

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

- [ ] Тест schema get_fitting_stations (city required)
- [ ] Тест schema get_fitting_slots (station_id required)
- [ ] Тест schema book_fitting (station_id, date, time, phone required)
- [ ] Тест cancel_fitting (booking_id, action required)
- [ ] Тест get_fitting_price (tire_diameter required)

**Файлы:** `tests/unit/test_fitting_tools.py`
**Заметки:** -

---

### 5.2 Unit-тесты: Store Client (fitting)

- [ ] Тест get_fitting_stations — маппинг
- [ ] Тест get_fitting_slots — фильтрация доступных
- [ ] Тест create_booking — все поля
- [ ] Тест cancel_booking — обработка ошибок
- [ ] Тест reschedule_booking — PATCH
- [ ] Тест get_fitting_prices — по диаметру

**Файлы:** `tests/unit/test_store_client_fitting.py`
**Заметки:** -

---

### 5.3 Unit-тесты: RAG / Knowledge Base

- [ ] Тест chunking статей (разбиение на фрагменты)
- [ ] Тест генерации embeddings (mock API)
- [ ] Тест векторного поиска (cosine similarity)
- [ ] Тест search_knowledge_base tool — маппинг результатов
- [ ] Тест fallback при недоступности pgvector
- [ ] Тест пустого результата поиска

**Файлы:** `tests/unit/test_knowledge_base.py`
**Заметки:** -

---

### 5.4 Тесты комплексных сценариев

- [ ] Тест цепочки: search_tires → create_order_draft → book_fitting(linked_order_id)
- [ ] Тест: клиент отказывается от записи на монтаж после заказа
- [ ] Тест: экспертная консультация с search_knowledge_base
- [ ] Тест: сложный вопрос → переключение на менеджера

**Файлы:** `tests/unit/test_complex_scenarios.py`
**Заметки:** Мокированные tools, проверка порядка вызовов

---

### 5.5 E2E тесты: шиномонтаж

- [ ] SIP-звонок → "Хочу записатися на шиномонтаж" → полный цикл записи
- [ ] SIP-звонок → подбор шин → заказ → запись на монтаж (комплексный)
- [ ] SIP-звонок → "Що краще Michelin чи Continental?" → консультация

**Файлы:** `tests/e2e/test_services.py`
**Заметки:** -

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
