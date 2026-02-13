# Чек-лист развёртывания в продакшен

## Перед первым запуском (MVP)

### Инфраструктура

- [ ] Сервер Call Processor: 4+ vCPU, 8+ GB RAM, 50+ GB SSD, Ubuntu 22.04
- [ ] Сетевой доступ от Asterisk к Call Processor (порт 9092, LAN)
- [ ] Сетевой доступ от Call Processor к интернету (Google API, Anthropic API)
- [ ] DNS / IP настроены и зафиксированы
- [ ] Firewall: закрыты все порты кроме необходимых (9092 — только LAN, 8080 — только LAN, 3000 — Grafana за reverse proxy)

### Секреты и аккаунты

- [ ] Google Cloud: проект создан, Speech-to-Text API и Text-to-Speech API включены
- [ ] Google Cloud: Service Account создан с минимальными ролями
- [ ] Google Cloud: JSON-ключ сгенерирован и размещён в `secrets/`
- [ ] Anthropic: API-ключ получен, лимиты проверены (достаточно для 500 зв/день)
- [ ] Все секреты в `.env` (НЕ в git), права файла `600`
- [ ] `.env` и `secrets/` в `.gitignore`

### Asterisk

- [ ] AudioSocket модуль загружен: `module show like audiosocket`
- [ ] Dialplan обновлён (`extensions.conf`): входящие → AudioSocket
- [ ] ARI включён и настроен (`ari.conf`)
- [ ] Контекст `transfer-to-operator` создан
- [ ] Очередь операторов создана и протестирована
- [ ] Тестовый звонок через AudioSocket → Call Processor проходит
- [ ] fail2ban настроен для SIP
- [ ] SIP-пароли достаточной сложности
- [ ] IP whitelist для SIP trunk

### Приложение

- [ ] Docker images собраны и протестированы
- [ ] `docker compose up -d` — все сервисы стартуют без ошибок
- [ ] Health check: `GET /health` возвращает 200
- [ ] Readiness check: `GET /health/ready` возвращает 200 (все зависимости доступны)
- [ ] Call Processor принимает AudioSocket-соединения
- [ ] STT: тестовое аудио корректно распознаётся
- [ ] TTS: тестовый текст корректно синтезируется
- [ ] LLM: тестовый запрос возвращает адекватный ответ
- [ ] Store API: тестовые запросы (search, availability) работают
- [ ] Переключение на оператора работает

### База данных

- [ ] PostgreSQL запущен, миграции применены (`alembic upgrade head`)
- [ ] pgvector расширение установлено (для фазы 3)
- [ ] Пользователь БД с минимальными правами (не superuser)
- [ ] Бэкап настроен и протестирован (pg_dump → восстановление)
- [ ] Redis запущен, TTL на сессиях работает

### Мониторинг

- [ ] Prometheus собирает метрики с Call Processor
- [ ] Grafana дашборд настроен (активные звонки, задержки, ошибки)
- [ ] Алерты настроены (ошибки > 5/10 мин, задержка p95 > 3 сек, CPU > 80%)
- [ ] Канал оповещений работает (Telegram/Slack)
- [ ] Логирование: structured JSON logs, уровень INFO в проде

### Тестирование

- [ ] Unit-тесты проходят: `pytest tests/unit/`
- [ ] Интеграционные тесты проходят: `pytest tests/integration/`
- [ ] 5+ тестовых звонков выполнены вручную (разные сценарии)
- [ ] Нагрузочный тест: 20 одновременных звонков — задержка < 2 сек, 0 ошибок
- [ ] Prompt injection тесты пройдены (агент не раскрывает промпт, не оформляет на 0 грн)

### Безопасность

- [ ] pip-audit: 0 критических/высоких уязвимостей
- [ ] PII sanitizer подключен к логгеру
- [ ] API-ключи НЕ в логах (проверить: `grep -r "sk-ant" logs/`)
- [ ] AudioSocket доступен только из LAN
- [ ] PostgreSQL доступен только из Docker network
- [ ] Бот произносит уведомление об обработке звонка автоматизированной системой в начале разговора

---

## Перед каждым релизом

- [ ] Все тесты (unit + integration) проходят в CI
- [ ] Security scan (pip-audit) пройден
- [ ] Docker image собран и протестирован на staging
- [ ] E2E тест на staging: минимум 3 сценария (подбор, наличие, transfer)
- [ ] Changelog обновлён
- [ ] Версия промпта зафиксирована в БД

---

## После запуска (первые 24 часа)

- [ ] Мониторинг: все метрики в норме
- [ ] Первые 10 звонков: прослушать транскрипции, проверить качество
- [ ] % переключений на оператора < 50%
- [ ] Средняя задержка < 2 сек
- [ ] Нет ошибок STT/TTS/LLM в логах
- [ ] Бэкап за первый день создан
- [ ] Оператору доступна информация о переключённых звонках (summary от бота)

---

## Откат (Rollback)

Если что-то пошло не так:

```bash
# 1. Переключить весь трафик на операторов (в Asterisk dialplan)
# extensions.conf: закомментировать AudioSocket, раскомментировать Queue

# 2. Остановить Call Processor
docker compose down call-processor

# 3. Откатить на предыдущую версию
git checkout <previous-tag>
docker compose build call-processor
docker compose up -d call-processor

# 4. Или — полный откат на операторов (dialplan без AudioSocket)
```

Время полного отката: **< 5 минут**.
