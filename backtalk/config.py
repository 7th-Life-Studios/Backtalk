from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

APP_NAME = "Backtalk"
from . import __version__ as APP_VERSION
ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"

FROZEN = bool(getattr(sys, "frozen", False))


def _app_dir() -> Path:
    if FROZEN:
        return Path(sys.executable).resolve().parent
    return ROOT


def _user_data_dir() -> Path:
    if FROZEN:
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        return Path(base) / APP_NAME
    return ROOT / "data"


def _audio_dir() -> Path:
    if FROZEN:
        return _user_data_dir() / "audio"
    return _app_dir() / "audio"


DATA = Path(os.environ.get("BACKTALK_DATA", str(_user_data_dir())))
AUDIO_OUT = Path(os.environ.get("BACKTALK_AUDIO", str(_audio_dir())))

def _find_kokoro() -> tuple[Path, str]:
    override = os.environ.get("KOKORO_ROOT")
    if override:
        return Path(override), "env-override"
    app = _app_dir()
    for candidate, source in (
        (app / "engine", "beside-app"),
        (app.parent / "engine", "beside-install"),
        (app / "kokoro", "app-kokoro"),
    ):
        if candidate.exists():
            return candidate, source
    bundled = ROOT / "kokoro"
    if bundled.exists():
        return bundled, "in-tree"
    for ancestor in ROOT.parents:
        candidate = ancestor / "Dev" / "Kokoro-FastAPI"
        if candidate.exists():
            return candidate, "developer-checkout-fallback"
        candidate = ancestor / "Kokoro-FastAPI"
        if candidate.exists():
            return candidate, "developer-checkout-fallback"
    return bundled, "missing"

KOKORO_ROOT, KOKORO_ROOT_SOURCE = _find_kokoro()


def _find_engine_python() -> tuple[Path | None, str]:
    override = os.environ.get("KOKORO_PYTHON")
    if override:
        return Path(override), "env-override"
    for candidate, source in (
        (KOKORO_ROOT / "python" / "python.exe", "bundled-runtime"),
        (KOKORO_ROOT / ".venv" / "Scripts" / "python.exe", "developer-venv"),
    ):
        if candidate.exists():
            return candidate, source
    return None, "none"


KOKORO_PYTHON, KOKORO_PYTHON_SOURCE = _find_engine_python()
KOKORO_HOST = KOKORO_ROOT / "run_engine.py"
KOKORO_URL = os.environ.get("KOKORO_URL", "http://127.0.0.1:8880")


def _kokoro_port() -> int:
    from urllib.parse import urlparse
    try:
        return urlparse(KOKORO_URL).port or 8880
    except ValueError:
        return 8880


KOKORO_PORT = _kokoro_port()

def _find_packs() -> Path | None:
    app = _app_dir()
    for candidate in (app / "packs", app.parent / "packs"):
        if candidate.exists():
            return candidate
    return None


ARGOS_PACKAGES = _find_packs()
if FROZEN and ARGOS_PACKAGES is not None:
    os.environ.setdefault("ARGOS_PACKAGES_DIR", str(ARGOS_PACKAGES))

SAMPLE_RATE = 24000
COLD_START_SECONDS = 75

PALETTE_LIGHT = {
    "bg": "#FAF9F5",
    "sidebar": "#F0EEE6",
    "surface": "#FFFFFF",
    "input": "#FFFFFF",
    "primary": "#D97757",
    "primary_dim": "#C26646",
    "secondary": "#B0AA9E",
    "text": "#1F1E1D",
    "text_muted": "#6C6A65",
    "border": "#E5E2D9",
    "danger": "#BC4C3B",
    "ok": "#5E8A62",
    "translate": "#2F7D9E",
    "translate_fill": "rgba(47, 125, 158, 0.55)",
    "translate_on": "#1F1E1D",
    "working": "#B8860B",
}

