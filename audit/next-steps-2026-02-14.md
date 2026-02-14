# Анализ следующих шагов — Call Center AI

**Дата:** 2026-02-14
**Статус проекта:** Production-ready (MVP + Phases 2-3 реализованы)

## Текущее состояние

| Область | Статус | Деталь |
|---------|--------|--------|
| Core pipeline (AudioSocket → STT → LLM → TTS) | ✅ Реализовано | 261 строка, barge-in, silence timeout |
| STT (Google Cloud + Whisper) | ✅ Реализовано | Streaming, multilingual (uk-UA, ru-RU) |
| TTS (Google Cloud Neural2) | ✅ Реализовано | Кеширование фраз, sentence-level streaming |
| LLM Agent (Claude API) | ✅ Реализовано | Tool calling, PII masking, 40 msg context |
| Store API client | ✅ Реализовано | Circuit breaker, retry, idempotency |
| Все 13 tools | ✅ Реализовано | search_tires → search_knowledge_base |
| Database (8 миграций) | ✅ Реализовано | Monthly partitioning, pgvector |
| Admin UI | ✅ Реализовано | WebSocket, responsive, RBAC |
| CI/CD | ✅ Реализовано | lint → test → security → build → staging |
| Тесты | ✅ 479 тестов / 61 файл | Unit, integration, E2E, load |
| Документация | ✅ 40+ документов | Business, technical, security, operations |
| Monitoring | ✅ Реализовано | Prometheus + Grafana + AlertManager |
| Security hardening | ✅ Реализовано | Rate limiting, headers, CORS, bandit, gitleaks |

**Вывод:** Код полностью реализован. Критических стабов нет. Проект можно выводить в production.

---

## Рекомендуемые следующие шаги

### Приоритет 1 — КРИТИЧНО (до production deploy)

#### 1.1 Интеграция с реальным Asterisk
- **Что:** Настроить Asterisk 20, extensions.conf с AudioSocket, провести звонок через SIP-провайдер
- **Почему:** Всё тестировалось с mock AudioSocket. Реальный Asterisk может иметь отличия в таймингах и формате пакетов
- **Объём:** 2-3 дня
- **Файлы:** `asterisk/extensions.conf`, `asterisk/pjsip.conf` (в Docker)

#### 1.2 Запуск load-тестов и фиксация baseline
- **Что:** Запустить `scripts/run_load_test.sh normal`, зафиксировать p95 latency, error rate, throughput
- **Почему:** NFR targets (p95 < 2s, 50 concurrent) не подтверждены на реальной инфраструктуре
- **Объём:** 1 день
- **Команда:** `./scripts/run_load_test.sh normal` → `./scripts/run_load_test.sh peak`

#### 1.3 Настройка production-окружения
- **Что:** Сервер, DNS, SSL, VPN между серверами (AudioSocket не поддерживает TLS)
- **Почему:** Документация готова (`doc/development/deployment-guide.md`), нужно выполнить
- **Объём:** 2-3 дня
- **Зависимости:** Выбор хостинга, домен, SIP-провайдер

#### 1.4 Первый реальный звонок
- **Что:** Тестовый звонок через SIP-провайдер → Asterisk → Call Processor
- **Почему:** Валидация всего pipeline end-to-end
- **Объём:** 1 день (после 1.1 и 1.3)

### Приоритет 2 — ВАЖНО (первая неделя после launch)

#### 2.1 Мониторинг и тюнинг промптов
- **Что:** Анализ первых 100 звонков — качество ответов, причины transfer_to_operator
- **Почему:** Промпт работает на тестовых данных, реальные клиенты могут задавать непредвиденные вопросы
- **Объём:** Ongoing
- **Инструменты:** Admin UI → Call Journal, quality_score, A/B tests

#### 2.2 A/B тестирование промптов
- **Что:** Создать v2 промпта с улучшениями по итогам анализа, запустить 50/50 split
- **Почему:** Итеративное улучшение качества диалога
- **Объём:** 1-2 дня на подготовку, 1 неделя на сбор данных
- **Файлы:** `src/agent/prompts.py`, Admin UI → Prompts → A/B Tests

