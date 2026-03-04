# Phase 03 — Timeout and Loop Fixes

## Commit message
```
fix: correct off-by-one in tool loop, align OpenAI timeout with pipeline
```

## Tasks

- [ ] 3.1 Fix off-by-one in `agent.py`: change `while tool_call_count <= MAX_TOOL_CALLS_PER_TURN` to `< MAX_TOOL_CALLS_PER_TURN`
- [ ] 3.2 Fix off-by-one in `streaming_loop.py`: change `while tool_round <= self._max_tool_rounds` to `< self._max_tool_rounds`
- [ ] 3.3 Align OpenAI provider timeout: change `_REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=60)` to `total=35` in `openai_compat.py` (pipeline timeout is 30s, add 5s margin)
- [ ] 3.4 Replace naive `asyncio.sleep(AUDIO_FRAME_DURATION_MS / 1000)` pacing in `audio_socket.py` with monotonic clock-based pacing to prevent drift