PALETTE_DARK = {
    "bg": "#2B2A27",
    "sidebar": "#232220",
    "surface": "#353431",
    "input": "#3A3937",
    "primary": "#D97757",
    "primary_dim": "#C26646",
    "secondary": "#8A8680",
    "text": "#E8E4DD",
    "text_muted": "#9C9890",
    "border": "#444240",
    "danger": "#D4574A",
    "ok": "#6FA873",
    "translate": "#63B3D4",
    "translate_fill": "rgba(99, 179, 212, 0.55)",
    "translate_on": "#FFFFFF",
    "working": "#E8C468",
}

PALETTE = dict(PALETTE_LIGHT)

FONT_UI = "'Segoe UI Variable Text', 'Segoe UI', system-ui, sans-serif"
FONT_DISPLAY = "Georgia, Constantia, 'Book Antiqua', serif"

DEFAULTS: dict[str, Any] = {
    "voice": "am_adam",
    "speed": 1.0,
    "capture_armed": False,
    "volume": 1.0,
    "autostart_kokoro": True,
    "history_limit": 50,
    "theme": "light",
    "show_translation": True,
    "translate_mode": False,
    "source_voice": None,
    "window_geometry": None,
    "window_maximized": False,
    "in_use": None,
    "languages_offered": None,
    "favourites": [],
    "ratings": {},
    "rated_first": False,
    "compact_width": 348,
    "startup_phrase": True,
    "autoplay": False,
    "preferred_language": "en",
    "keep_original_languages": False,
    "switch_latin_voices": False,
    "compute_device": "auto",
    "cpu_threads": 0,
}


def set_theme(theme: str) -> None:
    PALETTE.clear()
    PALETTE.update(PALETTE_DARK if theme == "dark" else PALETTE_LIGHT)


DIR_FALLBACK: str | None = None


def _ensure_dirs() -> None:
    global DATA, AUDIO_OUT, DIR_FALLBACK
    for name in ("DATA", "AUDIO_OUT"):
        target = globals()[name]
        try:
            target.mkdir(parents=True, exist_ok=True)
            continue
        except OSError as exc:
            reason = exc.strerror or exc.__class__.__name__
        alt = Path(tempfile.gettempdir()) / APP_NAME / name.lower()
        try:
            alt.mkdir(parents=True, exist_ok=True)
        except OSError:
            DIR_FALLBACK = f"{target} could not be created ({reason})."
            continue
        globals()[name] = alt
        DIR_FALLBACK = (
            f"{target} could not be created ({reason}). Using {alt} instead."
        )


RECOVERED_FROM: str | None = None


def _write_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, path)


def _quarantine(path: Path) -> None:
    global RECOVERED_FROM
    kept = path.with_name(path.stem + ".corrupt" + path.suffix)
    try:
        os.replace(path, kept)
        RECOVERED_FROM = str(kept)
    except OSError:
        RECOVERED_FROM = str(path)


def load_settings() -> dict[str, Any]:
    _ensure_dirs()
    data = dict(DEFAULTS)
    settings_file = DATA / "settings.json"
    if settings_file.exists():
        try:
            data.update(json.loads(settings_file.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            _quarantine(settings_file)
        except OSError:
            pass
    return data


def save_settings(data: dict[str, Any]) -> None:
    _ensure_dirs()
    _write_atomic(
        DATA / "settings.json",
        json.dumps(data, indent=2, ensure_ascii=False),
    )


def load_history() -> list[dict[str, Any]]:
    _ensure_dirs()
    history_file = DATA / "history.json"
    if not history_file.exists():
        return []
    try:
        items = json.loads(history_file.read_text(encoding="utf-8"))
        return items if isinstance(items, list) else []
    except json.JSONDecodeError:
        _quarantine(history_file)
        return []
    except OSError:
        return []


def save_history(items: list[dict[str, Any]]) -> None:
    _ensure_dirs()
    _write_atomic(
        DATA / "history.json",
        json.dumps(items, indent=2, ensure_ascii=False),
    )
