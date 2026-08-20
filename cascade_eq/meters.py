"""Output waveform, peak/RMS, 16-band RTA, and a live tempo guess from the DSP tap."""

from __future__ import annotations

import math
import threading
import time
from collections import deque

from .dsp import BANDS

WAVE_POINTS = 256
RTA_SR = 48000
RTA_FLOOR_DB = -48.0
HIST_BINS = 36
HIST_LO_DB = -42.0
HIST_HI_DB = 3.0


def hist_bin_db(index: int, bins: int = HIST_BINS) -> float:
    span = HIST_HI_DB - HIST_LO_DB
    return HIST_LO_DB + (float(index) + 0.5) * span / max(1, bins)


def _amp_to_hist_bin(mag: float, bins: int = HIST_BINS) -> int:
    db = 20.0 * math.log10(max(float(mag), 1e-9))
    t = (db - HIST_LO_DB) / (HIST_HI_DB - HIST_LO_DB)
    return max(0, min(bins - 1, int(t * bins)))


def hist_percentile_db(hist: list[float] | tuple[float, ...], percentile: float = 0.75) -> float:
    total = sum(max(0.0, float(v)) for v in hist)
    if total < 1e-9:
        return HIST_LO_DB
    acc = 0.0
    last = 0
    for i, value in enumerate(hist):
        acc += max(0.0, float(value))
        last = i
        if acc >= percentile * total:
            return hist_bin_db(i, len(hist))
    return hist_bin_db(last, len(hist))


def _rta_q(freq: float) -> float:
    """Wider Q on the sub so 16–100 Hz actually registers."""
    hz = float(freq)
    if hz <= 40:
        return 0.65
    if hz <= 100:
        return 0.9
    if hz <= 250:
        return 1.4
    return 2.4


class _BandPass:
    """RBJ band-pass, one ISO graphic-EQ center."""

    def __init__(self, freq: float, sr: int = RTA_SR, q: float = 2.4) -> None:
        w0 = 2.0 * math.pi * max(8.0, float(freq)) / float(sr)
        alpha = math.sin(w0) / (2.0 * max(0.3, float(q)))
        b0 = alpha
        b2 = -alpha
        a0 = 1.0 + alpha
        a1 = -2.0 * math.cos(w0)
        a2 = 1.0 - alpha
        self.b0 = b0 / a0
        self.b1 = 0.0
        self.b2 = b2 / a0
        self.a1 = a1 / a0
        self.a2 = a2 / a0
        self.x1 = self.x2 = 0.0
        self.y1 = self.y2 = 0.0

    def tick(self, x: float) -> float:
        y = self.b0 * x + self.b1 * self.x1 + self.b2 * self.x2 - self.a1 * self.y1 - self.a2 * self.y2
        self.x2, self.x1 = self.x1, x
        self.y2, self.y1 = self.y1, y
        if abs(self.y1) < 1e-18:
            self.y1 = 0.0
        return y

    def reset(self) -> None:
        self.x1 = self.x2 = self.y1 = self.y2 = 0.0


def _rta_display(env: float) -> float:
    db = 20.0 * math.log10(max(float(env), 1e-9))
    return max(0.0, min(1.0, (db - RTA_FLOOR_DB) / -RTA_FLOOR_DB))


