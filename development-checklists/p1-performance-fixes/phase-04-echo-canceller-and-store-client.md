# Phase 04 — Echo Canceller Optimization and Store Client Tuning

## Commit message
```
perf: optimize echo canceller RMS with struct.unpack, tune store client retry delays
```

## Tasks

- [x] 4.1 Optimize `_compute_rms` in `echo_canceller.py`: replace `array.array` with `struct.unpack` for bulk sample extraction
- [x] 4.2 Reduce store client retry delays from `[1.0, 2.0]` to `[0.5, 1.0]` for faster recovery during calls
- [x] 4.3 Add `pool_recycle=1800` to the shared DB engine in `src/api/database.py` to prevent stale connections
- [x] 4.4 Add ONEC_SOAP_TIMEOUT config validation: warn if > 30s (interactive calls shouldn't wait that long)