#### 2.3 Настройка email-отчётов
- **Что:** Настроить SMTP и включить еженедельный PDF-отчёт
- **Почему:** Celery task готов (`src/tasks/email_report.py`), нужна конфигурация
- **Объём:** 0.5 дня
- **Env:** `SMTP_HOST`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_REPORT_RECIPIENTS`

#### 2.4 Проверка retention policy
- **Что:** Убедиться что `data_retention` Celery task корректно удаляет старые данные
- **Почему:** GDPR compliance — транскрипции хранятся 90 дней
- **Объём:** 0.5 дня

### Приоритет 3 — УЛУЧШЕНИЯ (1-3 месяца)

#### 3.1 Kubernetes manifests
- **Что:** Создать Deployment, Service, Ingress, HPA для K8s
- **Почему:** docker-compose работает, но K8s даёт auto-scaling и resilience
- **Объём:** 3-5 дней
- **Альтернатива:** Docker Swarm, если K8s overkill

#### 3.2 JWT blacklist (полноценный logout)
- **Что:** Реализовать Redis-backed JWT blacklist при logout
- **Почему:** Сейчас logout не инвалидирует токен (MVР compromise)
- **Объём:** 1 день
- **Файлы:** `src/api/auth.py`

#### 3.3 Модуляризация admin UI
- **Что:** Разделить `admin-ui/index.html` (1400+ строк) на компоненты, добавить build pipeline
- **Почему:** Maintainability — сейчас всё в одном файле
- **Объём:** 3-5 дней
- **Подход:** Vite + vanilla JS modules или lightweight framework

#### 3.4 Whisper STT rollout
- **Что:** Включить Whisper STT на часть трафика (feature flag `FF_STT_PROVIDER=whisper`)
- **Почему:** Экономия ~$600/мес vs Google STT при масштабировании
- **Объём:** 2-3 дня (настройка GPU-сервера, тестирование)
- **Файлы:** `src/stt/whisper_stt.py`, `src/config.py` (FeatureFlagSettings)

#### 3.5 Расширение PII sanitizer
- **Что:** Добавить маскирование email, адресов, номеров карт
- **Почему:** Сейчас маскируются только телефоны и имена
- **Объём:** 1 день
- **Файлы:** `src/logging/pii_sanitizer.py`

### Приоритет 4 — NICE TO HAVE

#### 4.1 Grafana dashboards as code
- **Что:** Экспортировать Grafana дашборды в JSON, хранить в repo
- **Почему:** Reproducibility, version control

#### 4.2 Chaos testing
- **Что:** Тесты на отказ Redis, PostgreSQL, Store API — проверить graceful degradation
- **Почему:** Circuit breaker и fallback-и написаны, но не тестировались в production-подобных условиях

#### 4.3 Cost optimization
- **Что:** Анализ стоимости на 1000 звонков, оптимизация (кеширование TTS, batch STT)
- **Почему:** ROI зависит от стоимости звонка ($0.15-0.30 target)

#### 4.4 Автоматическое тестирование качества
- **Что:** Регрессионные тесты с записанными диалогами — проверка что новый промпт не ухудшает ответы
- **Почему:** Защита от regression при обновлении промптов

---

## Оценка готовности к production

| Критерий | Статус |
|----------|--------|
| Код написан и работает | ✅ |
| Тесты проходят | ✅ |
| CI/CD настроен | ✅ |
| Security hardening | ✅ |
| Monitoring + alerts | ✅ |
| Документация | ✅ |
| Backup + restore | ✅ |
| Staging environment | ✅ |
| **Asterisk интеграция** | ⚠️ Нужно настроить |
| **Load test baseline** | ⚠️ Нужно запустить |
| **Production сервер** | ⚠️ Нужно развернуть |

**Estimated time to production: 1-2 недели** (при наличии сервера и SIP-провайдера).