class OutputMeters:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.wave = [0.0] * WAVE_POINTS
        self._cursor = 0
        self.peak_l = 0.0
        self.peak_r = 0.0
        self.rms_l = 0.0
        self.rms_r = 0.0
        self.rta = [0.0] * len(BANDS)
        self._rta_env = [0.0] * len(BANDS)
        self._rta = [_BandPass(freq, q=_rta_q(freq)) for _prop, _label, freq in BANDS]
        self.hist = [0.0] * HIST_BINS
        self.form_db = HIST_LO_DB
        self.appsink = None
        self.bpm = 120.0
        self._onset_rms = 0.0
        self._onset: deque[tuple[float, float]] = deque()
        self._bpm_due = 0.0

    def reset(self) -> None:
        with self._lock:
            self.wave = [0.0] * WAVE_POINTS
            self._cursor = 0
            self.peak_l = self.peak_r = 0.0
            self.rms_l = self.rms_r = 0.0
            self.rta = [0.0] * len(BANDS)
            self._rta_env = [0.0] * len(BANDS)
            self.hist = [0.0] * HIST_BINS
            self.form_db = HIST_LO_DB
            self.bpm = 120.0
            self._onset_rms = 0.0
            self._onset.clear()
            self._bpm_due = 0.0
            for filt in self._rta:
                filt.reset()

    def ingest(self, samples: list[float] | tuple[float, ...], rta: bool = True) -> None:
        n = len(samples)
        if n < 2:
            return
        cols = min(48, max(8, n // 64))
        step = max(2, (n // 2 * 2) // cols)
        trace: list[float] = []
        acc_l = acc_r = 0.0
        pk_l = pk_r = 0.0
        pairs = n // 2
        for i in range(0, pairs * 2, 2):
            left = abs(float(samples[i]))
            right = abs(float(samples[i + 1]))
            pk_l = max(pk_l, left)
            pk_r = max(pk_r, right)
            acc_l += left * left
            acc_r += right * right
        for col in range(cols):
            start = col * step
            end = min(n - 1, start + step)
            best = 0.0
            for i in range(start, end, 2):
                mix = 0.5 * (float(samples[i]) + float(samples[i + 1]))
                if abs(mix) >= abs(best):
                    best = mix
            trace.append(max(-1.0, min(1.0, best)))
        rms_l = math.sqrt(acc_l / max(1, pairs))
        rms_r = math.sqrt(acc_r / max(1, pairs))
        acc_b = [0.0] * len(self._rta)
        counted = 0
        if rta:
            for i in range(0, pairs * 2, 2):
                mono = 0.5 * (float(samples[i]) + float(samples[i + 1]))
                for b, filt in enumerate(self._rta):
                    y = filt.tick(mono)
                    acc_b[b] += y * y
                counted += 1
        with self._lock:
            for value in trace:
                self.wave[self._cursor] = value
                self._cursor = (self._cursor + 1) % WAVE_POINTS
            self.peak_l = min(1.0, 0.55 * self.peak_l + 0.45 * pk_l)
            self.peak_r = min(1.0, 0.55 * self.peak_r + 0.45 * pk_r)
            self.rms_l = min(1.0, 0.72 * self.rms_l + 0.28 * rms_l)
            self.rms_r = min(1.0, 0.72 * self.rms_r + 0.28 * rms_r)
            mono_rms = 0.5 * (self.rms_l + self.rms_r)
            flux = max(0.0, mono_rms - self._onset_rms)
            self._onset_rms = mono_rms
            now = time.monotonic()
            self._onset.append((now, flux))
            while self._onset and now - self._onset[0][0] > 8.0:
                self._onset.popleft()
            if now >= self._bpm_due and len(self._onset) >= 32:
                self.bpm = self._estimate_bpm()
                self._bpm_due = now + 1.2
            if counted:
                for i, energy in enumerate(acc_b):
                    rms = math.sqrt(energy / counted)
                    self._rta_env[i] = 0.62 * self._rta_env[i] + 0.38 * rms
                    self.rta[i] = _rta_display(self._rta_env[i])
            for i in range(HIST_BINS):
                self.hist[i] *= 0.86
            hop_h = max(4, pairs // 48)
            for i in range(0, pairs * 2, hop_h * 2):
                mag = 0.5 * (abs(float(samples[i])) + abs(float(samples[i + 1])))
                self.hist[_amp_to_hist_bin(mag)] += 1.0
            self.form_db = hist_percentile_db(self.hist, 0.78)

    def decay(self) -> None:
        with self._lock:
            self.peak_l *= 0.86
            self.peak_r *= 0.86
            self.rms_l *= 0.9
            self.rms_r *= 0.9
            if self.peak_l < 0.002:
                self.peak_l = 0.0
            if self.peak_r < 0.002:
                self.peak_r = 0.0
            for i, env in enumerate(self._rta_env):
                env *= 0.82
                if env < 1e-6:
                    env = 0.0
                self._rta_env[i] = env
                self.rta[i] = _rta_display(env)
            for i, value in enumerate(self.hist):
                value *= 0.82
                if value < 0.02:
                    value = 0.0
                self.hist[i] = value
            self.form_db = hist_percentile_db(self.hist, 0.78) if max(self.hist) > 0.02 else HIST_LO_DB

    def _estimate_bpm(self) -> float:
        pts = list(self._onset)
        if len(pts) < 24:
            return self.bpm
        t0, t1 = pts[0][0], pts[-1][0]
        span = t1 - t0
        if span < 2.4:
            return self.bpm
        sr_env = 40.0
        n = min(400, int(span * sr_env) + 1)
        if n < 40:
            return self.bpm
        env = [0.0] * n
        scale = (n - 1) / span
        for t, flux in pts:
            i = int((t - t0) * scale)
            if 0 <= i < n:
                env[i] = max(env[i], float(flux))
        mean = sum(env) / n
        env = [v - mean for v in env]
        min_lag = max(2, int(sr_env * 60.0 / 180.0))
        max_lag = min(n // 2 - 1, int(sr_env * 60.0 / 70.0))
        if max_lag <= min_lag + 2:
            return self.bpm
        best_lag, best = min_lag, -1e18
        for lag in range(min_lag, max_lag + 1):
            acc = 0.0
            limit = n - lag
            for i in range(limit):
                acc += env[i] * env[i + lag]
            if acc > best:
                best, best_lag = acc, lag
        bpm = 60.0 * sr_env / max(best_lag, 1)
        while bpm < 70.0:
            bpm *= 2.0
        while bpm > 180.0:
            bpm /= 2.0
        bpm = max(70.0, min(180.0, bpm))
        return 0.65 * self.bpm + 0.35 * bpm

    def snapshot(self) -> dict:
        with self._lock:
            ordered = self.wave[self._cursor :] + self.wave[: self._cursor]
            peak_h = max(self.hist) or 1.0
            return {
                "wave": [round(v, 4) for v in ordered],
                "peak_l": round(self.peak_l, 5),
                "peak_r": round(self.peak_r, 5),
                "rms_l": round(self.rms_l, 5),
                "rms_r": round(self.rms_r, 5),
                "rta": [round(v, 4) for v in self.rta],
                "hist": [round(min(1.0, v / peak_h), 4) for v in self.hist],
                "form_db": round(float(self.form_db), 2),
                "bpm": round(float(self.bpm), 1),
            }
