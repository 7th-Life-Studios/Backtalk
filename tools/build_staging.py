import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools" / "dist"
ENTRY = ROOT / "run_backtalk.pyw"
ICO = ROOT / "assets" / "backtalk.ico"
PROFILES = ROOT / "data" / "voice_profiles.json"

import PyInstaller.__main__

PyInstaller.__main__.run([
    str(ENTRY),
    "--name=Backtalk",
    "--onedir",
    "--windowed",
    "--noupx",
    f"--icon={ICO}",
    f"--add-data={ICO}{os.pathsep}assets",
    f"--add-data={PROFILES}{os.pathsep}data",
    f"--paths={ROOT}",
    "--hidden-import=backtalk.app",
    f"--version-file={ROOT / 'tools' / 'version_info.py'}",
    f"--distpath={OUT}",
    f"--workpath={ROOT / 'tools' / 'build'}",
    f"--specpath={ROOT / 'tools'}",
    "--noconfirm",
])

app_dir = OUT / "Backtalk"
total = 0
by_top: dict[str, int] = {}
for f in app_dir.rglob("*"):
    if f.is_file():
        n = f.stat().st_size
        total += n
        rel = f.relative_to(app_dir)
        key = rel.parts[1] if len(rel.parts) > 2 and rel.parts[0] == "_internal" \
            else (rel.parts[0] if len(rel.parts) > 1 else "(root files)")
        by_top[key] = by_top.get(key, 0) + n

print("\n" + "=" * 60)
print(f"BUILT: {app_dir}")
print(f"TOTAL ON DISK: {total / 1e9:.2f} GB  ({total / 1e6:.0f} MB)")
print("=" * 60)
for k, v in sorted(by_top.items(), key=lambda kv: -kv[1])[:25]:
    print(f"{v/1e6:9.1f} MB  {k}")

from PyInstaller.archive.readers import CArchiveReader, ZlibArchiveReader
import tempfile
arch = CArchiveReader(str(app_dir / "Backtalk.exe"))
mods: list[str] = []
for name in arch.toc:
    if name.upper().endswith(".PYZ"):
        blob = arch.extract(name)
        tmp = Path(tempfile.mkdtemp()) / "out.pyz"
        tmp.write_bytes(blob if isinstance(blob, bytes) else blob[1])
        mods = sorted(m for m in ZlibArchiveReader(str(tmp)).toc
                      if m == "backtalk" or m.startswith("backtalk."))
        break
print("")
print(f"app modules in the PYZ: {len(mods)}")
for m in mods:
    print("   ", m)
if len(mods) < 5:
    print("WARNING: this looks like a launcher shim, not an installable app")
