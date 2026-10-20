from __future__ import annotations

import ctypes
import logging
import logging.handlers
import os
import time
from ctypes import wintypes

from PySide6.QtCore import QMimeData, QObject, QTimer, Signal
from PySide6.QtGui import QCursor, QGuiApplication
from pynput import keyboard, mouse

from . import config, stallwatch

_log = logging.getLogger("backtalk.capture")


_get_time = time.monotonic

def _is_mouse_button_down() -> bool:
    try:
        return any(ctypes.windll.user32.GetAsyncKeyState(k) & 0x8000 for k in (1, 2, 4))
    except Exception:
        return False

def _setup_log() -> None:
    if _log.handlers:
        return
    path = config.DATA / "capture_debug.log"
    path.parent.mkdir(parents=True, exist_ok=True)
    h = logging.handlers.RotatingFileHandler(
        str(path), maxBytes=1_000_000, backupCount=2, encoding="utf-8"
    )
    h.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
    _log.addHandler(h)
    _log.setLevel(
        logging.DEBUG if os.environ.get("BACKTALK_DEBUG") else logging.WARNING
    )

_user32 = ctypes.windll.user32
_kernel32 = ctypes.windll.kernel32

_SETTLE_MS = 90
_READ_MS = 140
_RETRY_MS = 90
_READ_RETRIES = 3
_DEBOUNCE_S = 0.35
_MIN_CHARS = 2
_DUP_WINDOW_S = 3.0


_GA_ROOT = 2
_PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
_SM_CXDRAG = 68
_SM_CYDRAG = 69

_DRAG_X = max(4, int(_user32.GetSystemMetrics(_SM_CXDRAG)))
_DRAG_Y = max(4, int(_user32.GetSystemMetrics(_SM_CYDRAG)))

_CONSOLE_DRAG = 10

_CONSOLE_CLASSES = frozenset({
    "consolewindowclass",
    "cascadia_hosting_window_class",
    "pseudoconsolewindow",
    "virtualconsoleclass",
    "virtualconsoleclasswork",
    "mintty",
    "putty",
    "alacritty window",
    "org.wezfurlong.wezterm",
})

_CONSOLE_EXES = frozenset({
    "windowsterminal.exe", "conhost.exe", "openconsole.exe",
    "cmd.exe", "powershell.exe", "pwsh.exe",
    "wsl.exe", "wslhost.exe", "mintty.exe", "git-bash.exe",
    "conemu.exe", "conemu64.exe", "cmder.exe",
    "alacritty.exe", "wezterm-gui.exe", "kitty.exe",
    "hyper.exe", "tabby.exe", "terminus.exe",
    "putty.exe", "mobaxterm.exe",
})

_NON_TEXT_CLASSES = frozenset({
    "progman",
    "workerw",
    "shell_traywnd",
    "shell_secondarytraywnd",
})

_MAX_CLIP_BYTES = 64 * 1024 * 1024

def _exe_for_window(hwnd: int) -> str:
    pid = wintypes.DWORD()
    _user32.GetWindowThreadProcessId(wintypes.HWND(hwnd), ctypes.byref(pid))
    name = ""
    handle = _kernel32.OpenProcess(
        _PROCESS_QUERY_LIMITED_INFORMATION, False, pid.value
    )
    if handle:
        buf = ctypes.create_unicode_buffer(512)
        size = wintypes.DWORD(512)
        if _kernel32.QueryFullProcessImageNameW(handle, 0, buf, ctypes.byref(size)):
            name = buf.value.rsplit("\\", 1)[-1].lower()
        _kernel32.CloseHandle(handle)
    return name


def _client_rect_on_screen(hwnd: int) -> tuple[int, int, int, int] | None:
    r = wintypes.RECT()
    if not _user32.GetClientRect(wintypes.HWND(hwnd), ctypes.byref(r)):
        return None
    tl = wintypes.POINT(r.left, r.top)
    br = wintypes.POINT(r.right, r.bottom)
    if not _user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(tl)):
        return None
    if not _user32.ClientToScreen(wintypes.HWND(hwnd), ctypes.byref(br)):
        return None
    return (tl.x, tl.y, br.x, br.y)


