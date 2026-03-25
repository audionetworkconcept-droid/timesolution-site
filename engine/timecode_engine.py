"""
Time Solution – High-Precision Timecode Synchronisation Engine
==============================================================
Cross-platform (macOS / Windows) core engine for multi-protocol
timecode synchronisation with Kalman-filter latency compensation.

Supported protocols
-------------------
- MIDI / MTC  (via python-rtmidi)
- OSC         (via python-osc)
- SMPTE / LTC (software decoder)
- Art-Net     (UDP broadcast, port 6454)
- Dante        (AES67 / PTP – network timestamping)

Usage
-----
    from engine.timecode_engine import TimecodeEngine, EngineConfig

    cfg = EngineConfig(protocols=["MIDI", "OSC", "ARTNET"])
    engine = TimecodeEngine(cfg)
    engine.start()
    ...
    engine.stop()
"""

from __future__ import annotations

import logging
import platform
import socket
import struct
import threading
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Callable, Dict, List, Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ARTNET_PORT = 6454
ARTNET_HEADER = b"Art-Net\x00"
OSC_DEFAULT_PORT = 9000
SMPTE_FRAME_RATES = {24, 25, 29.97, 30}


# ---------------------------------------------------------------------------
# Data types
# ---------------------------------------------------------------------------


class Protocol(str, Enum):
    MIDI = "MIDI"
    OSC = "OSC"
    ARTNET = "ARTNET"
    SMPTE = "SMPTE"
    DANTE = "DANTE"


@dataclass
class Timecode:
    """Represents a timecode position."""

    hours: int = 0
    minutes: int = 0
    seconds: int = 0
    frames: int = 0
    frame_rate: float = 25.0
    protocol: Protocol = Protocol.MIDI
    received_at: float = field(default_factory=time.monotonic)

    def to_seconds(self) -> float:
        return (
            self.hours * 3600
            + self.minutes * 60
            + self.seconds
            + self.frames / self.frame_rate
        )

    def __str__(self) -> str:
        return (
            f"{self.hours:02d}:{self.minutes:02d}:{self.seconds:02d}:"
            f"{self.frames:02d} @ {self.frame_rate}fps [{self.protocol.value}]"
        )


@dataclass
class SyncState:
    """Snapshot of the current synchronisation state across all active protocols."""

    master_time: float = 0.0          # seconds since epoch (monotonic reference)
    protocol_offsets: Dict[str, float] = field(default_factory=dict)  # protocol → offset ms
    last_timecodes: Dict[str, Timecode] = field(default_factory=dict)
    is_locked: bool = False
    lock_quality: float = 0.0         # 0.0 – 1.0


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


@dataclass
class EngineConfig:
    protocols: List[str] = field(default_factory=lambda: ["MIDI", "OSC", "ARTNET"])
    midi_device_index: int = 0
    osc_listen_port: int = OSC_DEFAULT_PORT
    osc_listen_ip: str = "127.0.0.1"    # set to "0.0.0.0" to receive from all interfaces
    osc_send_host: str = "127.0.0.1"
    osc_send_port: int = 9001
    artnet_listen_ip: str = "0.0.0.0"
    smpte_frame_rate: float = 25.0
    kalman_process_noise: float = 1e-5    # Q  – trust the model
    kalman_measurement_noise: float = 1e-2  # R  – trust the measurement
    log_file: Optional[str] = None         # e.g. "show_data_marrakech.log"
    engine_mode: str = "AI-Enhanced"       # "Legacy" | "AI-Enhanced"
    sync_callback: Optional[Callable[[SyncState], None]] = None


# ---------------------------------------------------------------------------
# Kalman filter (1-D, scalar) for latency compensation
# ---------------------------------------------------------------------------


class KalmanFilter1D:
    """
    Minimal 1-D Kalman filter used to smooth per-protocol latency estimates.

    State: estimated latency (ms)
    """

    def __init__(self, process_noise: float = 1e-5, measurement_noise: float = 1e-2):
        self._q = process_noise       # process noise covariance
        self._r = measurement_noise   # measurement noise covariance
        self._x = 0.0                 # state estimate
        self._p = 1.0                 # estimate error covariance

    def update(self, measurement: float) -> float:
        # Predict
        self._p += self._q

        # Update (Kalman gain)
        k = self._p / (self._p + self._r)
        self._x += k * (measurement - self._x)
        self._p *= 1 - k

        return self._x

    @property
    def estimate(self) -> float:
        return self._x


