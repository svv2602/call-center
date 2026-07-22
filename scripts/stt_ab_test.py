"""Offline STT A/B: run both models on a labeled test set, compute WER.

Reads a folder of paired 8 kHz mono LINEAR16 ``*.wav`` + ``*.txt`` files
(txt = reference transcript). Sends each clip to Google Cloud STT v2 with
two model configurations (``latest_long`` on the global endpoint and
``chirp_2`` on a regional endpoint), then computes word error rate against
the reference.

Neither model uses phrase adaptation here — chirp_2 does not support it,
and the goal is to compare raw acoustic quality on real Russian/Ukrainian
speech captured from callers.

Usage (from inside the call-processor container, which has Google creds):

    docker exec call-center-call-processor-1 python -m scripts.stt_ab_test \\
        --input-dir /tmp/stt_wav \\
        --output /tmp/stt_ab_results.csv \\
        --project my-speech-app-487614 \\
        --chirp-location europe-west4

Outputs a CSV with (file, reference, latest_long, chirp_2, wer_ll, wer_c2)
plus per-language and overall averages on stderr.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
import time
import unicodedata
from pathlib import Path

from google.api_core.client_options import ClientOptions
from google.cloud.speech_v2 import SpeechAsyncClient
from google.cloud.speech_v2.types import cloud_speech


def normalize(text: str) -> str:
    """Case-fold, strip punctuation, collapse whitespace for fair WER comparison."""
    text = unicodedata.normalize("NFC", text).lower()
    text = re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def wer(ref: str, hyp: str) -> float:
    """Word error rate = Levenshtein distance over word tokens / len(reference)."""
    r = normalize(ref).split()
    h = normalize(hyp).split()
    if not r:
        return 0.0 if not h else 1.0
    d = [[0] * (len(h) + 1) for _ in range(len(r) + 1)]
    for i in range(len(r) + 1):
        d[i][0] = i
    for j in range(len(h) + 1):
        d[0][j] = j
    for i in range(1, len(r) + 1):
        for j in range(1, len(h) + 1):
            if r[i - 1] == h[j - 1]:
                d[i][j] = d[i - 1][j - 1]
            else:
                d[i][j] = 1 + min(d[i - 1][j], d[i][j - 1], d[i - 1][j - 1])
    return d[len(r)][len(h)] / len(r)


async def transcribe(
    audio_pcm: bytes,
    project: str,
    model: str,
    location: str,
    language_codes: list[str],
) -> str:
    """Batch-recognize one clip and return the concatenated transcript.

    Regional chirp_2 does not support the multi-language mode used by
    global latest_long; pass ``["auto"]`` for chirp_2 (it detects language
    from audio) and ``["uk-UA", "ru-RU"]`` for latest_long (Google's
    documented multi-language config).
    """
    client_opts = (
        ClientOptions(api_endpoint=f"{location}-speech.googleapis.com")
        if location != "global"
        else None
    )
    client = SpeechAsyncClient(client_options=client_opts)
    config = cloud_speech.RecognitionConfig(
        explicit_decoding_config=cloud_speech.ExplicitDecodingConfig(
            encoding=cloud_speech.ExplicitDecodingConfig.AudioEncoding.LINEAR16,
            sample_rate_hertz=8000,
            audio_channel_count=1,
        ),
        language_codes=language_codes,
        model=model,
    )
    recognizer = f"projects/{project}/locations/{location}/recognizers/_"
    req = cloud_speech.RecognizeRequest(
        recognizer=recognizer,
        config=config,
        content=audio_pcm,
    )
    resp = await client.recognize(request=req)
    parts = [
        r.alternatives[0].transcript for r in resp.results if r.alternatives
    ]
    return " ".join(parts).strip()


def strip_wav_header(wav_bytes: bytes) -> bytes:
    """Return raw PCM samples from a canonical WAV file (skip header up to 'data' chunk)."""
    idx = wav_bytes.find(b"data")
    if idx < 0:
        return wav_bytes
    # 'data' + 4-byte size, then samples
    return wav_bytes[idx + 8 :]


def detect_language(filename: str) -> str:
    """Rough language tag from filename ('укр'/'рус' or by speaker name)."""
    low = filename.lower()
    if "укр" in low:
        return "uk"
    if "рус" in low:
        return "ru"
    # Fallback for '19 Катя.txt' style — Катя without 'укр' means Russian in this dataset
    if "катя" in low and "укр" not in low:
        return "ru"
    return "?"


async def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--input-dir", required=True)
    ap.add_argument("--output", default="stt_ab_results.csv")
    ap.add_argument("--project", required=True)
    ap.add_argument("--chirp-location", default="europe-west4")
    ap.add_argument("--concurrency", type=int, default=3)
    args = ap.parse_args()

    input_dir = Path(args.input_dir)
    pairs: list[tuple[Path, str]] = []
    for wav in sorted(input_dir.glob("*.wav")):
        txt = wav.with_suffix(".txt")
        if not txt.exists() or txt.stat().st_size == 0:
            continue
        ref = txt.read_text(encoding="utf-8").strip()
        if not ref:
            continue
        pairs.append((wav, ref))

    print(f"Found {len(pairs)} labeled clips", file=sys.stderr)
    if not pairs:
        sys.exit(1)

    sem = asyncio.Semaphore(args.concurrency)

    async def run_one(wav: Path, ref: str) -> dict[str, object]:
        async with sem:
            pcm = strip_wav_header(wav.read_bytes())
            t0 = time.monotonic()
            try:
                h_ll = await transcribe(
                    pcm,
                    args.project,
                    "latest_long",
                    "global",
                    ["uk-UA", "ru-RU"],
                )
                err_ll = ""
            except Exception as e:
                h_ll = ""
                err_ll = f"{type(e).__name__}: {e}"
            try:
                h_c2 = await transcribe(
                    pcm,
                    args.project,
                    "chirp_2",
                    args.chirp_location,
                    ["auto"],
                )
                err_c2 = ""
            except Exception as e:
                h_c2 = ""
                err_c2 = f"{type(e).__name__}: {e}"
            elapsed = time.monotonic() - t0
            row = {
                "file": wav.name,
                "language": detect_language(wav.name),
                "reference": ref,
                "latest_long": h_ll,
                "chirp_2": h_c2,
                "wer_ll": wer(ref, h_ll) if h_ll else 1.0,
                "wer_c2": wer(ref, h_c2) if h_c2 else 1.0,
                "err_ll": err_ll,
                "err_c2": err_c2,
            }
            print(
                f"[{elapsed:5.1f}s] {wav.name} lang={row['language']} "
                f"WER ll={row['wer_ll']:.2f} c2={row['wer_c2']:.2f}",
                file=sys.stderr,
            )
            return row

    results = await asyncio.gather(*[run_one(w, r) for w, r in pairs])

    with open(args.output, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=[
                "file",
                "language",
                "reference",
                "latest_long",
                "chirp_2",
                "wer_ll",
                "wer_c2",
                "err_ll",
                "err_c2",
            ],
        )
        writer.writeheader()
        writer.writerows(results)

    # Summary
    def avg(field: str, subset: list[dict[str, object]] | None = None) -> float:
        rows = subset if subset is not None else results
        return sum(float(r[field]) for r in rows) / max(len(rows), 1)

    print("\n=== SUMMARY ===", file=sys.stderr)
    print(
        f"Overall  (n={len(results)}): latest_long={avg('wer_ll'):.3f}  chirp_2={avg('wer_c2'):.3f}",
        file=sys.stderr,
    )
    for lang in ("uk", "ru"):
        subset = [r for r in results if r["language"] == lang]
        if subset:
            print(
                f"{lang.upper():>7} (n={len(subset)}): "
                f"latest_long={avg('wer_ll', subset):.3f}  chirp_2={avg('wer_c2', subset):.3f}",
                file=sys.stderr,
            )
    ll_wins = sum(1 for r in results if r["wer_ll"] < r["wer_c2"])
    c2_wins = sum(1 for r in results if r["wer_c2"] < r["wer_ll"])
    ties = len(results) - ll_wins - c2_wins
    print(
        f"Per-clip: latest_long wins {ll_wins}, chirp_2 wins {c2_wins}, ties {ties}",
        file=sys.stderr,
    )
    print(f"CSV → {args.output}", file=sys.stderr)


if __name__ == "__main__":
    asyncio.run(main())