class _Target:

    __slots__ = ("hwnd", "cls", "exe", "rect", "client", "point",
                 "console", "blocked", "reason")

    def __init__(self, hwnd: int, cls: str, exe: str,
                 rect: tuple[int, int, int, int],
                 client: tuple[int, int, int, int] | None = None,
                 point: tuple[int, int] | None = None) -> None:
        self.hwnd = hwnd
        self.cls = cls
        self.exe = exe
        self.rect = rect
        self.client = client
        self.point = point
        low = cls.lower()
        self.console = low in _CONSOLE_CLASSES or exe in _CONSOLE_EXES
        if low in _NON_TEXT_CLASSES:
            self.blocked, self.reason = True, "no text here"
        else:
            self.blocked, self.reason = False, ""

    @property
    def in_client(self) -> bool:
        if self.client is None or self.point is None:
            return True
        x, y = self.point
        left, top, right, bottom = self.client
        return left <= x < right and top <= y < bottom

    def __repr__(self) -> str:
        kind = "console" if self.console else "window"
        return f"<{self.hwnd} {kind} {self.cls!r} {self.exe!r} {self.rect}>"


def _target_under_cursor() -> _Target | None:
    try:
        pt = wintypes.POINT()
        if not _user32.GetCursorPos(ctypes.byref(pt)):
            return None
        hwnd = _user32.WindowFromPoint(pt)
        if not hwnd:
            return None
        root = _user32.GetAncestor(wintypes.HWND(hwnd), _GA_ROOT)
        if not root:
            return None
        root = int(root)
        buf = ctypes.create_unicode_buffer(256)
        _user32.GetClassNameW(wintypes.HWND(root), buf, 256)
        rect = wintypes.RECT()
        _user32.GetWindowRect(wintypes.HWND(root), ctypes.byref(rect))
        return _Target(
            root, buf.value, _exe_for_window(root),
            (rect.left, rect.top, rect.right, rect.bottom),
            _client_rect_on_screen(root), (pt.x, pt.y),
        )
    except OSError as exc:
        _log.debug("_target_under_cursor failed: %s", exc)
        return None


def _snapshot_clipboard(clipboard) -> QMimeData | None:
    try:
        src = clipboard.mimeData()
        if src is None:
            return None
        snap = QMimeData()
        total = 0
        for fmt in src.formats():
            try:
                data = src.data(fmt)
            except Exception:
                continue
            total += data.size()
            if total > _MAX_CLIP_BYTES:
                _log.debug("_snapshot_clipboard: over size cap, falling back to text")
                return None
            snap.setData(fmt, data)
        return snap
    except Exception as exc:
        _log.debug("_snapshot_clipboard failed: %s", exc)
        return None


