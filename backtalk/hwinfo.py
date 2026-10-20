from __future__ import annotations

import ctypes
import os
import winreg

_cache: dict | None = None


def detect() -> dict:
    global _cache
    if _cache is not None:
        return _cache
    info = {"cores": None, "ram_bytes": None, "gpus": []}
    try:
        info["cores"] = _logical_cores()
    except Exception:
        pass
    try:
        info["ram_bytes"] = _total_ram()
    except Exception:
        pass
    try:
        info["gpus"] = _gpu_names()
    except Exception:
        pass
    _cache = info
    return info


def _logical_cores() -> int | None:
    class _SYSTEM_INFO(ctypes.Structure):
        _fields_ = [
            ("wProcessorArchitecture", ctypes.c_ushort),
            ("wReserved", ctypes.c_ushort),
            ("dwPageSize", ctypes.c_uint32),
            ("lpMinimumApplicationAddress", ctypes.c_void_p),
            ("lpMaximumApplicationAddress", ctypes.c_void_p),
            ("dwActiveProcessorMask", ctypes.c_void_p),
            ("dwNumberOfProcessors", ctypes.c_uint32),
            ("dwProcessorType", ctypes.c_uint32),
            ("dwAllocationGranularity", ctypes.c_uint32),
            ("wProcessorLevel", ctypes.c_ushort),
            ("wProcessorRevision", ctypes.c_ushort),
        ]
    si = _SYSTEM_INFO()
    ctypes.windll.kernel32.GetSystemInfo(ctypes.byref(si))
    count = si.dwNumberOfProcessors
    return count if count else None


def _total_ram() -> int | None:
    class _MEMORYSTATUSEX(ctypes.Structure):
        _fields_ = [
            ("dwLength", ctypes.c_uint32),
            ("dwMemoryLoad", ctypes.c_uint32),
            ("ullTotalPhys", ctypes.c_uint64),
            ("ullAvailPhys", ctypes.c_uint64),
            ("ullTotalPageFile", ctypes.c_uint64),
            ("ullAvailPageFile", ctypes.c_uint64),
            ("ullTotalVirtual", ctypes.c_uint64),
            ("ullAvailVirtual", ctypes.c_uint64),
            ("ullAvailExtendedVirtual", ctypes.c_uint64),
        ]
    ms = _MEMORYSTATUSEX()
    ms.dwLength = ctypes.sizeof(_MEMORYSTATUSEX)
    ok = ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(ms))
    if ok:
        return ms.ullTotalPhys
    return None


def _gpu_names() -> list[str]:
    names: list[str] = []
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r"SYSTEM\CurrentControlSet\Control\Class\{4d36e968-e325-11ce-bfc1-08002be10318}",
        )
        idx = 0
        while True:
            try:
                sub = winreg.EnumKey(key, idx)
            except OSError:
                break
            idx += 1
            try:
                vkey = winreg.OpenKey(key, sub)
                name, _ = winreg.QueryValueEx(vkey, "DriverDesc")
                winreg.CloseKey(vkey)
                name = str(name).strip()
                if name:
                    names.append(name)
            except OSError:
                continue
        winreg.CloseKey(key)
    except OSError:
        pass
    return names
