# Фаза 5: Тестирование заказов

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Полное тестирование функциональности заказов: unit-тесты новых tools, тесты Store Client, adversarial-тесты безопасности заказов, интеграционные и E2E тесты.

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить существующие тесты из phase-1 (паттерны, fixtures)
- [ ] Проверить mock-реализации
- [ ] Определить новые тестовые сценарии

**Команды для поиска:**
```bash
ls tests/unit/ tests/integration/
grep -rn "def test_.*order\|def test_.*caller" tests/
```

#### B. Анализ зависимостей
- [ ] Существующие fixtures из phase-1 можно переиспользовать
- [ ] Новые mocks для order endpoints

**Референс-модуль:** Существующие тесты из phase-1

**Цель:** Определить полный план тестирования заказов.

**Заметки для переиспользования:** Fixtures из phase-1 тестов

---

### 5.1 Unit-тесты: Order Tools

- [ ] Тест schema get_order_status (phone и/или order_id)
- [ ] Тест schema create_order_draft (валидация items, quantity)
- [ ] Тест schema update_order_delivery (delivery vs pickup)
- [ ] Тест schema confirm_order (payment_method enum)
- [ ] Тест валидации: quantity = 0 → отклонение
- [ ] Тест валидации: quantity > 100 → отклонение
- [ ] Тест валидации: некорректный phone формат

**Файлы:** `tests/unit/test_order_tools.py`
**Заметки:** -

---

### 5.2 Unit-тесты: Store Client (заказы)

- [ ] Тест search_orders — корректный маппинг
- [ ] Тест get_order — полная информация
- [ ] Тест create_order — Idempotency-Key генерируется
- [ ] Тест update_delivery — доставка и самовывоз
- [ ] Тест confirm_order — Idempotency-Key, sms_sent
- [ ] Тест повторного запроса с тем же Idempotency-Key → тот же ответ

**Файлы:** `tests/unit/test_store_client_orders.py`
**Заметки:** Использовать `aioresponses`

---

### 5.3 Unit-тесты: CallerID

- [ ] Тест получения CallerID через ARI
- [ ] Тест скрытого CallerID → None
- [ ] Тест ARI недоступен → ошибка обработана
- [ ] Тест интеграции CallerID в CallSession

**Файлы:** `tests/unit/test_caller_id.py`
**Заметки:** -

---

### 5.4 Adversarial-тесты: безопасность заказов

- [ ] Запрос чужого заказа (phone не совпадает с CallerID) → отказ
- [ ] Заказ с quantity=10000 → отклонение, предложение оператора
- [ ] Заказ с quantity=0 → отклонение
- [ ] Попытка confirm_order без предварительного подтверждения → агент сначала озвучивает итого
- [ ] Отмена заказа в середине оформления → черновик удалён
- [ ] Prompt injection во время оформления → агент продолжает нормально

**Файлы:** `tests/unit/test_adversarial_orders.py`
**Заметки:** -

---

### 5.5 E2E тесты: заказы

- [ ] SIP-звонок → "Де мій заказ?" → бот озвучивает статус
- [ ] SIP-звонок → подбор шин → "Замовити" → полный цикл оформления
- [ ] SIP-звонок → "Скасуй замовлення" → отмена на этапе доставки

**Файлы:** `tests/e2e/test_orders.py`
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
   git commit -m "checklist(phase-2-orders): phase-5 testing completed"
   ```
6. Обнови PROGRESS.md — Фаза 2 Заказы завершена!
