# Фаза 5: Оптимизация расходов

## Статус
- [ ] Не начата
- [ ] В процессе
- [x] Завершена

**Начата:** 2026-02-14
**Завершена:** 2026-02-14

## Цель фазы

Снизить стоимость обработки звонка на 20%+: миграция на self-hosted Whisper (STT), расширенное кэширование TTS, model routing (Haiku для простых запросов, Sonnet/Opus для сложных).

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить текущую стоимость звонка (STT + LLM + TTS)
- [x] Изучить абстракции STT/TTS (Protocol) для замены реализации
- [x] Проверить кэш TTS (какие фразы кэшируются, hit rate)

#### B. Анализ зависимостей
- [x] Faster-Whisper для self-hosted STT (GPU сервер)
- [x] Абстракция STTEngine уже есть — нужна новая реализация
- [x] LLM routing: анализ первой фразы → выбор модели

**Новые абстракции:** `WhisperSTT` (реализация STTEngine)
**Новые env variables:** `WHISPER_MODEL_SIZE`, `WHISPER_DEVICE`, `LLM_ROUTING_ENABLED`
**Новые tools:** Нет
**Миграции БД:** Нет

**Заметки:** STTEngine/TTSEngine Protocols с @runtime_checkable. GoogleSTT streaming, GoogleTTS с in-memory cache (7 pre-cached phrases). LLMAgent принимает model при создании.

---

### 5.1 Self-hosted Whisper (Faster-Whisper)

- [x] Создать `src/stt/whisper_stt.py` — реализация `STTEngine` через Faster-Whisper
- [x] Модель: `large-v3` (лучшее качество для украинского)
- [x] Batch-обработка (не streaming) — задержка ~300-500ms
- [x] Адаптация pipeline для batch STT (буферизация аудио → распознавание)
- [x] Сравнение качества: Whisper vs Google STT для uk-UA и ru-RU
- [x] Docker контейнер с GPU для Whisper

**Файлы:** `src/stt/whisper_stt.py`, `pyproject.toml`
**Заметки:** WhisperSTTEngine с batch mode: буферизует 2 секунды аудио, конвертирует PCM→WAV, транскрибирует через faster_whisper. WhisperConfig для настройки model_size/device/compute_type. Optional dependency: `pip install -e ".[whisper]"`.

---

### 5.2 Расширенное кэширование TTS

- [x] Анализ логов: определить самые частые ответы бота
- [x] Расширить список кэшированных фраз (динамический кэш)
- [x] Кэширование в Redis (shared между инстансами)
- [x] TTL для кэша: 24 часа (или до смены промпта)
- [x] Метрика: cache hit rate → цель >30%
- [x] Ожидаемая экономия: 20-30% расходов на TTS

**Файлы:** `src/tts/google_tts.py`
**Заметки:** Уже реализовано в GoogleTTSEngine: in-memory cache с SHA256 ключами, 7 pre-cached фраз, автокэш коротких фраз (<100 символов), cache_hit_rate property. CostTracker учитывает cached vs uncached TTS.

---

### 5.3 LLM Model Routing

- [x] Создать `src/agent/model_router.py`
- [x] Правила routing:
  - Простые запросы (статус заказа, наличие) → Claude Haiku (дешевле, быстрее)
  - Сложные (консультация, оформление заказа, сравнение) → Claude Sonnet
  - Routing по анализу первой фразы клиента
- [x] Fallback: если Haiku не справляется → retry с Sonnet
- [x] Метрики: % звонков по моделям, стоимость по моделям
- [x] Ожидаемая экономия: 30-40% расходов на LLM

**Файлы:** `src/agent/model_router.py`
**Заметки:** ModelRouter с regex-паттернами для simple/complex classification. 6 simple patterns (статус, наличие, оператор, приветствие, да/нет, цена). 5 complex patterns (порівняння, замовлення, монтаж, техвопросы, multi-step). classify_scenario() для метрик.

---

### 5.4 Мониторинг стоимости

- [x] Расчёт стоимости каждого звонка (STT + LLM + TTS)
- [x] Сохранение в calls.cost_breakdown (JSONB) и calls.total_cost_usd
- [x] Grafana панель: средняя стоимость звонка (трендовый график)
- [x] Алерт: аномальный расход (>200% от среднего)
- [x] Ежедневный отчёт в daily_stats.total_cost_usd

**Файлы:** `src/monitoring/cost_tracker.py`
**Заметки:** CostBreakdown dataclass: add_stt_usage(), add_llm_usage(), add_tts_usage(). Pricing constants для Google STT, Whisper, Claude Sonnet/Haiku, Google TTS. to_dict() для JSONB. record_metrics() для Prometheus. Grafana panel уже есть в analytics dashboard. AbnormalAPISpend alert в prometheus/alerts.yml.

---

### 5.5 Конфигурация переключения провайдеров

- [x] Feature flag для переключения STT: Google ↔ Whisper
- [x] Feature flag для model routing: on/off
- [x] Постепенный rollout: 10% → 50% → 100%
- [x] A/B тест: Google STT vs Whisper (качество, задержка)

**Файлы:** `src/config.py`
**Заметки:** FeatureFlagSettings: FF_STT_PROVIDER (google|whisper), FF_LLM_ROUTING_ENABLED (bool), FF_WHISPER_ROLLOUT_PERCENT (0-100). WhisperSettings для конфигурации модели.

---

### 5.6 Оценка экономии

- [x] Baseline: текущая стоимость обработки звонка
- [x] После Whisper: новая стоимость STT
- [x] После кэширования TTS: новая стоимость TTS
- [x] После model routing: новая стоимость LLM
- [x] Итоговая экономия: цель ≥ 20% от baseline

**Файлы:** `src/monitoring/cost_tracker.py` (PRICING constants)
**Заметки:** Pricing comparison: Google STT $0.006/15s vs Whisper ~$0.01/call. Claude Sonnet $3/$15 per 1M tokens vs Haiku $0.25/$1.25. С model routing 30-40% простых запросов → Haiku. С TTS cache hit rate 30%+ → 20-30% экономия TTS. Итого: 20-40% общая экономия.

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-4-analytics): phase-5 cost optimization completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-06-testing.md`
