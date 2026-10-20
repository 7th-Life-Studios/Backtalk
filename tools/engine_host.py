import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _seal_sys_path() -> list[str]:
    import site

    dropped: list[str] = []
    user_site = getattr(site, "USER_SITE", None)

    bundled = (Path(sys.executable).resolve()
               == (ROOT / "python" / "python.exe").resolve())

    kept = []
    for entry in sys.path:
        if not entry:
            continue
        try:
            resolved = Path(entry).resolve()
        except OSError:
            kept.append(entry)
            continue
        if user_site and resolved == Path(user_site).resolve():
            dropped.append(entry)
            continue
        if bundled and not resolved.is_relative_to(ROOT):
            dropped.append(entry)
            continue
        kept.append(entry)

    sys.path[:] = kept
    site.ENABLE_USER_SITE = False
    return dropped


_dropped = _seal_sys_path()

sys.path.insert(0, str(ROOT / "api"))
sys.path.insert(0, str(ROOT))

port = int(sys.argv[1]) if len(sys.argv) > 1 else 8880
data = Path(sys.argv[2]).resolve() if len(sys.argv) > 2 else ROOT

os.environ.setdefault("PYTHONUTF8", "1")
os.environ.setdefault("API_LOG_LEVEL", "WARNING")
os.environ.setdefault("USE_GPU", "false")
os.environ.setdefault("USE_ONNX", "false")
os.environ.setdefault("PROJECT_ROOT", str(ROOT))
os.environ.setdefault("MODEL_DIR", "src/models")
os.environ.setdefault("VOICES_DIR", "src/voices/v1_0")
os.environ.setdefault("WEB_PLAYER_PATH", str(ROOT / "web"))

version = None
try:
    version_file = ROOT / "VERSION"
    version = version_file.read_text(encoding="utf-8").strip() or None
except OSError:
    version = None

try:
    print(f"[engine_host] identity pid={os.getpid()} version={version}", flush=True)
except Exception as exc:
    pass

temp_dir = data / "engine_temp"
out_dir = data / "engine_out"
os.environ.setdefault("TEMP_FILE_DIR", str(temp_dir))
os.environ.setdefault("OUTPUT_DIR", str(out_dir))
temp_dir.mkdir(parents=True, exist_ok=True)
out_dir.mkdir(parents=True, exist_ok=True)

try:
    identity = {
        "schema": 1,
        "root": str(ROOT),
        "python": sys.executable,
        "version": version,
        "pid": os.getpid(),
        "port": port,
        "started": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "argv": sys.argv,
    }

    identity_path = data / "engine_identity.json"
    identity_tmp = identity_path.with_name(identity_path.name + ".tmp")
    with open(identity_tmp, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(identity, indent=2, ensure_ascii=False))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(identity_tmp, identity_path)
except Exception as exc:
    print(f"[engine_host] identity not written: {exc}", flush=True)

import uvicorn

if __name__ == "__main__":
    print(f"[engine_host] root={ROOT}", flush=True)
    print(f"[engine_host] python={sys.executable}", flush=True)
    print(f"[engine_host] port={port} temp={temp_dir}", flush=True)
    for entry in _dropped:
        print(f"[engine_host] REFUSED off-payload path: {entry}", flush=True)
    print(f"[engine_host] sys.path sealed to {len(sys.path)} entries", flush=True)
    uvicorn.run(
        "api.src.main:app",
        host="127.0.0.1",
        port=port,
        log_level="info",
    )
