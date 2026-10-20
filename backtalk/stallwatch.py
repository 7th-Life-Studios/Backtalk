from __future__ import annotations

import datetime as _dt
import faulthandler
import threading
import time
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QTimer

from . import config

_started = False
_stop_event = threading.Event()
_mark = 0.0


def _log_path() -> Path:
    return config.DATA / "stall-trace.log"


def _write_line(text: str) -> None:
    try:
        config.DATA.mkdir(parents=True, exist_ok=True)
        path = _log_path()
        line = _dt.datetime.now().isoformat() + " " + text.rstrip() + "\n"
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(line)
            fh.flush()
    except Exception:
        pass


def note(text: str) -> None:
    _write_line(text)


class StallDetector:

    def __init__(self) -> None:
        self.dumps_remaining = 20
        self.stalled = False
        self.stall_start = 0.0
        self.last_check_time = 0.0

    def check(self, now: float, mark: float,
              log_fn=_write_line, dump_fn=None) -> None:
        if self.last_check_time <= 0.0:
            self.last_check_time = now
            return
        late = (now - self.last_check_time) - 1.0
        self.last_check_time = now

        if late > 5.0:
            log_fn(f"process paused {late:.1f} s")
            self.stalled = False
            return

        if (now - mark) > 3.0:
            if not self.stalled:
                self.stalled = True
                self.stall_start = mark
                log_fn(f"window stall detected ({now - mark:.1f} s)")
                if self.dumps_remaining > 0:
                    self.dumps_remaining -= 1
                    if dump_fn is not None:
                        dump_fn()
                    else:
                        self._dump_trace()
            return

        if self.stalled and mark > self.stall_start:
            duration = now - self.stall_start
            log_fn(f"stall ended after {duration:.1f} s")
            self.stalled = False

    def _dump_trace(self) -> None:
        try:
            config.DATA.mkdir(parents=True, exist_ok=True)
            with open(_log_path(), "a", encoding="utf-8", errors="replace") as fh:
                faulthandler.dump_traceback(file=fh, all_threads=True)
                fh.flush()
        except Exception:
            pass


_detector = StallDetector()


def _tick() -> None:
    global _mark
    _mark = time.monotonic()


def _checker_loop() -> None:
    while not _stop_event.wait(1.0):
        if _stop_event.is_set():
            break
        try:
            _detector.check(time.monotonic(), _mark)
        except Exception:
            pass


def start() -> None:
    global _started, _mark
    if _started:
        return
    _started = True
    try:
        config.DATA.mkdir(parents=True, exist_ok=True)
        path = _log_path()
        if path.exists() and path.stat().st_size > 1_000_000:
            path.replace(path.with_name("stall-trace.log.1"))
    except Exception:
        pass

    try:
        _mark = time.monotonic()
        timer = QTimer()
        timer.setInterval(500)
        timer.timeout.connect(_tick)
        timer.start()

        app = QCoreApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(_stop_event.set)
            app._stall_timer = timer

        thread = threading.Thread(target=_checker_loop, daemon=True)
        thread.start()
    except Exception:
        pass
