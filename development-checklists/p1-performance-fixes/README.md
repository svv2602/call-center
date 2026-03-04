# P1 Performance Fixes

Performance and resource management improvements for the Call Center AI backend.

## Phases

1. **phase-01-db-pool-consolidation.md** — Consolidate 20+ per-router DB engines into one shared pool
2. **phase-02-tts-cache-and-queue-limits.md** — Bound TTS cache, add STT queue maxsize, fix docstring
3. **phase-03-timeout-and-loop-fixes.md** — Fix off-by-one in tool loop, align OpenAI timeout, fix audio pacing
4. **phase-04-echo-canceller-and-store-client.md** — Optimize echo canceller RMS, tune store client retries

## Commit message template
```
perf: <phase description>
```
