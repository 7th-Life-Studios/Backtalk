from __future__ import annotations

import ctypes
import json
import os
import subprocess
from ctypes import wintypes
from dataclasses import dataclass, field
from pathlib import Path

_NO_WINDOW = 0x08000000

_NETSTAT = os.path.join(
    os.environ.get("SystemRoot", r"C:\Windows"), "System32", "NETSTAT.EXE"
)

_LAUNCH_STATES = frozenset({"launched", "adopted", "not-attempted", "refused"})


@dataclass(frozen=True)
class EngineIdentity:

    origin: str
    trusted: bool
    root: str | None
    python: str | None
    version: str | None
    pid: int | None
    port: int
    started: str | None
    voices: int | None
    root_source: str
    python_source: str
    notes: list[str] = field(default_factory=list)
    owner_image: str | None = None
    owner_started: str | None = None


def read_identity_file(data_dir: Path) -> dict | None:
    try:
        path = Path(data_dir) / "engine_identity.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _same_path(a: object, b: object) -> bool:
    if not isinstance(a, str) or not isinstance(b, str):
        return False
    return os.path.normcase(os.path.normpath(a)) == os.path.normcase(
        os.path.normpath(b)
    )


_TCP_TABLE_OWNER_PID_LISTENER = 3
_AF_INET = 2
_AF_INET6 = 23


