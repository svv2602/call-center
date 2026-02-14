# Прогресс выполнения

## Текущий статус
- **Последнее обновление:** 2026-02-14
- **Текущая фаза:** 5 из 6
- **Статус фазы:** не начата
- **Общий прогресс:** 38/54 задач (70%)

## Как продолжить работу
1. Открой файл текущей фазы: `phase-05-operational-tooling.md`
2. Найди первую незавершённую задачу (без [x])
3. Выполни задачу
4. Отметь [x] в чекбоксе
5. Обнови этот файл (PROGRESS.md)

## Сводка по фазам

| Фаза | Файл | Задач | Статус |
|------|------|-------|--------|
| 1. Валидация конфига и CLI | `phase-01-config-and-cli.md` | 8 | завершена |
| 2. Интеграция Admin UI | `phase-02-admin-ui-integration.md` | 11 | завершена |
| 3. Экспорт и отчётность | `phase-03-export-and-reports.md` | 9 | завершена |
| 4. RBAC и безопасность | `phase-04-rbac-and-security.md` | 10 | завершена |
| 5. Операционный инструментарий | `phase-05-operational-tooling.md` | 9 | не начата |
| 6. Управление операторами | `phase-06-operator-management.md` | 7 | не начата |

## История выполнения
| Дата | Событие |
|------|---------|
| 2026-02-14 | Проект создан |
| 2026-02-14 | Фаза 1 завершена: валидация конфига, CLI-скелет (version, config, db, stats, calls, prompts), автобэкапы PostgreSQL, обновление Makefile |
| 2026-02-14 | Фаза 2 завершена: Admin UI подключён к API — авторизация (JWT expiry, 401 redirect), dashboard (auto-refresh 30s), журнал звонков (фильтры, пагинация, search, модальное окно деталей), промпты (CRUD, активация, A/B тесты), база знаний (CRUD, activate/deactivate), settings (health/ready), UX (toast, spinners, breadcrumbs, Escape для модалов, сохранение активной вкладки) |
| 2026-02-14 | Фаза 3 завершена: CSV-экспорт звонков и статистики (streaming, фильтры, PII masking), PDF-отчёт (Jinja2+WeasyPrint, A4 шаблон), кнопки экспорта в Admin UI, email-отчёт по расписанию (Celery+aiosmtplib, понедельник 09:00), CLI export calls/report, SMTPSettings в config |
| 2026-02-14 | Фаза 4 завершена: RBAC (require_role с admin/analyst/operator), миграция admin_users+admin_audit_log, auth через БД с fallback на env, bcrypt хэширование, защита эндпоинтов по ролям (analytics/prompts/knowledge/export), аудит-лог middleware, API управления пользователями (CRUD+reset-password), Admin UI страницы пользователей и аудит-лога, JWT TTL конфиг, rate limiting на login, логирование неудачных входов |
