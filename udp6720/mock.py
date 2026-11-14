"""In-memory fake UDP6720 with a resistive load, for development without hardware."""

from .device import MODEL_MAX_AMPS, MODEL_MAX_VOLTS
from .transport import TransportError


class MockTransport:
    def __init__(self, load_ohms: float = 10.0) -> None:
        self.load_ohms = load_ohms
        self.volts = 5.0
        self.amps = 1.0
        self.on = False

    def _measure(self) -> tuple[float, float, str]:
        if not self.on:
            return 0.0, 0.0, "CV"
        if self.volts / self.load_ohms <= self.amps:
            return self.volts, self.volts / self.load_ohms, "CV"
        return self.amps * self.load_ohms, self.amps, "CC"

    def write(self, command: str) -> None:
        name, _, arg = command.strip().upper().partition(" ")
        # Like the real device, out of range or malformed writes are silently ignored
        try:
            if name == "VOLT" and 0 <= float(arg) <= MODEL_MAX_VOLTS:
                self.volts = round(float(arg), 2)
            elif name == "CURR" and 0 <= float(arg) <= MODEL_MAX_AMPS:
                self.amps = round(float(arg), 3)
            elif name == "OUTP" and arg in ("ON", "OFF", "1", "0"):
                self.on = arg in ("ON", "1")
        except ValueError:
            pass

    def query(self, command: str) -> str:
        volts, amps, mode = self._measure()
        answers = {
            "*IDN?": "UNIT,UDP6720,MOCK,REV1.00",
            "VOLT?": f"{self.volts:.2f}",
            "CURR?": f"{self.amps:.3f}",
            "OUTP?": "ON" if self.on else "OFF",
            "OUTP:CVCC?": mode,
            "MEAS:ALL?": f"{volts:.2f},{amps:.3f},{volts * amps:.2f}",
        }
        try:
            return answers[command.strip().upper()]
        except KeyError:
            raise TransportError(f"no response to {command!r}") from None

    def close(self) -> None:
        pass
