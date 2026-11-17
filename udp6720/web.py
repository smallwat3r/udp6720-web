"""Web dashboard for the UDP6720: a FastAPI app polled by a single static page."""

import argparse
import csv
import io
import ipaddress
import logging
import socket
import threading
import tomllib
from collections import deque
from contextlib import asynccontextmanager
from dataclasses import asdict
from datetime import datetime
from ipaddress import IPv4Address
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import uvicorn
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, IPvAnyAddress, PositiveInt, ValidationError

from .device import MODEL_MAX_AMPS, MODEL_MAX_VOLTS, PSU, PSUError, State, Transport
from .mock import MockTransport
from .presets import MAX_PRESETS, Preset, PresetStore
from .transport import SerialTransport, TransportError

log = logging.getLogger("udp6720")

STATIC = Path(__file__).parent / "static"
CONFIG = Path.home() / ".config" / "udp6720" / "config.toml"
PRESETS = CONFIG.with_name("presets.json")
HISTORY_SAMPLES = 3600  # 30 minutes at the default 2 Hz
# The model number: easy to remember, and clear of the ports development servers fight over
DEFAULT_HTTP_PORT = 6720
# service.sh maps this name to the local machine, giving the dashboard a memorable address
LOCAL_ALIAS = "udp6720.local"
MAX_BODY = 4096  # bytes, every request this API accepts is far smaller
SECURITY_HEADERS = {
    # frame-ancestors stops another site framing the page to hijack the output button
    "Content-Security-Policy": "default-src 'none'; connect-src 'self'; "
    "style-src 'self'; script-src 'self'; font-src 'self'; frame-ancestors 'none'",
    "X-Content-Type-Options": "nosniff",
    "Cache-Control": "no-store",
}


class Monitor:
    """Polls the PSU on a background thread and keeps the latest state and a history."""

    def __init__(self, psu: PSU, poll_hz: float, safe_start: bool = False) -> None:
        self.psu = psu
        # Applied on the first successful contact, the supply may be off when the dashboard starts
        self._safe_start_pending = safe_start
        self.period = 1.0 / poll_hz
        self.history: deque[State] = deque(maxlen=HISTORY_SAMPLES)
        self.state: State | None = None
        self.error: str | None = "connecting"
        self.idn: str | None = None
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread = threading.Thread(target=self._run, name="psu-poll", daemon=True)

    def poll(self) -> None:
        # Called by the poll thread and by request handlers, the lock keeps the history in order
        with self._lock:
            try:
                if self.idn is None:
                    self.idn = self.psu.identify()
                if self._safe_start_pending:
                    self.psu.output(False)
                    self._safe_start_pending = False
                    log.info("safe start: output forced off")
                self.state = self.psu.read_state()
                self.history.append(self.state)
                self.error = None
            except (TransportError, PSUError) as exc:
                self.error = str(exc)
            except Exception:
                # Never let a bug kill the poll thread, the page would show frozen readings as live
                log.exception("unexpected error while polling")
                self.error = "internal error, see the server log"

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll()
            self._stop.wait(self.period)

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def snapshot(self) -> dict:
        return {
            "connected": self.error is None,
            "error": self.error,
            "idn": self.idn,
            "max_volts": self.psu.max_volts,
            "max_amps": self.psu.max_amps,
            "poll_ms": round(self.period * 1000),
            "state": asdict(self.state) if self.state else None,
        }


class Setpoints(BaseModel):
    volts: float | None = Field(None, ge=0, le=MODEL_MAX_VOLTS)
    amps: float | None = Field(None, ge=0, le=MODEL_MAX_AMPS)


class Apply(BaseModel):
    label: str = Field(max_length=40)


class Move(BaseModel):
    label: str = Field(max_length=40)
    offset: int = Field(ge=-MAX_PRESETS, le=MAX_PRESETS)


class Output(BaseModel):
    on: bool


def _request_allowed(request: Request, allowed_hosts: frozenset[str]) -> bool:
    """There is no login, so reject what a hostile web page could make a browser send:
    DNS rebinding (Host is a foreign name) and cross-site writes (foreign Origin)."""
    host = request.headers.get("host", "")
    try:
        name = (urlsplit(f"//{host}").hostname or "").lower()
        origin = request.headers.get("origin")
        same_origin = origin is None or urlsplit(origin).netloc == host
    except ValueError:  # malformed Host or Origin
        return False
    # Browsers resolve every *.localhost name to this machine themselves, so a remote
    # site cannot point one here: they are as safe as "localhost" and make nice short addresses
    if name.endswith(".localhost"):
        return same_origin
    try:
        ipaddress.ip_address(name)
    except ValueError:
        if name not in allowed_hosts:
            return False
    return same_origin