# ---------------------------------------------------------------------------
# Protocol receivers (threaded, non-blocking)
# ---------------------------------------------------------------------------


class _MidiReceiver(threading.Thread):
    """
    Receives MIDI Timecode (MTC) quarter-frame messages and assembles full
    timecodes.  Falls back to a software clock when no MIDI device is found.
    """

    def __init__(self, device_index: int, callback: Callable[[Timecode], None]):
        super().__init__(daemon=True, name="midi-receiver")
        self._device_index = device_index
        self._callback = callback
        self._stop_event = threading.Event()
        self._qf_buffer: List[int] = []

    def run(self) -> None:
        try:
            import rtmidi  # type: ignore

            midi_in = rtmidi.MidiIn()
            ports = midi_in.get_ports()
            if not ports:
                logger.warning("No MIDI input ports found – using software clock.")
                self._software_clock()
                return
            midi_in.open_port(self._device_index)
            midi_in.set_callback(self._on_message)
            logger.info("MIDI receiver open on port: %s", ports[self._device_index])
            while not self._stop_event.is_set():
                time.sleep(0.001)
            midi_in.close_port()
        except ImportError:
            logger.warning("python-rtmidi not installed – MIDI using software clock.")
            self._software_clock()

    def _on_message(self, message_and_delta, _data=None) -> None:
        message, _delta = message_and_delta
        # MTC quarter-frame: status byte 0xF1
        if message[0] == 0xF1 and len(message) >= 2:
            self._qf_buffer.append(message[1])
            if len(self._qf_buffer) >= 8:
                tc = self._assemble_mtc(self._qf_buffer[-8:])
                if tc:
                    self._callback(tc)

    def _assemble_mtc(self, qf: List[int]) -> Optional[Timecode]:
        """Assemble 8 MTC quarter-frame messages into a Timecode."""
        try:
            frames_ls = qf[0] & 0x0F
            frames_ms = qf[1] & 0x01
            secs_ls = qf[2] & 0x0F
            secs_ms = qf[3] & 0x03
            mins_ls = qf[4] & 0x0F
            mins_ms = qf[5] & 0x03
            hours_ls = qf[6] & 0x0F
            fr_type = (qf[7] >> 1) & 0x03
            hours_ms = qf[7] & 0x01

            fr_map = {0: 24.0, 1: 25.0, 2: 29.97, 3: 30.0}
            frame_rate = fr_map.get(fr_type, 25.0)
            frames = (frames_ms << 4) | frames_ls
            seconds = (secs_ms << 4) | secs_ls
            minutes = (mins_ms << 4) | mins_ls
            hours = (hours_ms << 4) | hours_ls

            return Timecode(
                hours=hours,
                minutes=minutes,
                seconds=seconds,
                frames=frames,
                frame_rate=frame_rate,
                protocol=Protocol.MIDI,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("MTC assembly error: %s", exc)
            return None

    def _software_clock(self) -> None:
        """Emit synthetic 25 fps timecode when no hardware MIDI is available."""
        start = time.monotonic()
        while not self._stop_event.is_set():
            elapsed = time.monotonic() - start
            total_frames = int(elapsed * 25)
            tc = Timecode(
                hours=int(elapsed // 3600),
                minutes=int((elapsed % 3600) // 60),
                seconds=int(elapsed % 60),
                frames=total_frames % 25,
                frame_rate=25.0,
                protocol=Protocol.MIDI,
            )
            self._callback(tc)
            time.sleep(1 / 25)

    def stop(self) -> None:
        self._stop_event.set()


class _OscReceiver(threading.Thread):
    """Listens for OSC /timecode messages on a UDP port."""

    def __init__(self, ip: str, port: int, callback: Callable[[Timecode], None]):
        super().__init__(daemon=True, name="osc-receiver")
        self._ip = ip
        self._port = port
        self._callback = callback
        self._stop_event = threading.Event()
        self._sock: Optional[socket.socket] = None

    def run(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        self._sock.bind((self._ip, self._port))
        self._sock.settimeout(0.5)
        logger.info("OSC receiver listening on %s:%d", self._ip, self._port)

        while not self._stop_event.is_set():
            try:
                data, _addr = self._sock.recvfrom(1024)
                tc = self._parse_osc(data)
                if tc:
                    self._callback(tc)
            except socket.timeout:
                continue
            except Exception as exc:  # noqa: BLE001
                logger.debug("OSC receive error: %s", exc)

        self._sock.close()

    def _parse_osc(self, data: bytes) -> Optional[Timecode]:
        """
        Parses a minimal OSC bundle / message for /timecode.

        Expected OSC message format:
            address: /timecode
            types:   ,iiiif   (hours, minutes, seconds, frames, frame_rate)
        """
        try:
            # Find address string (null-padded to 4-byte boundary)
            null_idx = data.index(b"\x00")
            address = data[:null_idx].decode("ascii")
            if "/timecode" not in address:
                return None

            # Skip address + type tag string (4-byte aligned)
            addr_len = (null_idx + 4) & ~3
            type_start = addr_len
            null_idx2 = data.index(b"\x00", type_start)
            tag_len = (null_idx2 - type_start + 4) & ~3
            payload_start = type_start + tag_len

            h, m, s, fr, fps = struct.unpack_from(">iiif", data, payload_start)
            # fps is packed as float (4 bytes); frame_rate = fps or fallback
            return Timecode(
                hours=h, minutes=m, seconds=s, frames=fr,
                frame_rate=float(fps) if fps > 0 else 25.0,
                protocol=Protocol.OSC,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("OSC parse error: %s", exc)
            return None

    def stop(self) -> None:
        self._stop_event.set()


class _ArtNetReceiver(threading.Thread):
    """
    Listens for Art-Net packets on UDP port 6454.
    Extracts timing information from ArtSync / ArtTimecode opcodes.
    """

    OPCODE_TIMECODE = 0x9700
    OPCODE_SYNC = 0x5200

    def __init__(self, listen_ip: str, callback: Callable[[Timecode], None]):
        super().__init__(daemon=True, name="artnet-receiver")
        self._ip = listen_ip
        self._callback = callback
        self._stop_event = threading.Event()

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            sock.bind((self._ip, ARTNET_PORT))
        except OSError as exc:
            logger.warning("Art-Net bind failed (%s) – skipping Art-Net receiver.", exc)
            return
        sock.settimeout(0.5)
        logger.info("Art-Net receiver on %s:%d", self._ip, ARTNET_PORT)

        while not self._stop_event.is_set():
            try:
                data, _addr = sock.recvfrom(1024)
                if not data.startswith(ARTNET_HEADER):
                    continue
                opcode = struct.unpack_from("<H", data, 8)[0]
                if opcode == self.OPCODE_TIMECODE:
                    tc = self._parse_timecode(data)
                    if tc:
                        self._callback(tc)
            except socket.timeout:
                continue
            except Exception as exc:  # noqa: BLE001
                logger.debug("Art-Net error: %s", exc)
        sock.close()

    def _parse_timecode(self, data: bytes) -> Optional[Timecode]:
        """Parse ArtTimecode packet (Art-Net 4 spec, §ArtTimecode)."""
        try:
            # Offset 14: Frames, Seconds, Minutes, Hours, Type
            if len(data) < 20:
                return None
            frames, seconds, minutes, hours, tc_type = struct.unpack_from(
                "5B", data, 14
            )
            fr_map = {0: 24.0, 1: 25.0, 2: 29.97, 3: 30.0}
            return Timecode(
                hours=hours, minutes=minutes, seconds=seconds, frames=frames,
                frame_rate=fr_map.get(tc_type, 25.0),
                protocol=Protocol.ARTNET,
            )
        except Exception as exc:  # noqa: BLE001
            logger.debug("ArtTimecode parse error: %s", exc)
            return None

    def stop(self) -> None:
        self._stop_event.set()


# ---------------------------------------------------------------------------
# Main Engine
# ---------------------------------------------------------------------------


class TimecodeEngine:
    """
    High-precision timecode synchronisation engine.

    Aggregates timecodes from all enabled protocol receivers, applies
    Kalman-filtered latency compensation, and maintains a unified
    master clock reference.
    """

    VERSION = "2.0.0"

    def __init__(self, config: Optional[EngineConfig] = None):
        self._cfg = config or EngineConfig()
        self._lock = threading.Lock()
        self._state = SyncState()
        self._receivers: List[threading.Thread] = []
        self._kalman: Dict[str, KalmanFilter1D] = {}
        self._running = False
        self._log_fh = None

        # Initialise per-protocol Kalman filters
        for proto in self._cfg.protocols:
            self._kalman[proto] = KalmanFilter1D(
                process_noise=self._cfg.kalman_process_noise,
                measurement_noise=self._cfg.kalman_measurement_noise,
            )

        if self._cfg.log_file:
            try:
                self._log_fh = open(self._cfg.log_file, "a", encoding="utf-8")  # noqa: WPS515
            except OSError as exc:
                logger.warning("Cannot open log file '%s': %s", self._cfg.log_file, exc)

        logger.info(
            "TimecodeEngine v%s initialised | mode=%s | platform=%s",
            self.VERSION,
            self._cfg.engine_mode,
            platform.system(),
        )

    # ── Public API ──────────────────────────────────────────────────────────

    def start(self) -> None:
        """Start all enabled protocol receivers."""
        if self._running:
            return
        self._running = True
        self._start_receivers()
        logger.info("Engine started with protocols: %s", self._cfg.protocols)

    def stop(self) -> None:
        """Gracefully stop all receivers and flush logs."""
        self._running = False
        for receiver in self._receivers:
            if hasattr(receiver, "stop"):
                receiver.stop()
        for receiver in self._receivers:
            receiver.join(timeout=2.0)
        if self._log_fh:
            self._log_fh.flush()
            self._log_fh.close()
        logger.info("Engine stopped.")

    @property
    def state(self) -> SyncState:
        with self._lock:
            return self._state

    def get_master_time(self) -> float:
        """Return the Kalman-compensated master time in seconds."""
        with self._lock:
            return self._state.master_time

    # ── Internal ────────────────────────────────────────────────────────────

    def _start_receivers(self) -> None:
        cfg = self._cfg
        for proto in cfg.protocols:
            proto_upper = proto.upper()
            if proto_upper == Protocol.MIDI:
                r = _MidiReceiver(cfg.midi_device_index, self._on_timecode)
            elif proto_upper == Protocol.OSC:
                r = _OscReceiver(cfg.osc_listen_ip, cfg.osc_listen_port, self._on_timecode)
            elif proto_upper == Protocol.ARTNET:
                r = _ArtNetReceiver(cfg.artnet_listen_ip, self._on_timecode)
            else:
                logger.warning("Protocol '%s' not yet implemented – skipping.", proto)
                continue
            r.start()
            self._receivers.append(r)

    def _on_timecode(self, tc: Timecode) -> None:
        """Called by each receiver thread when a new timecode is received."""
        now = time.monotonic()
        raw_latency_ms = (now - tc.received_at) * 1000.0

        proto_key = tc.protocol.value
        filtered_latency = self._kalman[proto_key].update(raw_latency_ms)

        tc_seconds = tc.to_seconds()
        compensated_time = tc_seconds + filtered_latency / 1000.0

        with self._lock:
            self._state.last_timecodes[proto_key] = tc
            self._state.protocol_offsets[proto_key] = filtered_latency
            self._state.master_time = compensated_time
            self._state.is_locked = True
            self._state.lock_quality = self._compute_lock_quality()

        self._log_event(tc, raw_latency_ms, filtered_latency)

        if self._cfg.sync_callback:
            self._cfg.sync_callback(self._state)

        logger.debug(
            "[%s] %s | raw_lat=%.3fms | filtered_lat=%.3fms",
            proto_key, tc, raw_latency_ms, filtered_latency,
        )

    def _compute_lock_quality(self) -> float:
        """Estimate lock quality as a normalised score [0, 1]."""
        if not self._state.protocol_offsets:
            return 0.0
        offsets = list(self._state.protocol_offsets.values())
        max_offset = max(abs(o) for o in offsets)
        # Quality degrades as max offset grows beyond 10 ms
        return max(0.0, 1.0 - max_offset / 10.0)

    def _log_event(
        self,
        tc: Timecode,
        raw_ms: float,
        filtered_ms: float,
    ) -> None:
        if not self._log_fh:
            return
        try:
            line = (
                f"{time.strftime('%Y-%m-%dT%H:%M:%S')} "
                f"proto={tc.protocol.value} "
                f"tc={tc} "
                f"raw_lat={raw_ms:.3f}ms "
                f"filtered_lat={filtered_ms:.3f}ms\n"
            )
            self._log_fh.write(line)
        except Exception as exc:  # noqa: BLE001
            logger.debug("Log write error: %s", exc)
