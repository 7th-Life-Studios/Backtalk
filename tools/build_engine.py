import hashlib
import os
import shutil
import subprocess
import tempfile
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "tools" / "dist" / "engine"

KOKORO = Path(os.environ.get(
    "BUILD_ENGINE_KOKORO_DIR",
    ROOT.parent / "Kokoro-FastAPI",
))
VENV_SITE_PACKAGES = KOKORO / ".venv" / "Lib" / "site-packages"
ENGINE_HOST = ROOT / "tools" / "engine_host.py"

PY_VERSION = "3.10.11"
PY_ZIP_NAME = f"python-{PY_VERSION}-embed-amd64.zip"
PY_ZIP_URL = f"https://www.python.org/ftp/python/{PY_VERSION}/{PY_ZIP_NAME}"
PY_ZIP_MD5 = "f1c0538b060e03cbb697ab3581cb73bc"

CACHE_DIR = Path(os.environ.get(
    "BUILD_ENGINE_CACHE_DIR",
    Path(tempfile.gettempdir()) / "backtalk_build_cache",
))
PY_ZIP_CACHE = CACHE_DIR / PY_ZIP_NAME

FALLBACK = os.environ.get("BUILD_ENGINE_FALLBACK") == "1"
SYSTEM_PYTHON_310 = Path(r"C:\Python310")


def log(msg: str) -> None:
    print(f"[build_engine] {msg}", flush=True)


def download_python_embed() -> Path:
    if PY_ZIP_CACHE.exists():
        log(f"using cached download: {PY_ZIP_CACHE}")
        return PY_ZIP_CACHE
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    log(f"downloading {PY_ZIP_URL}")
    tmp = PY_ZIP_CACHE.with_suffix(".part")
    urllib.request.urlretrieve(PY_ZIP_URL, tmp)
    tmp.rename(PY_ZIP_CACHE)
    log(f"saved to {PY_ZIP_CACHE}")
    return PY_ZIP_CACHE


def verify_md5(path: Path, expected: str) -> None:
    digest = hashlib.md5(path.read_bytes()).hexdigest()
    log(f"MD5 expected (python.org release page): {expected}")
    log(f"MD5 actual   (downloaded file)        : {digest}")
    if digest.lower() != expected.lower():
        raise SystemExit(f"MD5 MISMATCH for {path}. Refusing to build from this file.")
    log("MD5 OK")


def run_robocopy(src: Path, dst: Path, exclude_dirs: "list[str] | None" = None) -> None:
    dst.mkdir(parents=True, exist_ok=True)
    cmd = ["robocopy", str(src), str(dst), "/E", "/MT:16", "/R:2", "/W:2",
           "/NFL", "/NDL", "/NJH", "/NP"]
    if exclude_dirs:
        cmd += ["/XD", *exclude_dirs]
    log(f"robocopy {src} -> {dst}" + (f"  (excluding {exclude_dirs})" if exclude_dirs else ""))
    result = subprocess.run(cmd, capture_output=True, text=True, errors="replace")
    if result.stdout:
        print(result.stdout)
    if result.returncode >= 8:
        print(result.stderr)
        raise SystemExit(f"robocopy failed copying {src} -> {dst}, exit code {result.returncode}")
    log(f"robocopy exit code {result.returncode} (0-7 is success)")


def patch_pth_file(pth_path: Path) -> None:
    if not pth_path.exists():
        raise SystemExit(f"expected {pth_path} from the embeddable zip, not found")
    original = pth_path.read_text().splitlines()
    log(f"original {pth_path.name}: {original}")

    lines = ["python310.zip", ".", "Lib\\site-packages", "import site"]
    pth_path.write_text("\n".join(lines) + "\n")
    log(f"rewrote {pth_path.name}: {lines}")


def build_embeddable_python() -> None:
    py_dir = OUT / "python"
    py_dir.mkdir(parents=True, exist_ok=True)
    zip_path = download_python_embed()
    verify_md5(zip_path, PY_ZIP_MD5)

    log(f"unzipping {zip_path.name} -> {py_dir}")
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(py_dir)

    patch_pth_file(py_dir / "python310._pth")


def build_fallback_python() -> None:
    py_dir = OUT / "python"
    log(f"FALLBACK MODE: copying {SYSTEM_PYTHON_310} minus site-packages -> {py_dir}")
    py_dir.mkdir(parents=True, exist_ok=True)
    for name in ("python.exe", "pythonw.exe", "python310.dll", "vcruntime140.dll"):
        src = SYSTEM_PYTHON_310 / name
        if src.exists():
            shutil.copy2(src, py_dir / name)
            log(f"copied {name}")
        else:
            log(f"WARNING: {src} not found, skipping")
    for name in ("DLLs", "Lib", "libs"):
        src = SYSTEM_PYTHON_310 / name
        if src.exists():
            run_robocopy(src, py_dir / name, exclude_dirs=["site-packages", "__pycache__"])
        else:
            log(f"WARNING: {src} not found, skipping")


