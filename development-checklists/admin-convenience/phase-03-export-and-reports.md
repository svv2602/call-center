# Фаза 3: Экспорт данных и отчётность

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Дать администратору возможность экспортировать данные в CSV/PDF.
Настроить автоматическую отправку отчётов по email через Celery.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `src/api/analytics.py` — существующие эндпоинты аналитики, форматы ответов
- [ ] Изучить `src/tasks/daily_stats.py` — как агрегируются данные, структура `daily_stats`
- [ ] Изучить модели данных: calls, call_turns, call_tool_calls — для экспорта
- [ ] Проверить наличие библиотек: `csv` (stdlib), нужен ли `reportlab`/`weasyprint` для PDF

**Команды для поиска:**
```bash
# Формат daily_stats
grep -rn "daily_stats\|DailyStat" src/
# Модели данных
grep -rn "class Call\b\|class CallTurn\|class DailyStat" src/
# Существующие зависимости
grep -rn "reportlab\|weasyprint\|jinja2" pyproject.toml requirements*.txt
```

#### B. Анализ зависимостей
- [ ] Нужны ли новые пакеты? (`weasyprint` для PDF, `aiosmtplib` для email)
- [ ] Нужны ли новые env variables? (SMTP_HOST, SMTP_PORT, REPORT_RECIPIENTS)
- [ ] Нужны ли миграции БД? (нет)

**Новые пакеты:** `weasyprint` (PDF), `aiosmtplib` (email), `jinja2` (шаблоны отчётов)
**Новые env variables:** `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `REPORT_RECIPIENTS`
**Миграции БД:** нет

#### C. Проверка архитектуры
- [ ] CSV-экспорт — streaming response или генерация в памяти? (streaming для больших объёмов)
- [ ] PDF — серверный рендеринг через HTML-шаблон
- [ ] Email-отчёты — Celery задача по расписанию

**Референс-модуль:** `src/api/analytics.py` (паттерн API), `src/tasks/daily_stats.py` (паттерн scheduled task)

**Цель:** Определить формат экспортируемых данных и технологию генерации PDF.

**Заметки для переиспользования:** -

---

### 3.1 CSV-экспорт звонков через API
- [ ] Добавить эндпоинт `GET /analytics/calls/export` — возвращает CSV с Content-Type `text/csv`
- [ ] Поддержать те же фильтры, что и `GET /analytics/calls` (date_from, date_to, scenario, transferred, min_quality)
- [ ] Колонки CSV: call_id, started_at, duration_sec, caller_id (маскированный), scenario, transferred, transfer_reason, quality_score, total_cost
- [ ] Streaming response через `StreamingResponse` для больших объёмов (>1000 записей)
- [ ] Ограничение: максимум 10000 записей за один запрос
- [ ] Имя файла: `calls_YYYY-MM-DD_YYYY-MM-DD.csv`
- [ ] Написать тесты: `tests/unit/test_export.py`

**Файлы:** `src/api/analytics.py` (или новый `src/api/export.py`), `tests/unit/test_export.py`
**Заметки:** PII (caller_id) маскировать через `pii_sanitizer` в экспорте. CSV через `csv.writer` из stdlib.

---

### 3.2 CSV-экспорт статистики
- [ ] Добавить эндпоинт `GET /analytics/summary/export` — дневная статистика за период в CSV
- [ ] Колонки: date, total_calls, resolved_by_bot, transferred, avg_duration_sec, avg_quality, total_cost, top_scenario
- [ ] Имя файла: `daily_stats_YYYY-MM-DD_YYYY-MM-DD.csv`
- [ ] Написать тесты

**Файлы:** `src/api/analytics.py`, `tests/unit/test_export.py`
**Заметки:** Данные из таблицы `daily_stats`. Простой запрос с фильтром по дате.

---

### 3.3 Кнопки экспорта в Admin UI
- [ ] Добавить кнопку «Экспорт CSV» на страницу журнала звонков
- [ ] Добавить кнопку «Экспорт CSV» на дашборд (статистика)
- [ ] При клике — скачивать файл через `window.location` или `fetch` + `Blob`
- [ ] Передавать текущие фильтры в запрос экспорта

**Файлы:** `admin-ui/index.html`
**Заметки:** Для скачивания через JS: `fetch()` → `response.blob()` → `URL.createObjectURL()` → `<a download>`.

---

### 3.4 PDF-отчёт: шаблон
- [ ] Создать HTML-шаблон отчёта: `src/reports/templates/weekly_report.html`
- [ ] Шаблон включает: период, сводка (звонки, resolved %, transfers, quality, cost), таблица по дням, топ-5 причин трансферов
- [ ] Использовать Jinja2 для рендеринга шаблона
- [ ] Стилизация: минимальный CSS для печати (A4-совместимый)

**Файлы:** `src/reports/__init__.py`, `src/reports/templates/weekly_report.html`
**Заметки:** HTML → PDF через `weasyprint`. Шаблон на русском языке.

---

### 3.5 PDF-генератор
- [ ] Создать `src/reports/generator.py` — функция `generate_weekly_report(date_from, date_to) -> bytes`
- [ ] Запрос данных из `daily_stats` за указанный период
- [ ] Рендеринг HTML через Jinja2 → конвертация в PDF через weasyprint
- [ ] Возвращает PDF как bytes
- [ ] Написать тесты: `tests/unit/test_report_generator.py`

**Файлы:** `src/reports/generator.py`, `tests/unit/test_report_generator.py`
**Заметки:** Weasyprint требует системные зависимости (cairo, pango). Добавить в `Dockerfile`.

---

### 3.6 API-эндпоинт для скачивания PDF
- [ ] Добавить `GET /analytics/report/pdf?date_from=...&date_to=...` — возвращает PDF
- [ ] Content-Type: `application/pdf`
- [ ] Имя файла: `report_YYYY-MM-DD_YYYY-MM-DD.pdf`
- [ ] Добавить кнопку «Скачать PDF» на дашборд в Admin UI

**Файлы:** `src/api/analytics.py`, `admin-ui/index.html`
**Заметки:** -

---

### 3.7 Email-отчёт по расписанию
- [ ] Создать `src/tasks/email_report.py` — Celery-задача `send_weekly_report()`
- [ ] Расписание: каждый понедельник в 09:00 по Киеву
- [ ] Генерирует PDF за прошедшую неделю (пн-вс)
- [ ] Отправляет на адреса из `REPORT_RECIPIENTS` (env variable, через запятую)
- [ ] Использовать `aiosmtplib` для отправки
- [ ] Добавить конфигурацию SMTP в `src/config.py`: `SMTPSettings`
- [ ] Написать тесты (с mock SMTP)

**Файлы:** `src/tasks/email_report.py`, `src/config.py`, `tests/unit/test_email_report.py`
**Заметки:** Паттерн — аналогично `src/tasks/daily_stats.py`. Celery beat schedule.

---

### 3.8 CLI: команды экспорта
- [ ] Добавить `call-center-admin export calls --date-from ... --date-to ... --format csv --output calls.csv`
- [ ] Добавить `call-center-admin export report --date-from ... --date-to ... --output report.pdf`
- [ ] Переиспользовать логику из `src/reports/generator.py`

**Файлы:** `src/cli/export.py`, `tests/unit/test_cli_export.py`
**Заметки:** -

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
   git commit -m "checklist(admin-convenience): phase-3 export and reports completed"
   ```
5. Обнови PROGRESS.md:
   - Текущая фаза: 4
   - Добавь запись в историю
6. Открой следующую фазу и продолжи работу
