"""
Time Solution – Real-Time Latency Monitor
=========================================
Collects per-protocol latency samples, maintains rolling statistics,
and emits structured events for the dashboard and log system.

Designed to run alongside :class:`engine.timecode_engine.TimecodeEngine`.

Usage
-----
    from engine.latency_monitor import LatencyMonitor

    monitor = LatencyMonitor(window_seconds=30)
    monitor.start()

    # Feed measurements from the engine's sync_callback:
    monitor.record("MIDI", latency_ms=0.4)
    monitor.record("OSC",  latency_ms=1.1)

    stats = monitor.get_stats("MIDI")
    print(stats)  # LatencyStats(mean=0.4, p95=…, spikes=0, …)

    monitor.stop()
"""

from __future__ import annotations

import collections
import logging
import threading
import time
from dataclasses import dataclass, field
from typing import Deque, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

SPIKE_THRESHOLD_MS = 5.0     # Latency above this is flagged as a spike
CRITICAL_THRESHOLD_MS = 20.0  # Latency above this triggers a critical alert
DEFAULT_WINDOW_SECONDS = 30


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


@dataclass
class LatencySample:
    protocol: str
    value_ms: float
    timestamp: float = field(default_factory=time.monotonic)


@dataclass
class LatencyStats:
    """Rolling statistics for one protocol over the monitoring window."""

    protocol: str
    sample_count: int = 0
    mean_ms: float = 0.0
    min_ms: float = 0.0
    max_ms: float = 0.0
    p95_ms: float = 0.0          # 95th-percentile
    spike_count: int = 0         # samples above SPIKE_THRESHOLD_MS
    critical_count: int = 0      # samples above CRITICAL_THRESHOLD_MS
    last_updated: float = field(default_factory=time.monotonic)

    def __str__(self) -> str:
        return (
            f"[{self.protocol}] "
            f"mean={self.mean_ms:.2f}ms  "
            f"p95={self.p95_ms:.2f}ms  "
            f"min={self.min_ms:.2f}ms  "
            f"max={self.max_ms:.2f}ms  "
            f"spikes={self.spike_count}"
        )


@dataclass
class MonitorAlert:
    protocol: str
    severity: str          # "WARNING" | "CRITICAL"
    value_ms: float
    message: str
    timestamp: float = field(default_factory=time.monotonic)

    def __str__(self) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(self.timestamp))
        return f"[{ts}] {self.severity} {self.protocol}: {self.message} ({self.value_ms:.2f}ms)"


# ---------------------------------------------------------------------------
# Latency Monitor
# ---------------------------------------------------------------------------


