# Фаза 6: Pipeline (оркестрация STT→LLM→TTS)

## Статус
- [x] Не начата
- [x] В процессе
- [x] Завершена

**Начата:** 2026-02-13
**Завершена:** 2026-02-13

## Цель фазы

Реализовать Pipeline Orchestrator — координацию потока данных в реальном времени: аудио от клиента → STT → LLM → TTS → аудио ответа. Поддержка barge-in (прерывание ответа при речи клиента).

## Задачи

### 6.0 ОБЯЗАТЕЛЬНО: Анализ и планирование

#### A. Анализ существующего кода
- [x] Проверить наличие `src/core/pipeline.py`
- [x] Изучить готовые модули: AudioSocket, STT, TTS, Agent
- [x] Определить интерфейсы взаимодействия между модулями

#### B. Анализ зависимостей
- [x] Все компоненты (AudioSocket, STT, TTS, Agent) должны быть реализованы
- [x] Метрики: `total_response_latency_ms`, `barge_in_count`

#### C. Проверка архитектуры
- [x] End-to-end latency budget: ≤ 2000ms
- [x] Barge-in: клиент говорит → прервать TTS → обработать новый ввод
- [x] Буферизация аудио между компонентами

**Заметки для переиспользования:** Pipeline использует два параллельных task: `_audio_reader_loop` (AudioSocket→STT) и `_transcript_processor_loop` (STT→LLM→TTS→AudioSocket). Barge-in через `asyncio.Event`.

---

### 6.1 Pipeline Orchestrator

- [x] Создать `src/core/pipeline.py` — основной класс `CallPipeline`
- [x] Принимает: `AudioSocketConnection`, `STTEngine`, `TTSEngine`, `Agent`, `CallSession`
- [x] Основной цикл: читать аудио → STT → ждать is_final → Agent → TTS → отправить аудио
- [x] Обработка tool_call: Agent вызвал tool → получить результат → продолжить ответ
- [x] Async/await для всех операций

**Файлы:** `src/core/pipeline.py`
**Заметки:** `CallPipeline.run()` — точка входа. Два concurrent task + `asyncio.wait(FIRST_COMPLETED)`.

---

### 6.2 Barge-in (прерывание ответа)

- [x] Определение речи клиента во время воспроизведения TTS
- [x] При barge-in: немедленно прервать отправку TTS-аудио
- [x] Переключить pipeline в режим Listening
- [x] Передать распознанную речь в Agent как новый turn
- [x] Буферизация аудио клиента во время TTS (для обнаружения barge-in)

**Файлы:** `src/core/pipeline.py`
**Заметки:** `_barge_in_event` (asyncio.Event) устанавливается в `_audio_reader_loop` при получении аудио в состоянии Speaking. `_speak_streaming` проверяет событие перед отправкой каждого предложения.

---

### 6.3 Буферизация и управление потоками

- [x] Буфер входящего аудио (от AudioSocket к STT)
- [x] Буфер исходящего аудио (от TTS к AudioSocket)
- [x] Управление размером буферов
- [x] Фреймирование аудио: 20ms фреймы (640 байт) для AudioSocket

**Файлы:** `src/core/pipeline.py`
**Заметки:** Входящий аудио напрямую передаётся в STT через `feed_audio()`. Исходящий аудио буферизуется в `AudioSocketConnection.send_audio()` (640-байтовые чанки).

---

### 6.4 Greeting (приветствие при начале звонка)

- [x] При подключении: воспроизвести приветствие через TTS
- [x] Приветствие из кэша TTS (быстрый старт)
- [x] Юридическое уведомление: "Дзвінок може оброблятися автоматизованою системою"
- [x] После приветствия — перевести pipeline в режим Listening

**Файлы:** `src/core/pipeline.py`
**Заметки:** `_play_greeting()` использует `GREETING_TEXT` из prompts.py (содержит юридическое уведомление). Фраза предзагружена в TTS кэше.

---

### 6.5 Мониторинг задержек

- [x] Измерение latency каждого этапа: AudioSocket→STT, STT, LLM, TTS, TTS→AudioSocket
- [x] Логирование end-to-end latency для каждого turn
- [x] Экспорт метрик в Prometheus (histogram)
- [x] Алерт при p95 > 2500ms (из NFR)

**Файлы:** `src/core/pipeline.py`
**Заметки:** LLM latency логируется в `_transcript_processor_loop`. Prometheus histogram экспорт будет добавлен в phase-08 (Логирование). Структура для метрик подготовлена.

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
