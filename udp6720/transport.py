"""Serial SCPI transport for the UDP6720 (CR+LF framed ASCII, 8N1, no flow control)."""

import threading
import time

import serial

BAUD_RATES = (9600, 19200, 38400, 57600, 115200)
# Longest response accepted, a device streaming garbage cannot grow a line without bound
MAX_LINE = 256


class TransportError(Exception):
    """The device did not answer, or the serial port failed."""


class SerialTransport:
    """One command in flight at a time, the port is reopened after a failure
    so an unplugged USB adapter recovers without restarting the server."""

    def __init__(
        self,
        port: str,
        baud: int = 9600,
        timeout: float = 0.5,
        retries: int = 1,
        write_delay: float = 0.05,
        address: int | None = None,
    ) -> None:
        self.port = port
        self.baud = baud
        self.timeout = timeout
        self.retries = retries
        # The device sends no acknowledgement for a write, give it time to act
        # before the next command.
        self.write_delay = write_delay
        # RS485 only, prefixes every command with "ADDR n::"
        self.address = address
        self._lock = threading.Lock()
        self._serial: serial.Serial | None = None

    def _frame(self, command: str) -> bytes:
        if "\r" in command or "\n" in command:
            raise ValueError("command must be a single line")
        prefix = f"ADDR {self.address}::" if self.address is not None else ""
        return f"{prefix}{command}\r\n".encode("ascii")

    def _open(self) -> serial.Serial:
        if self._serial is None:
            # Exclusive, a second program on the same port would corrupt the framing
            self._serial = serial.Serial(
                self.port, self.baud, timeout=self.timeout, write_timeout=self.timeout, exclusive=True
            )
        return self._serial

    def _drop(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except (serial.SerialException, OSError):
                pass
            self._serial = None

    def write(self, command: str) -> None:
        frame = self._frame(command)
        with self._lock:
            try:
                self._open().write(frame)
            except (serial.SerialException, OSError) as exc:
                self._drop()
                raise TransportError(f"{self.port}: {exc}") from exc
            time.sleep(self.write_delay)

    def query(self, command: str) -> str:
        frame = self._frame(command)
        with self._lock:
            for _ in range(self.retries + 1):
                try:
                    port = self._open()
                    port.reset_input_buffer()
                    port.write(frame)
                    line = port.read_until(b"\n", MAX_LINE)
                except (serial.SerialException, OSError) as exc:
                    self._drop()
                    raise TransportError(f"{self.port}: {exc}") from exc
                if line.endswith(b"\n"):
                    return line.decode("ascii", errors="replace").strip()
            raise TransportError(f"no response to {command!r} on {self.port} at {self.baud} baud")

    def close(self) -> None:
        with self._lock:
            self._drop()


def probe(port: str, bauds: tuple[int, ...] = BAUD_RATES) -> tuple[int, str]:
    """Try *IDN? at each baud rate, return (baud, identity) of the first that answers."""
    for baud in bauds:
        transport = SerialTransport(port, baud, retries=0)
        try:
            idn = transport.query("*IDN?")
        except TransportError:
            continue
        finally:
            transport.close()
        # Wrong baud rates can still produce a framed line of garbage
        if idn.isprintable() and "," in idn:
            return baud, idn
    raise TransportError(f"no device answered *IDN? on {port} at any of {bauds}")