class _MIB_TCPROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("dwState", wintypes.DWORD),
        ("dwLocalAddr", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("dwRemoteAddr", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


class _MIB_TCP6ROW_OWNER_PID(ctypes.Structure):
    _fields_ = [
        ("ucLocalAddr", ctypes.c_ubyte * 16),
        ("dwLocalScopeId", wintypes.DWORD),
        ("dwLocalPort", wintypes.DWORD),
        ("ucRemoteAddr", ctypes.c_ubyte * 16),
        ("dwRemoteScopeId", wintypes.DWORD),
        ("dwRemotePort", wintypes.DWORD),
        ("dwState", wintypes.DWORD),
        ("dwOwningPid", wintypes.DWORD),
    ]


def _listener_pids(port: int) -> list[int]:
    found: list[int] = []
    try:
        iphlpapi = ctypes.WinDLL("iphlpapi")
    except OSError:
        return found
    for family, row_type in ((_AF_INET, _MIB_TCPROW_OWNER_PID),
                             (_AF_INET6, _MIB_TCP6ROW_OWNER_PID)):
        size = wintypes.DWORD(0)
        iphlpapi.GetExtendedTcpTable(
            None, ctypes.byref(size), False, family,
            _TCP_TABLE_OWNER_PID_LISTENER, 0,
        )
        if not size.value:
            continue
        buf = (ctypes.c_byte * size.value)()
        if iphlpapi.GetExtendedTcpTable(
            ctypes.byref(buf), ctypes.byref(size), False, family,
            _TCP_TABLE_OWNER_PID_LISTENER, 0,
        ) != 0:
            continue
        count = ctypes.cast(buf, ctypes.POINTER(wintypes.DWORD))[0]
        rows = ctypes.cast(
            ctypes.addressof(buf) + ctypes.sizeof(wintypes.DWORD),
            ctypes.POINTER(row_type),
        )
        for i in range(count):
            raw = rows[i].dwLocalPort & 0xFFFF
            if ((raw >> 8) | ((raw & 0xFF) << 8)) == port:
                found.append(int(rows[i].dwOwningPid))
    return found


def port_owner_pid(port: int, timeout: float = 1.5) -> int | None:
    try:
        pids = _listener_pids(port)
        if pids:
            return pids[0]
    except Exception:
        pass
    try:
        result = subprocess.run(
            [_NETSTAT, "-ano", "-p", "TCP"],
            capture_output=True,
            text=True,
            timeout=timeout,
            creationflags=_NO_WINDOW,
        )
        needle = f":{port}"
        for line in result.stdout.splitlines():
            parts = line.split()
            if len(parts) < 5 or parts[0] != "TCP":
                continue
            if not parts[1].endswith(needle):
                continue
            try:
                return int(parts[-1])
            except ValueError:
                continue
        return None
    except Exception:
        return None


def process_image(pid: int) -> tuple[str | None, str | None]:
    try:
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        handle = kernel32.OpenProcess(
            PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
        )
        if not handle:
            return None, None
        try:
            buf_len = wintypes.DWORD(32768)
            buf = ctypes.create_unicode_buffer(buf_len.value)
            ok = kernel32.QueryFullProcessImageNameW(
                handle, 0, buf, ctypes.byref(buf_len)
            )
            image = buf.value if ok else None

            creation = wintypes.FILETIME()
            exit_t = wintypes.FILETIME()
            kernel_t = wintypes.FILETIME()
            user_t = wintypes.FILETIME()
            got_times = kernel32.GetProcessTimes(
                handle,
                ctypes.byref(creation),
                ctypes.byref(exit_t),
                ctypes.byref(kernel_t),
                ctypes.byref(user_t),
            )
            started_iso = None
            if got_times:
                started_iso = _filetime_to_iso(creation)

            return (image or None), started_iso
        finally:
            kernel32.CloseHandle(handle)
    except Exception:
        return None, None


def _filetime_to_iso(ft: wintypes.FILETIME) -> str | None:
    try:
        from datetime import datetime, timedelta, timezone

        ticks = (ft.dwHighDateTime << 32) | ft.dwLowDateTime
        if ticks == 0:
            return None
        dt = datetime(1601, 1, 1, tzinfo=timezone.utc) + timedelta(
            microseconds=ticks / 10
        )
        return dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    except Exception:
        return None


def describe(
    *,
    data_dir: Path,
    port: int,
    root,
    python,
    root_source: str,
    python_source: str,
    launch_state: str,
    voice_count: int | None = None,
) -> EngineIdentity:
    notes: list[str] = []
    try:
        if launch_state not in _LAUNCH_STATES:
            notes.append(f"unverified: unknown launch_state {launch_state!r}")
            launch_state = "not-attempted"

        root_str = str(root) if root is not None else None
        python_str = str(python) if python is not None else None

        owner_pid = port_owner_pid(port)
        if owner_pid is None:
            notes.append("unverified: no LISTENING owner found via netstat")

        owner_image: str | None = None
        owner_started: str | None = None
        if owner_pid is not None:
            try:
                owner_image, owner_started = process_image(owner_pid)
            except Exception:
                owner_image, owner_started = None, None

        ident = read_identity_file(data_dir)

        if ident is None:
            if owner_pid is None:
                return EngineIdentity(
                    origin="none",
                    trusted=False,
                    root=root_str,
                    python=python_str,
                    version=None,
                    pid=None,
                    port=port,
                    started=None,
                    voices=voice_count,
                    root_source=root_source,
                    python_source=python_source,
                    notes=notes,
                    owner_image=owner_image,
                    owner_started=owner_started,
                )
            notes.append("unverified: no engine_identity.json present")
            return EngineIdentity(
                origin="unknown",
                trusted=False,
                root=root_str,
                python=python_str,
                version=None,
                pid=owner_pid,
                port=port,
                started=None,
                voices=voice_count,
                root_source=root_source,
                python_source=python_source,
                notes=notes,
                owner_image=owner_image,
                owner_started=owner_started,
            )

        file_port = ident.get("port")
        file_pid = ident.get("pid")
        file_root = ident.get("root")
        file_python = ident.get("python")
        file_version = ident.get("version")
        file_started = ident.get("started")

        port_matches = isinstance(file_port, int) and file_port == port
        pid_matches = (
            owner_pid is not None
            and isinstance(file_pid, int)
            and file_pid == owner_pid
        )
        pid_port_verified = port_matches and pid_matches

        if not pid_port_verified:
            notes.append(
                "unverified: identity file does not match the port owner"
            )
            origin = "unknown"
            trusted = False
            resolved_root, resolved_python = root_str, python_str
        else:
            root_matches = _same_path(file_root, root_str)
            python_matches = _same_path(file_python, python_str)
            trusted = root_matches and python_matches
            origin = "launched" if launch_state == "launched" else "adopted"
            resolved_root = file_root if isinstance(file_root, str) else root_str
            resolved_python = (
                file_python if isinstance(file_python, str) else python_str
            )

        pid = file_pid if isinstance(file_pid, int) else owner_pid

        return EngineIdentity(
            origin=origin,
            trusted=trusted,
            root=resolved_root,
            python=resolved_python,
            version=file_version if isinstance(file_version, str) else None,
            pid=pid,
            port=port,
            started=file_started if isinstance(file_started, str) else None,
            voices=voice_count,
            root_source=root_source,
            python_source=python_source,
            notes=notes,
            owner_image=owner_image,
            owner_started=owner_started,
        )
    except Exception as exc:
        notes.append(f"unverified: describe() failed: {exc}")
        try:
            root_str = str(root) if root is not None else None
        except Exception:
            root_str = None
        try:
            python_str = str(python) if python is not None else None
        except Exception:
            python_str = None
        return EngineIdentity(
            origin="unknown",
            trusted=False,
            root=root_str,
            python=python_str,
            version=None,
            pid=None,
            port=port,
            started=None,
            voices=voice_count,
            root_source=root_source,
            python_source=python_source,
            notes=notes,
        )


def identity_line(ident: EngineIdentity) -> str:
    try:
        return (
            "[identity] "
            f"origin={ident.origin} trusted={ident.trusted} port={ident.port} "
            f"root={ident.root} python={ident.python} version={ident.version} "
            f"pid={ident.pid} started={ident.started} voices={ident.voices} "
            f"owner={ident.owner_image} "
            f"source=root:{ident.root_source},python:{ident.python_source}"
        )
    except Exception:
        return "[identity] <unavailable>"


def notice(ident: EngineIdentity) -> str | None:
    try:
        if ident.origin == "none":
            return None
        if ident.origin == "launched" and ident.trusted:
            return None
        if ident.origin == "adopted" and ident.trusted:
            return None

        n = ident.voices if ident.voices is not None else "an unknown number of"

        if ident.origin == "adopted":
            pid = ident.pid if ident.pid is not None else "unknown"
            started = ident.started or "an unknown time"
            root = ident.root or "an unknown location"
            return (
                f"Backtalk is using a voice engine it did not start "
                f"(PID {pid}, running since {started}, {n} voices) at "
                f"{root}. See Settings."
            )

        if ident.origin == "unknown":
            if ident.owner_image:
                started = ident.owner_started or "an unknown time"
                owner_name = os.path.basename(ident.owner_image) or ident.owner_image
                return (
                    f"A voice engine on port {ident.port} did not identify "
                    f"itself ({n} voices). Backtalk did not start it: "
                    f"{owner_name}, running since {started}. See "
                    f"Settings."
                )
            return (
                f"A voice engine on port {ident.port} did not identify "
                f"itself ({n} voices). Backtalk did not start it. See "
                f"Settings."
            )

        root = ident.root or "an unknown location"
        python = ident.python or "an unknown interpreter"
        return (
            f"The engine answering on port {ident.port} is not the one "
            f"Backtalk is configured to use: {root} under {python}. See "
            f"Settings."
        )
    except Exception:
        return None