class SelectionCapture(QObject):

    captured = Signal(str)
    _mouse_up = Signal()

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        _setup_log()
        self._armed = False
        self._listener: mouse.Listener | None = None
        self._kb = keyboard.Controller()
        self._own_hwnds: set[int] = set()
        self._last_fire = 0.0
        self._last_text = ""
        self._last_text_at = 0.0
        self._busy = False
        self._busy_since = 0.0
        self._drag_start: tuple[int, int] | None = None
        self._press_target: _Target | None = None
        self._target_is_console = False
        self._clip_snapshot: QMimeData | None = None
        self._watchdog: QTimer | None = None
        self._last_hook_event_time = 0.0
        self._last_watchdog_time = 0.0
        self._last_deaf_restart = 0.0
        self._deaf_wait = 30.0
        self._last_cursor_pos: tuple[int, int] | None = None
        self._deaf_count = 0
        self._mouse_up.connect(self._on_mouse_up)


    @property
    def armed(self) -> bool:
        return self._armed

    def set_armed(self, value: bool) -> None:
        _log.debug("set_armed(%s) current=%s", value, self._armed)
        if value == self._armed:
            return
        self._armed = value
        if value:
            self._start()
        else:
            self._stop()

    def register_window(self, hwnd: int) -> None:
        if hwnd:
            self._own_hwnds.add(int(hwnd))

    def _start(self) -> None:
        self._spawn_listener()
        if self._watchdog is None:
            self._watchdog = QTimer(self)
            self._watchdog.setInterval(3000)
            self._watchdog.timeout.connect(self._check_listener)
        self._watchdog.start()

    def _spawn_listener(self) -> None:
        alive = self._listener is not None and self._listener.running
        _log.debug("_spawn_listener: existing=%s alive=%s", self._listener is not None, alive)
        if alive:
            return
        if self._listener is not None:
            try:
                self._listener.stop()
            except Exception:
                pass
        self._listener = mouse.Listener(on_click=self._on_click, on_move=self._on_move)
        self._listener.daemon = True
        self._listener.start()
        _log.debug("_spawn_listener: new listener started, running=%s", self._listener.running)

    def _check_listener(self) -> None:
        if not self._armed:
            return
        alive = self._listener is not None and self._listener.running
        if not alive:
            _log.debug("_check_listener: listener dead, respawning")
            self._spawn_listener()
        else:
            now = _get_time()
            pos = QCursor.pos()
            cur = (pos.x(), pos.y())
            if self._last_cursor_pos is None:
                self._last_cursor_pos = cur
                self._last_watchdog_time = now
            else:
                if self._last_hook_event_time > self._last_watchdog_time:
                    self._deaf_count = 0
                    self._deaf_wait = 30.0
                elif cur != self._last_cursor_pos:
                    self._deaf_count += 1
                self._last_cursor_pos = cur
                self._last_watchdog_time = now
                if (
                    self._deaf_count >= 2
                    and not _is_mouse_button_down()
                    and (now - self._last_deaf_restart) >= self._deaf_wait
                    and not self._busy
                ):
                    if (
                        self._last_deaf_restart > 0.0
                        and self._last_hook_event_time <= self._last_deaf_restart
                    ):
                        self._deaf_wait = min(self._deaf_wait * 2.0, 600.0)
                    else:
                        self._deaf_wait = 30.0
                    self._deaf_count = 0
                    self._last_deaf_restart = now
                    msg = (
                        f"mouse hook restarted: cursor moved, hook silent "
                        f"(next check in {int(self._deaf_wait)} s)"
                    )
                    stallwatch.note(msg)
                    _log.warning(msg)
                    if self._listener is not None:
                        try:
                            self._listener.stop()
                        except Exception:
                            pass
                    self._listener = None
                    self._spawn_listener()
        if self._busy and time.monotonic() - self._busy_since > 5.0:
            _log.debug("_check_listener: clearing stale _busy flag")
            self._busy = False

    def _stop(self) -> None:
        if self._watchdog is not None:
            self._watchdog.stop()
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._last_cursor_pos = None
        self._deaf_count = 0
        self._last_hook_event_time = self._last_watchdog_time = 0.0
        self._drag_start = None
        self._press_target = None
        self._busy = False
        self._last_text = ""
        self._last_text_at = 0.0

    def shutdown(self) -> None:
        self._stop()


    def _on_move(self, x: int, y: int) -> None:
        self._last_hook_event_time = _get_time()

    def _on_click(self, x: int, y: int, button, pressed: bool) -> None:
        self._last_hook_event_time = _get_time()
        if button != mouse.Button.left:
            return
        if pressed:
            self._drag_start = (x, y)
            self._press_target = _target_under_cursor()
            return

        start = self._drag_start
        target = self._press_target
        self._drag_start = None
        self._press_target = None
        if start is None:
            return

        dx = abs(x - start[0])
        dy = abs(y - start[1])
        if dx < _DRAG_X and dy < _DRAG_Y:
            _log.debug("_on_click release: dx=%d dy=%d below the OS drag threshold "
                       "(%d,%d), that was a click", dx, dy, _DRAG_X, _DRAG_Y)
            return

        if target is None:
            _log.debug("_on_click release: no window resolved at press, skipping")
            return
        if target.blocked:
            _log.debug("_on_click release: refusing, target is %s: %r",
                       target.reason, target)
            return
        if not target.in_client:
            _log.debug("_on_click release: press was on the window frame, not its "
                       "contents: %r point=%s client=%s",
                       target, target.point, target.client)
            return
        if target.console and dx < _CONSOLE_DRAG and dy < _CONSOLE_DRAG:
            _log.debug("_on_click release: dx=%d dy=%d too short to be a terminal "
                       "selection (need %d)", dx, dy, _CONSOLE_DRAG)
            return

        now = _target_under_cursor()
        if now is None or now.hwnd != target.hwnd:
            _log.debug("_on_click release: drag crossed windows (%s -> %s), skipping",
                       target.hwnd, now.hwnd if now else None)
            return
        if now.rect != target.rect:
            _log.debug("_on_click release: window moved or resized %s -> %s, that was "
                       "a window drag, not a selection", target.rect, now.rect)
            return

        self._target_is_console = target.console
        _log.debug("_on_click: emitting _mouse_up (dx=%d dy=%d target=%r)",
                   dx, dy, target)
        self._mouse_up.emit()


    def _foreground_is_ours(self) -> bool:
        try:
            hwnd = int(_user32.GetForegroundWindow())
        except OSError:
            return False
        if hwnd in self._own_hwnds:
            return True
        if not hwnd:
            return False
        try:
            pid = wintypes.DWORD()
            _user32.GetWindowThreadProcessId(
                wintypes.HWND(hwnd), ctypes.byref(pid)
            )
        except OSError:
            return False
        return bool(pid.value) and pid.value == os.getpid()

    def _foreground_is_console(self) -> bool:
        try:
            fg = int(_user32.GetForegroundWindow())
            if not fg:
                return False
            buf = ctypes.create_unicode_buffer(256)
            _user32.GetClassNameW(wintypes.HWND(fg), buf, 256)
            return (buf.value.lower() in _CONSOLE_CLASSES
                    or _exe_for_window(fg) in _CONSOLE_EXES)
        except OSError:
            return False

    def _on_mouse_up(self) -> None:
        fg_ours = self._foreground_is_ours()
        _log.debug("_on_mouse_up: armed=%s busy=%s fg_ours=%s hwnds=%s fg=%s",
                    self._armed, self._busy, fg_ours, self._own_hwnds,
                    int(_user32.GetForegroundWindow()))
        if not self._armed or self._busy or fg_ours:
            return
        now = time.monotonic()
        if now - self._last_fire < _DEBOUNCE_S:
            _log.debug("_on_mouse_up: debounced")
            return
        self._last_fire = now
        self._busy = True
        self._busy_since = now
        _log.debug("_on_mouse_up: scheduling _send_copy")
        QTimer.singleShot(_SETTLE_MS, self._send_copy)

    def _send_copy(self) -> None:
        clipboard = QGuiApplication.clipboard()


        console = self._target_is_console or self._foreground_is_console()
        self._target_is_console = False
        combo = keyboard.Key.insert if console else "c"
        name = "Ctrl+Insert" if console else "Ctrl+C"

        saved = clipboard.text()
        self._clip_snapshot = _snapshot_clipboard(clipboard)
        _log.debug("_send_copy: saved clipboard len=%d formats=%d, sending %s",
                   len(saved),
                   len(self._clip_snapshot.formats()) if self._clip_snapshot else 0,
                   name)
        try:
            self._kb.press(keyboard.Key.ctrl)
            self._kb.press(combo)
            self._kb.release(combo)
            self._kb.release(keyboard.Key.ctrl)
        except Exception as exc:
            _log.debug("_send_copy: %s failed: %s", name, exc)
            self._clip_snapshot = None
            self._busy = False
            return
        QTimer.singleShot(_READ_MS, lambda: self._read_result(saved, 0))

    def _restore_clipboard(self, clipboard, fallback: str) -> None:
        snap = self._clip_snapshot
        self._clip_snapshot = None
        try:
            current = clipboard.mimeData()
            if current is not None and current.hasImage():
                _log.debug("_restore_clipboard: an image arrived while we were "
                           "copying, leaving the clipboard alone")
                return
        except Exception as exc:
            _log.debug("_restore_clipboard: could not inspect clipboard: %s", exc)
        if snap is not None:
            try:
                clipboard.setMimeData(snap)
                return
            except Exception as exc:
                _log.debug("_restore_clipboard: setMimeData failed: %s", exc)
        clipboard.setText(fallback)

    def _read_result(self, saved: str, attempt: int) -> None:
        clipboard = QGuiApplication.clipboard()
        text = clipboard.text()
        _log.debug("_read_result: attempt=%d saved_len=%d new_len=%d same=%s",
                    attempt, len(saved), len(text), text == saved)

        if text == saved and attempt < _READ_RETRIES:
            QTimer.singleShot(
                _RETRY_MS, lambda: self._read_result(saved, attempt + 1)
            )
            return

        try:
            if text == saved:
                _log.debug("_read_result: no new text after retries")
                self._clip_snapshot = None
                return
            self._restore_clipboard(clipboard, saved)
            text = text.strip()
            if len(text) < _MIN_CHARS:
                _log.debug("_read_result: text too short (%d chars)", len(text))
                return
            if (
                text == self._last_text
                and time.monotonic() - self._last_text_at < _DUP_WINDOW_S
            ):
                _log.debug("_read_result: duplicate text")
                return
            self._last_text = text
            self._last_text_at = time.monotonic()
            _log.debug("_read_result: emitting captured, len=%d", len(text))
            self.captured.emit(text)
        finally:
            self._clip_snapshot = None
            self._busy = False
