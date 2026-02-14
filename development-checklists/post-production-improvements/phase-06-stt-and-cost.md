# Фаза 6: STT & Cost Optimization

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы
Подготовить rollout Whisper STT для экономии на STT (~$600/мес при масштабировании) и провести анализ стоимости звонка. После этой фазы Whisper STT готов к включению через feature flag, а стоимость звонка оптимизирована.

## Задачи

### 6.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Изучить `src/stt/base.py` — абстрактный интерфейс STT
- [ ] Изучить `src/stt/google_stt.py` — реализация Google Cloud STT
- [ ] Изучить `src/stt/whisper_stt.py` — существующая реализация Whisper (если есть)
- [ ] Изучить `src/config.py` — FeatureFlagSettings, `FF_STT_PROVIDER`
- [ ] Изучить `src/tts/google_tts.py` — TTS кеширование (паттерн для оптимизации)

**Команды для поиска:**
```bash
# STT модули
ls src/stt/
cat src/stt/base.py
# Feature flags
grep -rn "FF_\|feature_flag\|FeatureFlag" src/config.py
# Whisper
grep -rn "whisper\|Whisper" src/
# TTS кеширование
grep -rn "cache\|Cache" src/tts/
```

#### B. Анализ зависимостей
- [ ] Нужен ли GPU-сервер для Whisper? — Да (или API endpoint)
- [ ] Нужны ли новые env variables? — `WHISPER_API_URL`, `WHISPER_MODEL_SIZE`
- [ ] Нужны ли изменения в pipeline? — Минимальные (уже абстрагировано через Protocol)
- [ ] Нужен ли A/B тест STT провайдеров? — Да (feature flag)

**Новые env variables:** `WHISPER_API_URL`, `WHISPER_MODEL_SIZE`, `FF_STT_PROVIDER`
**Зависимости:** GPU-сервер или whisper API endpoint

#### C. Проверка архитектуры
- [ ] STT Protocol уже определён — реализовать для Whisper
- [ ] Feature flag `FF_STT_PROVIDER` для переключения
- [ ] Метрики: latency, accuracy comparison
- [ ] Fallback: если Whisper down → Google STT

**Референс-модуль:** `src/stt/google_stt.py` (паттерн реализации STT)

**Цель:** Понять существующие паттерны проекта ПЕРЕД написанием кода.

**Заметки для переиспользования:** -

---

### 6.1 Whisper STT подготовка

- [ ] Проверить/доработать `src/stt/whisper_stt.py` — реализация Whisper STT через Protocol:
  - Streaming через WebSocket или chunked HTTP
  - Поддержка `uk-UA` и `ru-RU`
  - Возврат результата в том же формате что Google STT
- [ ] Добавить fallback: если Whisper недоступен → автопереключение на Google STT
- [ ] Добавить Prometheus метрики: `stt_whisper_latency_seconds`, `stt_whisper_errors_total`
- [ ] Добавить A/B метрику: `stt_provider_accuracy` — для сравнения Google vs Whisper
- [ ] Unit-тесты для Whisper STT client

**Файлы:** `src/stt/whisper_stt.py`, `src/monitoring/metrics.py`, `tests/unit/test_whisper_stt.py`
**Заметки:** Whisper сервер (faster-whisper или whisper.cpp) разворачивается отдельно, этот модуль — клиент

---

### 6.2 Анализ стоимости звонка

- [ ] Создать скрипт `scripts/cost_analysis.py`:
  - Подсчёт стоимости STT (Google vs Whisper) на 1000 звонков
  - Подсчёт стоимости TTS на 1000 звонков
  - Подсчёт стоимости LLM (Claude API) на 1000 звонков
  - Подсчёт стоимости инфраструктуры (серверы, SIP)
  - Итого: стоимость 1 звонка, ROI vs оператор
- [ ] Создать `doc/business/cost-analysis.md` — результаты анализа:
  - Breakdown по компонентам
  - Сравнение Google STT vs Whisper
  - Рекомендации по оптимизации (TTS caching hit rate, prompt optimization)
  - Target: $0.15-0.30 per call

**Файлы:** `scripts/cost_analysis.py`, `doc/business/cost-analysis.md`
**Заметки:** Цены: Google STT ~$0.006/15s, Claude Sonnet ~$3/$15 per 1M tokens, Google TTS ~$4/1M chars

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
   git commit -m "checklist(post-production-improvements): phase-6 STT and cost optimization completed"
   ```
5. Обнови PROGRESS.md:
   - Все фазы завершены
   - Добавь запись в историю
6. Все фазы завершены!
