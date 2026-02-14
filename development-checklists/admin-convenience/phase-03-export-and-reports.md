# Фаза 3: Экспорт данных и отчётность

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы
Дать администратору возможность экспортировать данные в CSV/PDF.
Настроить автоматическую отправку отчётов по email через Celery.

## Задачи

### 3.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Изучить `src/api/analytics.py` — существующие эндпоинты аналитики, форматы ответов
- [x] Изучить `src/tasks/daily_stats.py` — как агрегируются данные, структура `daily_stats`
- [x] Изучить модели данных: calls, call_turns, call_tool_calls — для экспорта
- [x] Проверить наличие библиотек: `csv` (stdlib), нужен ли `reportlab`/`weasyprint` для PDF

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
- [x] Нужны ли новые пакеты? (`weasyprint` для PDF, `aiosmtplib` для email)
- [x] Нужны ли новые env variables? (SMTP_HOST, SMTP_PORT, REPORT_RECIPIENTS)
- [x] Нужны ли миграции БД? (нет)

**Новые пакеты:** `weasyprint` (PDF), `aiosmtplib` (email), `jinja2` (шаблоны отчётов)
**Новые env variables:** `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `REPORT_RECIPIENTS`
**Миграции БД:** нет

#### C. Проверка архитектуры
- [x] CSV-экспорт — streaming response или генерация в памяти? (streaming для больших объёмов)
- [x] PDF — серверный рендеринг через HTML-шаблон
- [x] Email-отчёты — Celery задача по расписанию

**Референс-модуль:** `src/api/analytics.py` (паттерн API), `src/tasks/daily_stats.py` (паттерн scheduled task)

**Цель:** Определить формат экспортируемых данных и технологию генерации PDF.

**Заметки для переиспользования:** -

---

### 3.1 CSV-экспорт звонков через API
- [x] Добавить эндпоинт `GET /analytics/calls/export` — возвращает CSV с Content-Type `text/csv`
- [x] Поддержать те же фильтры, что и `GET /analytics/calls` (date_from, date_to, scenario, transferred, min_quality)
- [x] Колонки CSV: call_id, started_at, duration_sec, caller_id (маскированный), scenario, transferred, transfer_reason, quality_score, total_cost
- [x] Streaming response через `StreamingResponse` для больших объёмов (>1000 записей)
- [x] Ограничение: максимум 10000 записей за один запрос
- [x] Имя файла: `calls_YYYY-MM-DD_YYYY-MM-DD.csv`
- [x] Написать тесты: `tests/unit/test_export.py`

**Файлы:** `src/api/analytics.py` (или новый `src/api/export.py`), `tests/unit/test_export.py`
**Заметки:** PII (caller_id) маскировать через `pii_sanitizer` в экспорте. CSV через `csv.writer` из stdlib.

---

### 3.2 CSV-экспорт статистики
- [x] Добавить эндпоинт `GET /analytics/summary/export` — дневная статистика за период в CSV
- [x] Колонки: date, total_calls, resolved_by_bot, transferred, avg_duration_sec, avg_quality, total_cost, top_scenario
- [x] Имя файла: `daily_stats_YYYY-MM-DD_YYYY-MM-DD.csv`
- [x] Написать тесты

**Файлы:** `src/api/analytics.py`, `tests/unit/test_export.py`
**Заметки:** Данные из таблицы `daily_stats`. Простой запрос с фильтром по дате.

---

### 3.3 Кнопки экспорта в Admin UI
- [x] Добавить кнопку «Экспорт CSV» на страницу журнала звонков
- [x] Добавить кнопку «Экспорт CSV» на дашборд (статистика)
- [x] При клике — скачивать файл через `window.location` или `fetch` + `Blob`
- [x] Передавать текущие фильтры в запрос экспорта

**Файлы:** `admin-ui/index.html`
**Заметки:** Для скачивания через JS: `fetch()` → `response.blob()` → `URL.createObjectURL()` → `<a download>`.

---

### 3.4 PDF-отчёт: шаблон
- [x] Создать HTML-шаблон отчёта: `src/reports/templates/weekly_report.html`
- [x] Шаблон включает: период, сводка (звонки, resolved %, transfers, quality, cost), таблица по дням, топ-5 причин трансферов
- [x] Использовать Jinja2 для рендеринга шаблона
- [x] Стилизация: минимальный CSS для печати (A4-совместимый)

**Файлы:** `src/reports/__init__.py`, `src/reports/templates/weekly_report.html`
**Заметки:** HTML → PDF через `weasyprint`. Шаблон на русском языке.

---

### 3.5 PDF-генератор
- [x] Создать `src/reports/generator.py` — функция `generate_weekly_report(date_from, date_to) -> bytes`
- [x] Запрос данных из `daily_stats` за указанный период
- [x] Рендеринг HTML через Jinja2 → конвертация в PDF через weasyprint
- [x] Возвращает PDF как bytes
- [x] Написать тесты: `tests/unit/test_report_generator.py`

**Файлы:** `src/reports/generator.py`, `tests/unit/test_report_generator.py`
**Заметки:** Weasyprint требует системные зависимости (cairo, pango). Добавить в `Dockerfile`.

---

### 3.6 API-эндпоинт для скачивания PDF
- [x] Добавить `GET /analytics/report/pdf?date_from=...&date_to=...` — возвращает PDF
- [x] Content-Type: `application/pdf`
- [x] Имя файла: `report_YYYY-MM-DD_YYYY-MM-DD.pdf`
- [x] Добавить кнопку «Скачать PDF» на дашборд в Admin UI

**Файлы:** `src/api/analytics.py`, `admin-ui/index.html`
**Заметки:** -

---

### 3.7 Email-отчёт по расписанию
- [x] Создать `src/tasks/email_report.py` — Celery-задача `send_weekly_report()`
- [x] Расписание: каждый понедельник в 09:00 по Киеву
- [x] Генерирует PDF за прошедшую неделю (пн-вс)
- [x] Отправляет на адреса из `REPORT_RECIPIENTS` (env variable, через запятую)
- [x] Использовать `aiosmtplib` для отправки
- [x] Добавить конфигурацию SMTP в `src/config.py`: `SMTPSettings`
- [x] Написать тесты (с mock SMTP)

**Файлы:** `src/tasks/email_report.py`, `src/config.py`, `tests/unit/test_email_report.py`
**Заметки:** Паттерн — аналогично `src/tasks/daily_stats.py`. Celery beat schedule.

---

### 3.8 CLI: команды экспорта
- [x] Добавить `call-center-admin export calls --date-from ... --date-to ... --format csv --output calls.csv`
- [x] Добавить `call-center-admin export report --date-from ... --date-to ... --output report.pdf`
- [x] Переиспользовать логику из `src/reports/generator.py`

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
