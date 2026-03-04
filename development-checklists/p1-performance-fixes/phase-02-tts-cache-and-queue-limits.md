# Phase 02 — TTS Cache Bounds and STT Queue Limits

## Commit message
```
perf: bound TTS cache with LRU eviction, add STT queue maxsize
```

## Tasks

- [x] 2.1 Add `cachetools` to `pyproject.toml` dependencies
- [x] 2.2 Replace unbounded `self._cache: dict` in `GoogleTTSEngine` with `cachetools.LRUCache(maxsize=200)`
- [x] 2.3 Add `maxsize=100` to `asyncio.Queue()` calls in `GoogleSTTEngine.__init__` for both audio and transcript queues
- [x] 2.4 Fix audio_socket.py docstring: "16 kHz" -> "8 kHz" (line 8 says 16kHz but AUDIO_SAMPLE_RATE=8000)