def copy_site_packages() -> None:
    dst = OUT / "python" / "Lib" / "site-packages"
    run_robocopy(VENV_SITE_PACKAGES, dst)


def copy_api_web_version() -> None:
    run_robocopy(KOKORO / "api", OUT / "api",
                 exclude_dirs=["__pycache__", "temp_files", ".git", "tests"])
    run_robocopy(KOKORO / "web", OUT / "web",
                 exclude_dirs=["__pycache__", "tests", ".git"])
    shutil.copy2(KOKORO / "VERSION", OUT / "VERSION")
    log("copied api/, web/, VERSION")


def copy_launcher() -> None:
    shutil.copy2(ENGINE_HOST, OUT / "run_engine.py")
    log(f"copied {ENGINE_HOST.name} -> run_engine.py")


def precompile() -> None:
    py = OUT / "python" / "python.exe"
    for target in (OUT / "api", OUT / "python" / "Lib" / "site-packages"):
        log(f"pre-compiling {target.name} with the payload's own python ...")
        proc = subprocess.run(
            [str(py), "-m", "compileall", "-q", "-j", "1", str(target)],
            capture_output=True, text=True,
        )
        if proc.returncode != 0:
            log(f"  compileall reported errors in {target.name}, continuing "
                f"(unparseable vendored files are expected)")
    pycs = list(OUT.rglob("*.pyc"))
    wrong = [f for f in pycs if "cpython-310" not in f.name]
    log(f"pre-compiled: {len(pycs)} .pyc files, {len(wrong)} NOT cpython-310")
    if pycs and len(wrong) > len(pycs) // 2:
        raise SystemExit(
            "BUILD ERROR: most .pyc files are not cpython-310. They were "
            "compiled by the wrong interpreter and the payload cannot use them."
        )


def sanity_check() -> None:
    required = [
        OUT / "python" / "python.exe",
        OUT / "python" / "python310.dll",
        OUT / "python" / "Lib" / "site-packages" / "torch",
        OUT / "api" / "src" / "main.py",
        OUT / "web" / "index.html",
        OUT / "VERSION",
        OUT / "run_engine.py",
    ]
    missing = [p for p in required if not p.exists()]
    if missing:
        raise SystemExit("BUILD INCOMPLETE, missing:\n" + "\n".join(str(p) for p in missing))
    log("sanity check passed: all required paths present")


def weigh_output() -> None:
    total = 0
    by_top: dict[str, int] = {}
    for f in OUT.rglob("*"):
        if f.is_file():
            n = f.stat().st_size
            total += n
            rel = f.relative_to(OUT)
            key = rel.parts[0] if len(rel.parts) > 1 else "(root files)"
            by_top[key] = by_top.get(key, 0) + n

    print("\n" + "=" * 60)
    print(f"BUILT: {OUT}")
    print(f"MODE: {'FALLBACK (system Python copy)' if FALLBACK else 'embeddable zip'}")
    print(f"TOTAL ON DISK: {total / 1e9:.2f} GB  ({total / 1e6:.0f} MB)")
    print("=" * 60)
    for k, v in sorted(by_top.items(), key=lambda kv: -kv[1]):
        print(f"{v / 1e6:9.1f} MB  {k}")


def check_kokoro() -> None:
    needed = [VENV_SITE_PACKAGES, KOKORO / "api", KOKORO / "web", KOKORO / "VERSION"]
    missing = [p for p in needed if not p.exists()]
    if missing:
        raise SystemExit(
            f"Kokoro-FastAPI checkout not found at {KOKORO}\n"
            + "".join(f"  missing: {p}\n" for p in missing)
            + "Set BUILD_ENGINE_KOKORO_DIR to the Kokoro-FastAPI folder that "
              "holds api\\, web\\ and .venv\\. Nothing was deleted."
        )
    log(f"Kokoro checkout: {KOKORO}")


def main() -> None:
    check_kokoro()
    if OUT.exists():
        log(f"removing previous build at {OUT}")
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    if FALLBACK:
        build_fallback_python()
    else:
        build_embeddable_python()

    copy_site_packages()
    copy_api_web_version()
    copy_launcher()
    precompile()
    sanity_check()
    weigh_output()


if __name__ == "__main__":
    main()
