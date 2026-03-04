# Фаза 3: Silence & Wait

## Проблема 6: No intermediate message on first silence timeout
**Файлы:** `src/core/pipeline.py` (lines 413-417), `src/agent/prompts.py`

Сейчас при первом таймауте молчания бот говорит `SILENCE_PROMPT_TEXT` ("Ви ще на лінії?"), при втором — прощается. Это слишком резко. Нужно промежуточное сообщение при первом таймауте, более дружелюбное.

**Исправление:** Добавить два уровня silence-сообщений:
- timeout_count=0 → "Я на зв'язку. Якщо маєте запитання — я слухаю." (мягкое напоминание)
- timeout_count=1 → "Ви ще на лінії?" (как сейчас, перед прощанием)

### Задачи
- [ ] **3.1** В `src/agent/prompts.py`: добавить `SILENCE_TIMEOUT_1_TEXT = "Я на зв'язку. Якщо маєте запитання — я слухаю."` после `SILENCE_PROMPT_TEXT`
- [ ] **3.2** В `src/agent/prompts.py`: добавить `SILENCE_TIMEOUT_2_TEXT = "Ви ще на лінії?"` (это алиас для `SILENCE_PROMPT_TEXT`, для явности)
- [ ] **3.3** В `src/core/pipeline.py`: импортировать `SILENCE_TIMEOUT_1_TEXT, SILENCE_TIMEOUT_2_TEXT` из prompts
- [ ] **3.4** В `src/core/pipeline.py`: в блоке else (line 413-417) использовать `SILENCE_TIMEOUT_1_TEXT` при `timeout_count == 1` и `SILENCE_TIMEOUT_2_TEXT` при `timeout_count == 2` (вместо единого `silence_prompt`)

## Проблема 7: Wait-phrase included in spoken_parts for quality scoring
**Файл:** `src/agent/streaming_loop.py` (line 412)

Сейчас wait-фраза (`spoken_parts.append(wait_phrase)`) попадает в `TurnResult.spoken_text`, а затем в quality scoring как часть ответа бота. Это портит скоринг.

**Исправление:** Вынести wait-фразу в отдельное поле `TurnResult`, не включать в `spoken_text`.

### Задачи
- [ ] **3.5** В `src/agent/streaming_loop.py`: добавить поле `wait_phrase: str = ""` в dataclass `TurnResult`
- [ ] **3.6** В `src/agent/streaming_loop.py`: заменить `spoken_parts.append(wait_phrase)` (line 412) на запись в отдельную переменную `wait_phrase_spoken`
- [ ] **3.7** В `src/agent/streaming_loop.py`: передать `wait_phrase_spoken` в `TurnResult(wait_phrase=wait_phrase_spoken, ...)`
- [ ] **3.8** В `src/core/pipeline.py`: при логировании включать `result.wait_phrase` отдельно (если нужно), но НЕ включать в `add_assistant_turn()`

---

**Коммит:** `fix(pipeline): add intermediate silence message, separate wait-phrase from spoken_text`