class LatencyMonitor:
    """
    Real-time rolling latency monitor for multiple protocols.

    Maintains a fixed-duration sliding window of samples per protocol,
    computes rolling statistics, and raises alerts on threshold breaches.
    """

    def __init__(
        self,
        window_seconds: float = DEFAULT_WINDOW_SECONDS,
        alert_callback: Optional[Callable] = None,
        sample_rate_hz: float = 50.0,
    ):
        self._window = window_seconds
        self._alert_cb = alert_callback
        self._sample_rate = sample_rate_hz

        self._lock = threading.Lock()
        self._samples: Dict[str, Deque[LatencySample]] = {}
        self._alerts: List[MonitorAlert] = []
        self._stop_event = threading.Event()
        self._stats_thread: Optional[threading.Thread] = None

    # ── Lifecycle ───────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start the background statistics recomputation thread."""
        self._stats_thread = threading.Thread(
            target=self._stats_loop,
            daemon=True,
            name="latency-monitor-stats",
        )
        self._stats_thread.start()
        logger.info("LatencyMonitor started (window=%.0fs)", self._window)

    def stop(self) -> None:
        """Stop the monitor."""
        self._stop_event.set()
        if self._stats_thread:
            self._stats_thread.join(timeout=2.0)
        logger.info("LatencyMonitor stopped.")

    # ── Public API ───────────────────────────────────────────────────────────

    def record(self, protocol: str, latency_ms: float) -> None:
        """
        Record a latency measurement for *protocol*.

        Thread-safe; safe to call from receiver callbacks.
        """
        sample = LatencySample(protocol=protocol, value_ms=latency_ms)
        with self._lock:
            if protocol not in self._samples:
                self._samples[protocol] = collections.deque()
            self._samples[protocol].append(sample)
        self._check_threshold(sample)

    def get_stats(self, protocol: str) -> Optional[LatencyStats]:
        """Return the latest rolling statistics for *protocol*, or None."""
        with self._lock:
            deque = self._samples.get(protocol)
            if not deque:
                return None
            return self._compute_stats(protocol, list(deque))

    def get_all_stats(self) -> Dict[str, LatencyStats]:
        """Return a dict of protocol → LatencyStats for all tracked protocols."""
        with self._lock:
            snapshot = {p: list(d) for p, d in self._samples.items()}
        return {
            proto: self._compute_stats(proto, samples)
            for proto, samples in snapshot.items()
        }

    def get_waveform(self, protocol: str, points: int = 100) -> List[float]:
        """
        Return the last *points* latency values for *protocol* as a list of
        floats (ms), suitable for rendering a waveform on the dashboard.
        """
        with self._lock:
            deque = self._samples.get(protocol)
            if not deque:
                return []
            values = [s.value_ms for s in deque]
        return values[-points:]

    def get_recent_alerts(self, n: int = 20) -> List[MonitorAlert]:
        """Return the *n* most recent alerts."""
        with self._lock:
            return list(self._alerts[-n:])

    def clear_alerts(self) -> None:
        with self._lock:
            self._alerts.clear()

    # ── Internal ─────────────────────────────────────────────────────────────

    def _stats_loop(self) -> None:
        """Periodically prune old samples outside the rolling window."""
        interval = 1.0 / self._sample_rate
        while not self._stop_event.is_set():
            self._prune_old_samples()
            time.sleep(interval)

    def _prune_old_samples(self) -> None:
        cutoff = time.monotonic() - self._window
        with self._lock:
            for deque in self._samples.values():
                while deque and deque[0].timestamp < cutoff:
                    deque.popleft()

    def _compute_stats(self, protocol: str, samples: List[LatencySample]) -> LatencyStats:
        if not samples:
            return LatencyStats(protocol=protocol)

        values = sorted(s.value_ms for s in samples)
        n = len(values)
        mean = sum(values) / n
        p95_idx = max(0, int(n * 0.95) - 1)

        return LatencyStats(
            protocol=protocol,
            sample_count=n,
            mean_ms=mean,
            min_ms=values[0],
            max_ms=values[-1],
            p95_ms=values[p95_idx],
            spike_count=sum(1 for v in values if v >= SPIKE_THRESHOLD_MS),
            critical_count=sum(1 for v in values if v >= CRITICAL_THRESHOLD_MS),
            last_updated=time.monotonic(),
        )

    def _check_threshold(self, sample: LatencySample) -> None:
        alert: Optional[MonitorAlert] = None

        if sample.value_ms >= CRITICAL_THRESHOLD_MS:
            alert = MonitorAlert(
                protocol=sample.protocol,
                severity="CRITICAL",
                value_ms=sample.value_ms,
                message=(
                    f"Latency exceeded critical threshold "
                    f"({CRITICAL_THRESHOLD_MS:.0f}ms)"
                ),
            )
        elif sample.value_ms >= SPIKE_THRESHOLD_MS:
            alert = MonitorAlert(
                protocol=sample.protocol,
                severity="WARNING",
                value_ms=sample.value_ms,
                message=f"Latency spike detected (>{SPIKE_THRESHOLD_MS:.0f}ms)",
            )

        if alert:
            logger.warning(str(alert))
            with self._lock:
                self._alerts.append(alert)
            if self._alert_cb:
                try:
                    self._alert_cb(alert)
                except Exception as exc:  # noqa: BLE001
                    logger.debug("Alert callback error: %s", exc)
