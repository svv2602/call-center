"""Tests for echo cancellation module."""

from __future__ import annotations

import array
import math
import sys
from unittest.mock import MagicMock, patch

import pytest

from src.core.echo_canceller import (
    EchoCanceller,
    EchoCancellerConfig,
    FarEndBuffer,
    _FRAME_BYTES,
    _SILENCE_FRAME,
    _compute_rms,
)


# ---------------------------------------------------------------------------
# FarEndBuffer
# ---------------------------------------------------------------------------


class TestFarEndBuffer:
    """Tests for the far-end ring buffer."""

    def test_pop_empty_returns_silence(self):
        buf = FarEndBuffer()
        assert buf.pop_frame() == _SILENCE_FRAME

    def test_push_exact_frame(self):
        buf = FarEndBuffer()
        frame = b"\x01" * _FRAME_BYTES
        buf.push(frame)
        assert len(buf) == 1
        assert buf.pop_frame() == frame
        assert len(buf) == 0

    def test_push_multiple_frames(self):
        buf = FarEndBuffer()
        data = b"\x02" * (_FRAME_BYTES * 3)
        buf.push(data)
        assert len(buf) == 3
        for _ in range(3):
            frame = buf.pop_frame()
            assert len(frame) == _FRAME_BYTES

    def test_push_remainder_padded(self):
        buf = FarEndBuffer()
        data = b"\x03" * (_FRAME_BYTES + 10)
        buf.push(data)
        assert len(buf) == 2  # 1 full frame + 1 padded
        _ = buf.pop_frame()
        padded = buf.pop_frame()
        assert len(padded) == _FRAME_BYTES
        # First 10 bytes should be \x03, rest \x00
        assert padded[:10] == b"\x03" * 10
        assert padded[10:] == b"\x00" * (_FRAME_BYTES - 10)

    def test_push_less_than_frame(self):
        buf = FarEndBuffer()
        data = b"\x04" * 100
        buf.push(data)
        assert len(buf) == 1
        frame = buf.pop_frame()
        assert frame[:100] == b"\x04" * 100
        assert frame[100:] == b"\x00" * (_FRAME_BYTES - 100)

    def test_clear(self):
        buf = FarEndBuffer()
        buf.push(b"\x05" * _FRAME_BYTES * 5)
        assert len(buf) == 5
        buf.clear()
        assert len(buf) == 0
        assert buf.pop_frame() == _SILENCE_FRAME

    def test_maxlen_eviction(self):
        buf = FarEndBuffer(maxlen=3)
        for i in range(5):
            buf.push(bytes([i]) * _FRAME_BYTES)
        assert len(buf) == 3
        # Oldest frames (0, 1) should have been evicted
        first = buf.pop_frame()
        assert first[0] == 2

    def test_push_empty_data(self):
        buf = FarEndBuffer()
        buf.push(b"")
        assert len(buf) == 0


# ---------------------------------------------------------------------------
# _compute_rms
# ---------------------------------------------------------------------------


