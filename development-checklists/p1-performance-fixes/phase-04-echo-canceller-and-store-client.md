# Phase 04 — Echo Canceller Optimization and Store Client Tuning

## Commit message
```
perf: optimize echo canceller RMS with array.array, tune store client retry delays
```

## Tasks

- [ ] 4.1 Optimize `_compute_rms` in `echo_canceller.py`: replace `sum(s * s for s in samples)` with more efficient computation using `array` module or math
- [ ] 4.2 Reduce store client retry delays from `[1.0, 2.0]` to `[0.5, 1.0]` for faster recovery during calls
- [ ] 4.3 Add `pool_recycle=1800` to the shared DB engine in `src/api/database.py` to prevent stale connections
- [ ] 4.4 Add ONEC_SOAP_TIMEOUT config validation: warn if > 30s (interactive calls shouldn't wait that long)
