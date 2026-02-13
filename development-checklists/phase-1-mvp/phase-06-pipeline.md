# Фаза 6: Pipeline (оркестрация STT→LLM→TTS)

## Статус
- [ ] Не начата
- [ ] В процессе
- [ ] Завершена

**Начата:** -
**Завершена:** -

## Цель фазы

Реализовать Pipeline Orchestrator — координацию потока данных в реальном времени: аудио от клиента → STT → LLM → TTS → аудио ответа. Поддержка barge-in (прерывание ответа при речи клиента).

## Задачи

### 6.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [ ] Проверить наличие `src/core/pipeline.py`
- [ ] Изучить готовые модули: AudioSocket, STT, TTS, Agent
- [ ] Определить интерфейсы взаимодействия между модулями

**Команды для поиска:**
```bash
ls src/core/
grep -rn "pipeline\|Pipeline\|orchestrat" src/
grep -rn "barge.in\|interrupt" src/
```

#### B. Анализ зависимостей
- [ ] Все компоненты (AudioSocket, STT, TTS, Agent) должны быть реализованы
- [ ] Метрики: `total_response_latency_ms`, `barge_in_count`

**Новые абстракции:** Нет
**Новые env variables:** Нет
**Новые tools:** Нет
**Миграции БД:** Нет

#### C. Проверка архитектуры
- [ ] End-to-end latency budget: ≤ 2000ms (AudioSocket→STT ≤50ms, STT ≤500ms, LLM ≤1000ms, TTS ≤400ms, TTS→AudioSocket ≤50ms)
- [ ] Barge-in: клиент говорит → прервать TTS → обработать новый ввод
- [ ] Буферизация аудио между компонентами

**Референс-модуль:** `doc/technical/architecture.md` — секция 3.3, `doc/technical/nfr.md` — секция 1.2

**Цель:** Определить архитектуру потоков данных и стратегию barge-in.

**Заметки для переиспользования:** -

---

### 6.1 Pipeline Orchestrator

- [ ] Создать `src/core/pipeline.py` — основной класс `CallPipeline`
- [ ] Принимает: `AudioSocketConnection`, `STTEngine`, `TTSEngine`, `Agent`, `CallSession`
- [ ] Основной цикл: читать аудио → STT → ждать is_final → Agent → TTS → отправить аудио
- [ ] Обработка tool_call: Agent вызвал tool → получить результат → продолжить ответ
- [ ] Async/await для всех операций

**Файлы:** `src/core/pipeline.py`

**Поток данных:**
```
AudioSocket (аудио вход)
    │
    ▼
STT Streaming (Google) ──interim──► [можно: "Так, шукаю..."]
    │
    │ is_final=True
    ▼
LLM Agent (Claude)
    │
    ├── text response ──► TTS ──► AudioSocket (аудио выход)
    │
    └── tool_call ──► Store API ──► LLM (продолжение) ──► TTS
```

**Заметки:** -

---

### 6.2 Barge-in (прерывание ответа)

- [ ] Определение речи клиента во время воспроизведения TTS
- [ ] При barge-in: немедленно прервать отправку TTS-аудио
- [ ] Переключить pipeline в режим Listening
- [ ] Передать распознанную речь в Agent как новый turn
- [ ] Буферизация аудио клиента во время TTS (для обнаружения barge-in)

**Файлы:** `src/core/pipeline.py`
**Заметки:** STT продолжает работать во время воспроизведения TTS

---

### 6.3 Буферизация и управление потоками

- [ ] Буфер входящего аудио (от AudioSocket к STT)
- [ ] Буфер исходящего аудио (от TTS к AudioSocket)
- [ ] Управление размером буферов
- [ ] Фреймирование аудио: 20ms фреймы (640 байт) для AudioSocket

**Файлы:** `src/core/pipeline.py`
**Заметки:** Аудио: 16kHz × 16-bit = 32000 bytes/sec. Фрейм 20ms = 640 bytes.

---

### 6.4 Greeting (приветствие при начале звонка)

- [ ] При подключении: воспроизвести приветствие через TTS
- [ ] Приветствие из кэша TTS (быстрый старт)
- [ ] Юридическое уведомление: "Дзвінок може оброблятися автоматизованою системою"
- [ ] После приветствия — перевести pipeline в режим Listening

**Файлы:** `src/core/pipeline.py`
**Заметки:** Уведомление об автоматической обработке — юридическое требование

---

### 6.5 Мониторинг задержек

- [ ] Измерение latency каждого этапа: AudioSocket→STT, STT, LLM, TTS, TTS→AudioSocket
- [ ] Логирование end-to-end latency для каждого turn
- [ ] Экспорт метрик в Prometheus (histogram)
- [ ] Алерт при p95 > 2500ms (из NFR)

**Файлы:** `src/core/pipeline.py`
**Заметки:** Бюджет задержки из `doc/technical/nfr.md` — секция 1.2

---

## При завершении фазы

1. Убедись, что все задачи отмечены [x]
2. Измени статус фазы: [x] Завершена
3. Заполни дату "Завершена: YYYY-MM-DD"
4. Выполни коммит:
   ```bash
   git add .
   git commit -m "checklist(phase-1-mvp): phase-6 pipeline completed"
   ```
5. Обнови PROGRESS.md
6. Открой следующую фазу: `phase-07-store-client.md`