def _body_too_large(request: Request) -> bool:
    length = request.headers.get("content-length", "0")
    return "transfer-encoding" in request.headers or not length.isdigit() or int(length) > MAX_BODY


def local_name() -> str:
    """The name this machine answers to on the local network through mDNS, like "bench.local"."""
    return socket.gethostname().lower().split(".")[0] + ".local"


def create_app(
    psu: PSU,
    presets: PresetStore,
    poll_hz: float = 2.0,
    safe_start: bool = False,
    off_on_exit: bool = False,
    extra_hosts: tuple[str, ...] = (),
) -> FastAPI:
    monitor = Monitor(psu, poll_hz, safe_start)
    hostname = socket.gethostname().lower()
    allowed_hosts = frozenset({"localhost", LOCAL_ALIAS, hostname, f"{hostname}.local", local_name(), *extra_hosts})

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        monitor.start()
        yield
        monitor.stop()
        if off_on_exit:
            try:
                psu.output(False)
            except (TransportError, PSUError) as exc:
                log.error("could not turn the output off on exit: %s", exc)
        psu.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def guard(request: Request, call_next):
        if not _request_allowed(request, allowed_hosts):
            return JSONResponse({"detail": "host or origin not allowed"}, status_code=403)
        if _body_too_large(request):
            return JSONResponse({"detail": "request body too large"}, status_code=413)
        response = await call_next(request)
        response.headers.update(SECURITY_HEADERS)
        return response

    def run(action) -> dict:
        try:
            action()
        except PSUError as exc:
            raise HTTPException(422, str(exc)) from exc
        except TransportError as exc:
            raise HTTPException(503, str(exc)) from exc
        monitor.poll()
        return monitor.snapshot()

    @app.get("/")
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html", media_type="text/html")

    @app.get("/api/state")
    def state() -> dict:
        return monitor.snapshot()

    @app.get("/api/history")
    def history() -> list[list[float]]:
        return [[s.ts, s.volts, s.amps, s.watts] for s in list(monitor.history)]

    @app.get("/api/history.csv")
    def history_csv() -> Response:
        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["timestamp", "volts", "amps", "watts", "set_volts", "set_amps", "output", "mode"])
        for s in list(monitor.history):
            stamp = datetime.fromtimestamp(s.ts).isoformat(timespec="milliseconds")
            writer.writerow(
                [stamp, s.volts, s.amps, s.watts, s.set_volts, s.set_amps, "ON" if s.output else "OFF", s.mode or ""]
            )
        return Response(
            out.getvalue(),
            media_type="text/csv",
            headers={"Content-Disposition": 'attachment; filename="udp6720.csv"'},
        )

    @app.post("/api/setpoints")
    def setpoints(body: Setpoints) -> dict:
        def action() -> None:
            # Current limit first, so a new voltage never meets the old, looser limit
            if body.amps is not None:
                psu.set_current(body.amps)
            if body.volts is not None:
                psu.set_voltage(body.volts)

        return run(action)

    @app.get("/api/presets")
    def list_presets() -> list[Preset]:
        return presets.all()

    @app.post("/api/presets")
    def save_preset(body: Preset) -> list[Preset]:
        try:
            presets.save(body)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
        return presets.all()

    @app.post("/api/presets/apply")
    def apply_preset(body: Apply) -> dict:
        # The values come from the store, never from the request
        try:
            preset = presets.get(body.label)
        except KeyError as exc:
            raise HTTPException(404, "no such preset") from exc

        def action() -> None:
            # A preset is a different circuit: the output goes off first, is confirmed off,
            # and stays off until it is turned on by hand. If it cannot be confirmed off,
            # psu.output raises and the setpoints are left untouched
            psu.output(False)
            psu.set_current(preset.amps)
            psu.set_voltage(preset.volts)

        return run(action)

    @app.post("/api/presets/move")
    def move_preset(body: Move) -> list[Preset]:
        try:
            presets.move(body.label, body.offset)
        except KeyError as exc:
            raise HTTPException(404, "no such preset") from exc
        return presets.all()

    @app.delete("/api/presets")
    def delete_preset(label: str) -> list[Preset]:
        try:
            presets.delete(label)
        except KeyError as exc:
            raise HTTPException(404, "no such preset") from exc
        return presets.all()

    @app.post("/api/output")
    def output(body: Output) -> dict:
        return run(lambda: psu.output(body.on))

    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    return app


