"""Labelled voltage and current presets, kept in a JSON file so every browser sees the same list."""

import json
import os
import tempfile
import threading
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from .device import MODEL_MAX_AMPS, MODEL_MAX_VOLTS

MAX_PRESETS = 50


class Preset(BaseModel):
    label: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=40)]
    volts: float = Field(ge=0, le=MODEL_MAX_VOLTS)
    amps: float = Field(ge=0, le=MODEL_MAX_AMPS)


# Written to the presets file the first time the dashboard runs, edit them from the page
DEFAULTS = [
    Preset(label="Raspberry Pi", volts=5, amps=3),
    Preset(label="F91W board", volts=3, amps=0.02),
    Preset(label="LED test", volts=3, amps=0.005),
    Preset(label="1.8 V logic", volts=1.8, amps=0.2),
    Preset(label="3.3 V logic", volts=3.3, amps=0.5),
    Preset(label="USB device", volts=5, amps=0.5),
    Preset(label="AA cell stand-in", volts=1.5, amps=0.1),
    Preset(label="Li-ion cell stand-in", volts=3.7, amps=1),
    Preset(label="9 V battery stand-in", volts=9, amps=0.1),
    Preset(label="12 V, 1 A", volts=12, amps=1),
    Preset(label="12 V, 3 A", volts=12, amps=3),
    Preset(label="USB-PD 20 V", volts=20, amps=3),
    Preset(label="24 V industrial", volts=24, amps=1),
]


class PresetStore:
    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        if path.exists():
            # A file that does not parse raises here, it is never silently overwritten
            self._presets = [Preset(**item) for item in json.loads(path.read_text())]
            labels = [p.label for p in self._presets]
            if len(labels) > MAX_PRESETS or len(set(labels)) != len(labels):
                raise ValueError(f"{path}: at most {MAX_PRESETS} presets, each with a unique label")
        else:
            self._presets = list(DEFAULTS)
            self._write()

    def _write(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a power cut cannot leave a half-written file
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump([p.model_dump() for p in self._presets], f, indent=2)
            os.replace(tmp, self.path)
        except BaseException:
            os.unlink(tmp)
            raise

    def all(self) -> list[Preset]:
        with self._lock:
            return list(self._presets)

    def save(self, preset: Preset) -> None:
        """Add a preset, or replace the one with the same label."""
        with self._lock:
            labels = [p.label for p in self._presets]
            if preset.label in labels:
                self._presets[labels.index(preset.label)] = preset
            elif len(self._presets) >= MAX_PRESETS:
                raise ValueError(f"at most {MAX_PRESETS} presets, delete one first")
            else:
                self._presets.append(preset)
            self._write()

    def move(self, label: str, offset: int) -> None:
        """Shift a preset earlier (negative) or later, stopping at either end."""
        with self._lock:
            labels = [p.label for p in self._presets]
            if label not in labels:
                raise KeyError(label)
            old = labels.index(label)
            new = max(0, min(len(labels) - 1, old + offset))
            self._presets.insert(new, self._presets.pop(old))
            self._write()

    def delete(self, label: str) -> None:
        with self._lock:
            kept = [p for p in self._presets if p.label != label]
            if len(kept) == len(self._presets):
                raise KeyError(label)
            self._presets = kept
            self._write()