class TestComputeRms:
    def test_silence_rms_zero(self):
        assert _compute_rms(_SILENCE_FRAME) == 0.0

    def test_known_rms(self):
        # Constant signal of 100 → RMS should be 100
        samples = array.array("h", [100] * (_FRAME_BYTES // 2))
        rms = _compute_rms(samples.tobytes())
        assert abs(rms - 100.0) < 0.01

    def test_varying_signal(self):
        samples = array.array("h", [1000, -1000] * (_FRAME_BYTES // 4))
        rms = _compute_rms(samples.tobytes())
        assert abs(rms - 1000.0) < 0.01

    def test_empty_frame(self):
        assert _compute_rms(b"") == 0.0


# ---------------------------------------------------------------------------
# EchoCanceller
# ---------------------------------------------------------------------------


class TestEchoCanceller:
    """Tests for the EchoCanceller class."""

    def _make_config(self, **kwargs) -> EchoCancellerConfig:
        return EchoCancellerConfig(**kwargs)

    def _make_loud_frame(self, amplitude: int = 500) -> bytes:
        """Create a frame with amplitude above default threshold (50 RMS)."""
        samples = array.array("h", [amplitude] * (_FRAME_BYTES // 2))
        return samples.tobytes()

    def _make_quiet_frame(self, amplitude: int = 5) -> bytes:
        """Create a frame with amplitude below default threshold (50 RMS)."""
        samples = array.array("h", [amplitude] * (_FRAME_BYTES // 2))
        return samples.tobytes()

    def test_disabled_passthrough(self):
        config = self._make_config(enabled=False)
        ec = EchoCanceller(config, FarEndBuffer())
        frame = self._make_loud_frame()
        assert ec.process(frame, speaking=True) is frame

    def test_not_speaking_passthrough(self):
        config = self._make_config(enabled=True, energy_gate_only=True)
        ec = EchoCanceller(config, FarEndBuffer())
        frame = self._make_loud_frame()
        assert ec.process(frame, speaking=False) is frame

    def test_energy_gate_suppresses_quiet(self):
        config = self._make_config(enabled=True, energy_gate_only=True, energy_threshold_rms=50.0)
        ec = EchoCanceller(config, FarEndBuffer())
        quiet = self._make_quiet_frame(amplitude=5)
        result = ec.process(quiet, speaking=True)
        assert result == _SILENCE_FRAME

    def test_energy_gate_passes_loud(self):
        config = self._make_config(enabled=True, energy_gate_only=True, energy_threshold_rms=50.0)
        ec = EchoCanceller(config, FarEndBuffer())
        loud = self._make_loud_frame(amplitude=500)
        result = ec.process(loud, speaking=True)
        assert result == loud  # loud enough to pass

    def test_energy_gate_disabled(self):
        config = self._make_config(
            enabled=True, energy_gate_only=True, energy_gate_enabled=False
        )
        ec = EchoCanceller(config, FarEndBuffer())
        quiet = self._make_quiet_frame(amplitude=5)
        result = ec.process(quiet, speaking=True)
        # Without gate, quiet frame passes through
        assert result == quiet

    def test_wrong_frame_size_passthrough(self):
        config = self._make_config(enabled=True, energy_gate_only=True)
        ec = EchoCanceller(config, FarEndBuffer())
        odd_frame = b"\x01" * 100  # wrong size
        assert ec.process(odd_frame, speaking=True) is odd_frame

    def test_aec_available_property_without_pyaec(self):
        config = self._make_config(enabled=True, energy_gate_only=True)
        ec = EchoCanceller(config, FarEndBuffer())
        assert ec.aec_available is False

    def test_record_far_end(self):
        buf = FarEndBuffer()
        config = self._make_config(enabled=True, energy_gate_only=True)
        ec = EchoCanceller(config, buf)
        audio = b"\x06" * _FRAME_BYTES
        ec.record_far_end(audio)
        assert len(buf) == 1

    def test_record_far_end_disabled(self):
        buf = FarEndBuffer()
        config = self._make_config(enabled=False)
        ec = EchoCanceller(config, buf)
        ec.record_far_end(b"\x07" * _FRAME_BYTES)
        assert len(buf) == 0

    def test_clear_far_end(self):
        buf = FarEndBuffer()
        config = self._make_config(enabled=True, energy_gate_only=True)
        ec = EchoCanceller(config, buf)
        ec.record_far_end(b"\x08" * _FRAME_BYTES * 3)
        assert len(buf) == 3
        ec.clear_far_end()
        assert len(buf) == 0

    def test_aec_mock_integration(self):
        """Test AEC path with a mocked pyaec module."""
        mock_aec_instance = MagicMock()
        cleaned_frame = self._make_quiet_frame(amplitude=2)
        mock_aec_instance.process.return_value = cleaned_frame

        mock_pyaec = MagicMock()
        mock_pyaec.EchoCanceller.create.return_value = mock_aec_instance

        with patch.dict(sys.modules, {"pyaec": mock_pyaec}):
            config = self._make_config(enabled=True, energy_gate_only=False)
            buf = FarEndBuffer()
            ec = EchoCanceller(config, buf)
            assert ec.aec_available is True

            # Record far-end reference
            far = b"\x09" * _FRAME_BYTES
            ec.record_far_end(far)

            # Process near-end — AEC returns cleaned, then gate checks RMS
            near = self._make_loud_frame()
            result = ec.process(near, speaking=True)

            mock_aec_instance.process.assert_called_once()
            # cleaned_frame has low RMS → gate should suppress
            assert result == _SILENCE_FRAME

    def test_aec_mock_loud_output_passes_gate(self):
        """AEC output with sufficient energy passes through energy gate."""
        loud_cleaned = self._make_loud_frame(amplitude=200)
        mock_aec_instance = MagicMock()
        mock_aec_instance.process.return_value = loud_cleaned

        mock_pyaec = MagicMock()
        mock_pyaec.EchoCanceller.create.return_value = mock_aec_instance

        with patch.dict(sys.modules, {"pyaec": mock_pyaec}):
            config = self._make_config(enabled=True, energy_gate_only=False)
            buf = FarEndBuffer()
            ec = EchoCanceller(config, buf)

            ec.record_far_end(b"\x00" * _FRAME_BYTES)
            result = ec.process(self._make_loud_frame(), speaking=True)
            assert result == loud_cleaned

    def test_pyaec_import_error_fallback(self):
        """When pyaec import fails, AEC falls back to energy gate."""
        # Temporarily remove pyaec from modules
        saved = sys.modules.get("pyaec")
        sys.modules["pyaec"] = None  # type: ignore[assignment]
        try:
            config = self._make_config(enabled=True, energy_gate_only=False)
            ec = EchoCanceller(config, FarEndBuffer())
            assert ec.aec_available is False
            # Should still process via energy gate
            loud = self._make_loud_frame()
            result = ec.process(loud, speaking=True)
            assert result == loud
        finally:
            if saved is not None:
                sys.modules["pyaec"] = saved
            else:
                sys.modules.pop("pyaec", None)