class Config(BaseModel):
    """Every setting, validated the same way whether it comes from the file or a flag."""

    model_config = ConfigDict(extra="forbid")

    port: str = "/dev/ttyUSB0"
    baud: PositiveInt | Literal["auto"] = "auto"
    mock: bool = False
    host: IPvAnyAddress | Literal["localhost"] = IPv4Address("127.0.0.1")
    http_port: int = Field(DEFAULT_HTTP_PORT, ge=1, le=65535)
    poll_hz: float = Field(2.0, ge=0.1, le=10)
    max_volts: float = Field(MODEL_MAX_VOLTS, gt=0, le=MODEL_MAX_VOLTS)
    max_amps: float = Field(MODEL_MAX_AMPS, gt=0, le=MODEL_MAX_AMPS)
    safe_start: bool = False
    off_on_exit: bool = False
    allow_host: list[str] = []


def load_config(argv: list[str] | None = None, path: Path = CONFIG) -> Config:
    """Merge the config file with the command line flags, flags win. Exits with a
    readable message when either holds an unknown key or a value of the wrong type."""
    # No defaults here, so a flag only overrides the file when it was actually given
    parser = argparse.ArgumentParser(
        description=f"UDP6720 web dashboard. Defaults can be set in {path} (same names, with underscores).",
        argument_default=argparse.SUPPRESS,
    )
    parser.add_argument("--port", help="serial port (default /dev/ttyUSB0)")
    parser.add_argument("--baud", help="baud rate, or 'auto' to probe with *IDN? (default auto)")
    parser.add_argument("--mock", action="store_true", help="use the simulated PSU, no hardware")
    parser.add_argument("--host", help="bind address, 0.0.0.0 to serve the LAN, no login! (default 127.0.0.1)")
    parser.add_argument("--http-port", help=f"default {DEFAULT_HTTP_PORT}")
    parser.add_argument("--poll-hz", help="readings per second, 0.1 to 10 (default 2)")
    parser.add_argument("--max-volts", help="soft voltage limit")
    parser.add_argument("--max-amps", help="soft current limit")
    parser.add_argument("--safe-start", action="store_true", help="force the output OFF on connect")
    parser.add_argument("--off-on-exit", action="store_true", help="force the output OFF on shutdown")
    parser.add_argument("--allow-host", action="append", help="extra DNS name the page is served under")
    flags = vars(parser.parse_args(argv))
    try:
        from_file = tomllib.loads(path.read_text()) if path.exists() else {}
        return Config(**{**from_file, **flags})
    except (tomllib.TOMLDecodeError, OSError) as exc:
        parser.error(f"cannot read {path}: {exc}")
    except ValidationError as exc:
        problems = "; ".join(f"{'.'.join(map(str, e['loc']))}: {e['msg']}" for e in exc.errors())
        parser.error(f"invalid setting in {path} or on the command line: {problems}")


def main() -> None:
    config = load_config()
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    try:
        presets = PresetStore(PRESETS)
        if config.mock:
            transport: Transport = MockTransport()
        else:
            # No contact with the supply here, it may be off, the poller connects when it appears
            transport = SerialTransport(config.port, None if config.baud == "auto" else config.baud)
    except (TransportError, ValueError, OSError) as exc:
        raise SystemExit(f"error: {exc}") from exc

    app = create_app(
        PSU(transport, config.max_volts, config.max_amps),
        presets,
        config.poll_hz,
        config.safe_start,
        config.off_on_exit,
        tuple(h.lower() for h in config.allow_host),
    )
    port = "" if config.http_port == 80 else f":{config.http_port}"
    if config.host == "localhost" or config.host.is_loopback:
        log.info("dashboard on http://udp6720.localhost%s, this machine only", port)
    else:
        log.info("dashboard on http://%s%s, open to the local network, there is no login", local_name(), port)
    uvicorn.run(app, host=str(config.host), port=config.http_port, access_log=False, log_level="warning")


if __name__ == "__main__":
    main()
