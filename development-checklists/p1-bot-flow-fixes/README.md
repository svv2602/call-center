# Исправления бот-флоу (Pipeline/Agent)

## Цель
Исправить 7 выявленных проблем в pipeline обработки звонков, которые влияют на качество диалога: потеря контекста при сжатии истории, ложные barge-in от эха, отсутствие промежуточных сообщений при таймауте молчания, низкий порог релевантности паттернов, wait-фразы в качественном скоринге, буферизация множественных транскриптов, невидимый fallback тенанта.

## Фазы работы
1. **History & Context** — fix summarize-before-trim bug, raise pattern threshold
2. **Barge-in & Echo** — add suppression window after TTS, buffer multiple transcripts
3. **Silence & Wait** — add intermediate silence timeout message, separate wait-phrase from spoken_parts
4. **Tenant & Logging** — tenant fallback WARNING log

## Источник требований
Анализ продакшен-логов и живых звонков (2026-03).

## Начало работы
Для начала или продолжения работы прочитай PROGRESS.md
