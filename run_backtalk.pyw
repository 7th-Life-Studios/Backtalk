import ctypes
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path

ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID("backtalk.app.1")

sys.path.insert(0, str(Path(__file__).resolve().parent))


def _crash_log_path() -> Path:
    for root in (os.environ.get("LOCALAPPDATA"), os.environ.get("TEMP")):
        if not root:
            continue
        try:
            folder = Path(root) / "Backtalk"
            folder.mkdir(parents=True, exist_ok=True)
            return folder / "crash.log"
        except OSError:
            continue
    return Path("backtalk-crash.log")


def _report_crash(exc: BaseException) -> None:
    detail = "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))
    where = None
    try:
        path = _crash_log_path()
        with open(path, "a", encoding="utf-8", errors="replace") as fh:
            fh.write(f"\n===== {datetime.now():%Y-%m-%d %H:%M:%S} =====\n")
            fh.write(detail)
        where = path
    except OSError:
        pass

    body = "Backtalk could not start.\n\n"
    body += f"{type(exc).__name__}: {exc}\n\n"
    body += f"Details were saved to:\n{where}" if where else detail[-800:]
    try:
        ctypes.windll.user32.MessageBoxW(None, body, "Backtalk", 0x10 | 0x10000)
    except Exception:
        pass


def main() -> int:
    from backtalk.app import run

    return run()


if __name__ == "__main__":
    try:
        sys.exit(main())
    except SystemExit:
        raise
    except KeyboardInterrupt:
        sys.exit(1)
    except BaseException as exc:
        _report_crash(exc)
        sys.exit(1)
