# Фаза 2: Barge-in & Echo

## Проблема 4: No barge-in suppression window after TTS
**Файл:** `src/core/pipeline.py` (lines 373-394)

Сейчас бот сразу слушает barge-in после окончания TTS. Эхо от динамика (даже с AEC) может триггерить ложный barge-in. Нужно окно подавления 500мс после окончания TTS.

**Исправление:** После `self._speaking = False` добавить задержку, в течение которой barge-in event игнорируется.

### Задачи
- [ ] **2.1** В `src/core/pipeline.py`: в `_speak()` — после `self._speaking = False` (line 731), добавить `self._barge_in_event.clear()` и задержку подавления эха (500мс)
- [ ] **2.2** В `src/core/pipeline.py`: в `_speak_streaming()` — после `self._speaking = False` (line 774), добавить аналогичную очистку barge-in event

## Проблема 5: Multiple barge-in transcripts processed separately
**Файл:** `src/core/pipeline.py` (lines 396-417)

Когда клиент говорит во время TTS — могут прийти несколько финальных транскриптов подряд. Каждый обрабатывается как отдельный turn к LLM, что вызывает дублирование. Нужна буферизация: собираем транскрипты за 500мс окно, конкатенируем, обрабатываем как один turn.

**Исправление:** Добавить буферизацию в `_transcript_processor_loop()` — после получения первого транскрипта, подождать 500мс и собрать все накопившиеся.

### Задачи
- [ ] **2.3** Добавить метод `_drain_transcript_buffer()` в `CallPipeline` — после получения первого транскрипта, опрашивает очередь 500мс и объединяет тексты
- [ ] **2.4** В `_transcript_processor_loop()`: заменить прямое использование `transcript` на вызов `_drain_transcript_buffer(transcript)` для объединения множественных транскриптов
- [ ] **2.5** Объединённый транскрипт: конкатенация через пробел, confidence = средняя, language = от последнего
- [ ] **2.6** Добавить лог при объединении: `logger.info("Merged %d transcripts into one: '%s'", count, merged_text[:50])`

---

**Коммит:** `fix(pipeline): add barge-in suppression window, buffer multiple transcripts`
