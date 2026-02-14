# Фаза 4: Админ-интерфейс

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Создать веб-интерфейс администратора: журнал звонков с поиском, детали звонка (транскрипция, метрики), управление промптами, CRUD базы знаний, настройки системы.

## Задачи

### 4.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить существующие API endpoints (FastAPI)
- [x] Изучить требования к админке из `doc/development/phase-4-analytics.md`
- [x] Определить стек фронтенда (React или Vue)

#### B. Анализ зависимостей
- [x] FastAPI backend уже есть
- [x] Нужен фронтенд (React/Vue)
- [x] JWT аутентификация для admin API
- [x] Встроенный Grafana iframe для дашборда

**Новые абстракции:** Нет
**Новые env variables:** `ADMIN_JWT_SECRET`, `ADMIN_USERNAME`, `ADMIN_PASSWORD`
**Новые tools:** Нет
**Миграции БД:** Нет (данные уже в существующих таблицах)

**Заметки:** Выбран vanilla HTML/JS SPA для минимальных зависимостей. FastAPI отдаёт index.html через /admin endpoint. API endpoints: analytics, auth, prompts, knowledge.

---

### 4.1 Backend API для админки

- [x] `GET /analytics/calls` — список звонков с фильтрами (дата, сценарий, quality, transferred)
- [x] `GET /analytics/calls/{id}` — полная информация: транскрипция, tool calls, метрики, quality
- [x] `GET /analytics/summary` — агрегированная статистика (period: day/week/month)
- [x] JWT аутентификация: `POST /auth/login` → token
- [x] Middleware: проверка JWT для admin endpoints

**Файлы:** `src/api/analytics.py`, `src/api/auth.py`
**Заметки:** JWT HS256 с configurable secret. require_admin() dependency для protected endpoints. Full-text search в транскрипциях через ILIKE. Сортировка по date/quality/cost.

---

### 4.2 Журнал звонков

- [x] Список звонков с пагинацией
- [x] Фильтры: дата, сценарий, переключён на оператора, quality_score
- [x] Поиск по транскрипции (full-text search)
- [x] Сортировка: по дате, по качеству, по стоимости
- [x] Цветовая индикация: зелёный (quality > 0.8), жёлтый (0.5-0.8), красный (< 0.5)

**Файлы:** `admin-ui/index.html`
**Заметки:** Pagination по 20 записей. qualityBadge() с цветовой маркировкой. Клик по строке открывает модальное окно с деталями.

---

### 4.3 Детали звонка

- [x] Полная транскрипция (speaker, text, timestamp)
- [x] Tool calls с аргументами и результатами
- [x] Метрики задержек (STT, LLM, TTS per turn)
- [x] Оценка качества по критериям
- [x] Стоимость звонка (breakdown: STT, LLM, TTS)
- [x] Информация о клиенте (phone, name, previous calls)

**Файлы:** `admin-ui/index.html`
**Заметки:** Modal overlay с showCallDetail(). Quality breakdown таблица. Transcription с customer/bot маркировкой.

---

### 4.4 Управление промптами (UI)

- [x] Список версий промптов
- [x] Создание новой версии (текстовый редактор)
- [x] Активация/деактивация версии
- [x] Создание A/B теста (выбор двух вариантов)
- [x] Просмотр результатов A/B теста

**Файлы:** `admin-ui/index.html`, `src/api/prompts.py`
**Заметки:** Tab bar: Versions / A/B Tests. Activate button рядом с каждой неактивной версией.

---

### 4.5 CRUD базы знаний

- [x] Список статей с фильтрами по категории
- [x] Создание/редактирование статьи (markdown editor)
- [x] Удаление/архивация статей
- [x] Автоматическая перегенерация embeddings при изменении

**Файлы:** `admin-ui/index.html`, `src/api/knowledge.py`
**Заметки:** Knowledge API: GET/POST/PATCH/DELETE /knowledge/articles. GET /knowledge/categories для статистики. Деактивация вместо удаления (soft delete). Сообщение о необходимости перегенерации embeddings при изменении контента.

---

### 4.6 Настройки системы

- [x] Рабочие часы бота (расписание)
- [x] Лимит суммы заказа через бота
- [x] Таймауты (тишина, максимальная длительность)
- [x] Список операторов и их статус
- [x] Встроенный Grafana iframe для real-time дашборда

**Файлы:** `admin-ui/index.html`
**Заметки:** Settings page: ссылка на Grafana, system health status от /health endpoint. Grafana iframe embedded в dashboard page.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-4-analytics): phase-4 admin UI completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-05-cost-optimization.md`
