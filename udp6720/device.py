"""High-level UDP6720 API with limit validation and setpoint readback."""

import math
import re
import threading
import time
from dataclasses import dataclass
from typing import Protocol

from .transport import TransportError

MODEL_MAX_VOLTS = 60.0
MODEL_MAX_AMPS = 5.0
# Setpoint readback must match within this, covers the device rounding to 10 mV / 1 mA
READBACK_TOLERANCE = 0.011

_NUMBER = re.compile(r"[-+]?\d+(?:\.\d+)?(?:[eE][-+]?\d+)?")


class PSUError(Exception):
    """The device refused a value, or answered something unexpected."""


class Transport(Protocol):
    def write(self, command: str) -> None: ...
    def query(self, command: str) -> str: ...
    def close(self) -> None: ...


@dataclass(frozen=True)
class State:
    ts: float
    volts: float
    amps: float
    watts: float
    set_volts: float
    set_amps: float
    output: bool
    mode: str | None  # "CV", "CC", or None when the device does not report it


def _floats(response: str, count: int) -> list[float]:
    # Tolerant of units and spacing ("12.00V, 1.000A"), the exact format is unverified
    values = [float(m) for m in _NUMBER.findall(response)]
    if len(values) < count:
        raise PSUError(f"expected {count} number(s), got {response!r}")
    return values[:count]


def _on_off(response: str) -> bool:
    token = response.strip().upper()
    if token in ("ON", "1"):
        return True
    if token in ("OFF", "0"):
        return False
    raise PSUError(f"expected ON or OFF, got {response!r}")


class PSU:
    def __init__(
        self,
        transport: Transport,
        max_volts: float = MODEL_MAX_VOLTS,
        max_amps: float = MODEL_MAX_AMPS,
    ) -> None:
        self._t = transport
        # Soft limits can only tighten the model limits
        self.max_volts = min(max_volts, MODEL_MAX_VOLTS)
        self.max_amps = min(max_amps, MODEL_MAX_AMPS)
        # OUTP:CVCC? is in the newer manual only, stop asking if the device ignores it
        self._has_cvcc = True
        # Held for a whole operation, so one caller's write and readback are never
        # interleaved with another's
        self._lock = threading.Lock()

    def identify(self) -> str:
        return self._t.query("*IDN?")

    def _set(self, command: str, value: float, limit: float, unit: str) -> float:
        if not math.isfinite(value) or not 0 <= value <= limit:
            raise PSUError(f"{value} {unit} is outside 0 to {limit:g} {unit}")
        with self._lock:
            self._t.write(f"{command} {value:.3f}")
            (actual,) = _floats(self._t.query(f"{command}?"), 1)
        if abs(actual - value) > READBACK_TOLERANCE:
            raise PSUError(f"device reports {actual:g} {unit} after setting {value:g} {unit}")
        return actual

    def set_voltage(self, volts: float) -> float:
        return self._set("VOLT", volts, self.max_volts, "V")

    def set_current(self, amps: float) -> float:
        return self._set("CURR", amps, self.max_amps, "A")

    def output(self, on: bool) -> bool:
        with self._lock:
            self._t.write("OUTP ON" if on else "OUTP OFF")
            actual = _on_off(self._t.query("OUTP?"))
        if actual != on:
            raise PSUError(f"output is still {'ON' if actual else 'OFF'}")
        return actual

    def read_state(self) -> State:
        with self._lock:
            return self._read_state()

    def _read_state(self) -> State:
        volts, amps, watts = _floats(self._t.query("MEAS:ALL?"), 3)
        (set_volts,) = _floats(self._t.query("VOLT?"), 1)
        (set_amps,) = _floats(self._t.query("CURR?"), 1)
        output = _on_off(self._t.query("OUTP?"))
        mode = None
        if self._has_cvcc:
            try:
                mode = self._t.query("OUTP:CVCC?").strip().upper()
            except TransportError:
                self._has_cvcc = False
        return State(time.time(), volts, amps, watts, set_volts, set_amps, output, mode)

    def close(self) -> None:
        self._t.close()
