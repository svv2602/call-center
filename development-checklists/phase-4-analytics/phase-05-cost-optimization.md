# Фаза 5: Оптимизация расходов

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Снизить стоимость обработки звонка на 20%+: миграция на self-hosted Whisper (STT), расширенное кэширование TTS, model routing (Haiku для простых запросов, Sonnet/Opus для сложных).

## Задачи

### 5.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить текущую стоимость звонка (STT + LLM + TTS)
- [ ] Изучить абстракции STT/TTS (Protocol) для замены реализации
- [ ] Проверить кэш TTS (какие фразы кэшируются, hit rate)

**Команды для поиска:**
```bash
grep -rn "class.*Protocol\|class.*STTEngine\|class.*TTSEngine" src/
grep -rn "cache\|Cache\|CACHE" src/tts/
grep -rn "model.*haiku\|model.*sonnet\|model.*opus" src/
```

#### B. Анализ зависимостей
- [ ] Faster-Whisper для self-hosted STT (GPU сервер)
- [ ] Абстракция STTEngine уже есть — нужна новая реализация
- [ ] LLM routing: анализ первой фразы → выбор модели

**Новые абстракции:** `WhisperSTT` (реализация STTEngine)
**Новые env variables:** `WHISPER_MODEL_SIZE`, `WHISPER_DEVICE`, `LLM_ROUTING_ENABLED`
**Новые tools:** Нет
**Миграции БД:** Нет

**Референс-модуль:** `doc/development/phase-4-analytics.md` — секция 4.5

**Цель:** Определить стратегию оптимизации и ожидаемую экономию.

**Заметки для переиспользования:** Абстракция STTEngine позволяет seamless замену

---

### 5.1 Self-hosted Whisper (Faster-Whisper)

- [ ] Создать `src/stt/whisper_stt.py` — реализация `STTEngine` через Faster-Whisper
- [ ] Модель: `large-v3` (лучшее качество для украинского)
- [ ] Batch-обработка (не streaming) — задержка ~300-500ms
- [ ] Адаптация pipeline для batch STT (буферизация аудио → распознавание)
- [ ] Сравнение качества: Whisper vs Google STT для uk-UA и ru-RU
- [ ] Docker контейнер с GPU для Whisper

**Файлы:** `src/stt/whisper_stt.py`, `docker-compose.yml`

**Сравнение:**
| Параметр | Google STT | Faster-Whisper |
|----------|------------|----------------|
| Стоимость | ~$900/мес | ~$150/мес (GPU) |
| Качество UA | Отличное | Отличное (large-v3) |
| Задержка | ~200ms (streaming) | ~300-500ms (batch) |
| Code-switching | Хорошее | Лучше |

**Заметки:** Мигрировать после 300+ звонков/день, экономия ~$750/мес

---

### 5.2 Расширенное кэширование TTS

- [ ] Анализ логов: определить самые частые ответы бота
- [ ] Расширить список кэшированных фраз (динамический кэш)
- [ ] Кэширование в Redis (shared между инстансами)
- [ ] TTL для кэша: 24 часа (или до смены промпта)
- [ ] Метрика: cache hit rate → цель >30%
- [ ] Ожидаемая экономия: 20-30% расходов на TTS

**Файлы:** `src/tts/google_tts.py`
**Заметки:** -

---

### 5.3 LLM Model Routing

- [ ] Создать `src/agent/model_router.py`
- [ ] Правила routing:
  - Простые запросы (статус заказа, наличие) → Claude Haiku (дешевле, быстрее)
  - Сложные (консультация, оформление заказа, сравнение) → Claude Sonnet
  - Routing по анализу первой фразы клиента
- [ ] Fallback: если Haiku не справляется → retry с Sonnet
- [ ] Метрики: % звонков по моделям, стоимость по моделям
- [ ] Ожидаемая экономия: 30-40% расходов на LLM

**Файлы:** `src/agent/model_router.py`, `src/agent/agent.py`
**Заметки:** -

---

### 5.4 Мониторинг стоимости

- [ ] Расчёт стоимости каждого звонка (STT + LLM + TTS)
- [ ] Сохранение в calls.cost_breakdown (JSONB) и calls.total_cost_usd
- [ ] Grafana панель: средняя стоимость звонка (трендовый график)
- [ ] Алерт: аномальный расход (>200% от среднего)
- [ ] Ежедневный отчёт в daily_stats.total_cost_usd

**Файлы:** `src/monitoring/cost_tracker.py`
**Заметки:** -

---

### 5.5 Конфигурация переключения провайдеров

- [ ] Feature flag для переключения STT: Google ↔ Whisper
- [ ] Feature flag для model routing: on/off
- [ ] Постепенный rollout: 10% → 50% → 100%
- [ ] A/B тест: Google STT vs Whisper (качество, задержка)

**Файлы:** `src/config.py`
**Заметки:** -

---

### 5.6 Оценка экономии

- [ ] Baseline: текущая стоимость обработки звонка
- [ ] После Whisper: новая стоимость STT
- [ ] После кэширования TTS: новая стоимость TTS
- [ ] После model routing: новая стоимость LLM
- [ ] Итоговая экономия: цель ≥ 20% от baseline

**Файлы:** Документация / Grafana dashboard
**Заметки:** -

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
