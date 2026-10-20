from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import zipfile
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = Path(__file__).resolve().parents[1]
TOOLS = ROOT / "tools"
DIST = TOOLS / "dist"
APP_DIR = DIST / "Backtalk"
ENGINE_DIR = DIST / "engine"
PACKS_STAGING = DIST / "packs"
COMPONENTS_DIR = DIST / "components"
INSTALLER_DIR = ROOT / "installer"
OUTPUT_DIR = DIST / "installer"

ARGOS_USER_PACKS = Path.home() / ".local" / "share" / "argos-translate" / "packages"
DEFAULT_URL_BASE = "https://github.com/7th-Life-Studios/Backtalk/releases/download/v1.0.0"

LANG_PACKS: dict[str, dict[str, any]] = {
    "spanish": {
        "code": "es",
        "name": "Spanish",
        "packages": ["en_es", "translate-es_pt-1_0", "translate-pt_en-1_9"],
    },
    "french": {
        "code": "fr",
        "name": "French",
        "packages": ["translate-en_fr-1_9", "translate-fr_en-1_9"],
    },
    "hindi": {
        "code": "hi",
        "name": "Hindi",
        "packages": ["en_hi", "hi_en"],
    },
    "italian": {
        "code": "it",
        "name": "Italian",
        "packages": ["en_it", "it_en"],
    },
    "japanese": {
        "code": "ja",
        "name": "Japanese",
        "packages": ["en_ja", "ja_en"],
    },
    "portuguese": {
        "code": "pt",
        "name": "Portuguese",
        "packages": ["translate-en_pt-1_9", "translate-pt_en-1_9"],
    },
    "chinese": {
        "code": "zh",
        "name": "Chinese",
        "packages": ["translate-en_zh-1_9", "translate-zh_en-1_9"],
    },
}


def log(msg: str) -> None:
    print(f"[build_installer] {msg}", flush=True)


def find_iscc() -> Path:
    in_path = shutil.which("iscc")
    if in_path:
        return Path(in_path)

    candidates = [
        Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 7" / "ISCC.exe",
        Path.home() / "AppData" / "Local" / "Programs" / "Inno Setup 6" / "ISCC.exe",
        Path(r"C:\Program Files\Inno Setup 7\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup 7\ISCC.exe"),
        Path(r"C:\Program Files\Inno Setup 6\ISCC.exe"),
        Path(r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe"),
    ]
    for c in candidates:
        if c.exists():
            return c
    raise FileNotFoundError("ISCC.exe not found. Install Inno Setup 7 via winget or add it to PATH.")


def dir_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(f.stat().st_size for f in path.rglob("*") if f.is_file())


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1024 * 1024):
            h.update(chunk)
    return h.hexdigest()


def stale_reasons(root: Path = ROOT) -> list[str]:
    reasons: list[str] = []
    dist = root / "tools" / "dist"

    app_exe = dist / "Backtalk" / "Backtalk.exe"
    if app_exe.is_file():
        built = app_exe.stat().st_mtime
        sources = [
            root / "run_backtalk.pyw",
            root / "data" / "voice_profiles.json",
            root / "assets" / "backtalk.ico",
            root / "tools" / "version_info.py",
        ]
        sources += [
            f for f in (root / "backtalk").rglob("*")
            if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc"
        ]
        newer = [f for f in sources if f.is_file() and f.stat().st_mtime > built]
        if newer:
            newest = max(newer, key=lambda f: f.stat().st_mtime)
            reasons.append(
                f"The staged app in {app_exe.parent} is older than the source: "
                f"{len(newer)} file(s) changed after it was built, the newest is "
                f"{newest.relative_to(root)}. Run: python tools\\build_staging.py"
            )

    engine_dir = dist / "engine"
    if engine_dir.is_dir():
        host = root / "tools" / "engine_host.py"
        staged = engine_dir / "run_engine.py"
        same = (
            staged.is_file() and host.is_file()
            and staged.read_bytes().replace(b"\r\n", b"\n")
            == host.read_bytes().replace(b"\r\n", b"\n")
        )
        if not same:
            reasons.append(
                f"The staged engine launcher {staged} is not the same as "
                f"tools\\engine_host.py. Run: python tools\\build_engine.py "
                f"(it needs BUILD_ENGINE_KOKORO_DIR)"
            )
        leftovers = sorted(f.name for f in engine_dir.glob("*.bak"))
        if leftovers:
            reasons.append(
                f"Backup files in {engine_dir} would be packed into engine.zip: "
                f"{', '.join(leftovers)}. Delete them or rebuild the engine."
            )
    return reasons


def ensure_staging() -> None:
    if not APP_DIR.exists():
        log(f"App directory not found at {APP_DIR}. Running build_staging.py...")
        subprocess.run([sys.executable, str(TOOLS / "build_staging.py")], check=True)

    if not ENGINE_DIR.exists():
        log(f"Engine directory not found at {ENGINE_DIR}. Running build_engine.py...")
        subprocess.run([sys.executable, str(TOOLS / "build_engine.py")], check=True)


def stage_language_packs() -> None:
    PACKS_STAGING.mkdir(parents=True, exist_ok=True)
    if not ARGOS_USER_PACKS.exists():
        log(f"WARNING: User Argos packs directory {ARGOS_USER_PACKS} does not exist.")
        return

    for lang_key, info in LANG_PACKS.items():
        lang_dest = PACKS_STAGING / lang_key
        lang_dest.mkdir(parents=True, exist_ok=True)
        for pkg_name in info["packages"]:
            src_pkg = ARGOS_USER_PACKS / pkg_name
            dest_pkg = lang_dest / pkg_name
            if src_pkg.exists() and not dest_pkg.exists():
                shutil.copytree(src_pkg, dest_pkg)
    log("Staged language packs in tools/dist/packs/")


def make_zip(source_dir: Path, archive_path: Path, prefix_in_zip: str) -> None:
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = archive_path.with_suffix(".tmp")
    if tmp_path.exists():
        tmp_path.unlink()

    with zipfile.ZipFile(tmp_path, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=6) as zf:
        for f in source_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(source_dir)
                arcname = str(Path(prefix_in_zip) / rel).replace("\\", "/")
                zf.write(f, arcname)

    if archive_path.exists():
        archive_path.unlink()
    tmp_path.rename(archive_path)


def package_components(url_base: str) -> list[dict[str, any]]:
    COMPONENTS_DIR.mkdir(parents=True, exist_ok=True)
    components: list[dict[str, any]] = []

    app_archive = COMPONENTS_DIR / "app.zip"
    installed_app_sz = dir_size(APP_DIR)
    if not app_archive.exists() or app_archive.stat().st_mtime < max(f.stat().st_mtime for f in APP_DIR.rglob("*") if f.is_file()):
        log("Packaging app.zip...")
        t0 = time.time()
        make_zip(APP_DIR, app_archive, "Backtalk")
        log(f"app.zip created in {time.time()-t0:.1f}s")
    app_meta = {
        "id": "app",
        "iss_comp": "app",
        "name": "Backtalk Application",
        "archive": "app.zip",
        "url": f"{url_base}/app.zip",
        "compressed_size": app_archive.stat().st_size,
        "installed_size": installed_app_sz,
        "sha256": sha256_file(app_archive),
    }
    components.append(app_meta)

    engine_archive = COMPONENTS_DIR / "engine.zip"
    installed_engine_sz = dir_size(ENGINE_DIR)
    if not engine_archive.exists() or engine_archive.stat().st_mtime < max(f.stat().st_mtime for f in ENGINE_DIR.rglob("*") if f.is_file()):
        log("Packaging engine.zip...")
        t0 = time.time()
        make_zip(ENGINE_DIR, engine_archive, "engine")
        log(f"engine.zip created in {time.time()-t0:.1f}s")
    engine_meta = {
        "id": "engine",
        "iss_comp": "engine",
        "name": "Kokoro Voice Engine",
        "archive": "engine.zip",
        "url": f"{url_base}/engine.zip",
        "compressed_size": engine_archive.stat().st_size,
        "installed_size": installed_engine_sz,
        "sha256": sha256_file(engine_archive),
    }
    components.append(engine_meta)

    for lang_key, info in LANG_PACKS.items():
        lang_src = PACKS_STAGING / lang_key
        pack_archive = COMPONENTS_DIR / f"pack-{lang_key}.zip"
        installed_pack_sz = dir_size(lang_src)

        if not pack_archive.exists() or (lang_src.exists() and pack_archive.stat().st_mtime < max(f.stat().st_mtime for f in lang_src.rglob("*") if f.is_file())):
            log(f"Packaging pack-{lang_key}.zip...")
            t0 = time.time()
            make_zip(lang_src, pack_archive, "packs")
            log(f"pack-{lang_key}.zip created in {time.time()-t0:.1f}s")

        comp_meta = {
            "id": f"translation_{lang_key}",
            "iss_comp": f"translation\\{lang_key}",
            "name": f"Translation: {info['name']}",
            "archive": f"pack-{lang_key}.zip",
            "url": f"{url_base}/pack-{lang_key}.zip",
            "compressed_size": pack_archive.stat().st_size if pack_archive.exists() else 0,
            "installed_size": installed_pack_sz,
            "sha256": sha256_file(pack_archive) if pack_archive.exists() else "",
        }
        components.append(comp_meta)

    return components


def write_manifests(components: list[dict[str, any]], url_base: str) -> None:
    INSTALLER_DIR.mkdir(parents=True, exist_ok=True)

    manifest_data = {
        "version": "1.0.0",
        "release_url_base": url_base,
        "components": components,
    }
    json_path = INSTALLER_DIR / "components.json"
    json_path.write_text(json.dumps(manifest_data, indent=2), encoding="utf-8")
    log(f"Wrote {json_path}")

    iss_lines = [
        "; Auto-generated by tools/build_installer.py. Do not hand-edit.",
        "#ifndef ReleaseUrlBase",
        f'  #define ReleaseUrlBase "{url_base}"',
        "#endif",
        "",
    ]

    for comp in components:
        cid = comp["id"].replace("\\", "_").replace("/", "_")
        iss_lines.append(f'#define {cid}_Archive "{comp["archive"]}"')
        iss_lines.append(f'#define {cid}_Url ReleaseUrlBase + "/{comp["archive"]}"')
        iss_lines.append(f'#define {cid}_SHA256 "{comp["sha256"]}"')
        iss_lines.append(f'#define {cid}_Size {comp["compressed_size"]}')
        iss_lines.append(f'#define {cid}_InstalledSize {comp["installed_size"]}')
        iss_lines.append("")

    iss_path = INSTALLER_DIR / "components.iss"
    iss_path.write_text("\n".join(iss_lines), encoding="utf-8")
    log(f"Wrote {iss_path}")


def compile_installer(iscc: Path, offline: bool) -> Path:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    iss_file = INSTALLER_DIR / "Backtalk.iss"
    cmd = [str(iscc)]
    if offline:
        cmd.append("/DOFFLINE=1")
    cmd.append(str(iss_file))

    log(f"Compiling {'Offline' if offline else 'Web'} installer with {iscc.name}...")
    t0 = time.time()
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if res.returncode != 0:
        print(res.stdout)
        print(res.stderr)
        raise RuntimeError(f"ISCC compilation failed with code {res.returncode}")
    log(f"Compiled in {time.time()-t0:.1f}s")

    out_name = "Backtalk-Setup-Offline.exe" if offline else "Backtalk-Setup.exe"
    out_path = OUTPUT_DIR / out_name
    if not out_path.exists():
        raise FileNotFoundError(f"Expected installer binary not found at {out_path}")
    return out_path


def print_breakdown(components: list[dict[str, any]], web_exe: Path | None, offline_exe: Path | None) -> None:
    print("\n" + "=" * 78)
    print("BACKTALK COMPONENT INSTALLER BREAKDOWN")
    print("=" * 78)
    print(f"{'Component':<28} {'Archive File':<20} {'Download':>10} {'Installed':>11}")
    print("-" * 78)
    tot_dl = 0
    tot_inst = 0
    for c in components:
        dl_mb = c["compressed_size"] / 1e6
        in_mb = c["installed_size"] / 1e6
        tot_dl += c["compressed_size"]
        tot_inst += c["installed_size"]
        print(f"{c['name']:<28} {c['archive']:<20} {dl_mb:9.1f} MB {in_mb:9.1f} MB")
    print("-" * 78)
    print(f"{'TOTAL ALL COMPONENTS':<49} {tot_dl/1e9:9.2f} GB {tot_inst/1e9:9.2f} GB")
    print("=" * 78)

    print("\nINSTALLER BINARIES:")
    if web_exe and web_exe.exists():
        print(f"  Web Installer     : {web_exe} ({web_exe.stat().st_size / 1e6:.2f} MB)")
    if offline_exe and offline_exe.exists():
        print(f"  Offline Installer : {offline_exe} ({offline_exe.stat().st_size / 1e9:.2f} GB)")
    print("=" * 78 + "\n")


def main() -> int:
    offline_only = "--offline-only" in sys.argv
    web_only = "--web-only" in sys.argv
    no_iscc = "--no-iscc" in sys.argv
    url_base = os.environ.get("BACKTALK_DOWNLOAD_BASE", DEFAULT_URL_BASE)

    t_start = time.time()
    if "--allow-stale" not in sys.argv:
        reasons = stale_reasons()
        if reasons:
            for reason in reasons:
                log(reason)
            log("Nothing was built. To pack the staged folders anyway, add --allow-stale.")
            return 2
    ensure_staging()
    stage_language_packs()
    components = package_components(url_base)
    write_manifests(components, url_base)

    web_exe: Path | None = None
    offline_exe: Path | None = None

    if not no_iscc:
        iscc = find_iscc()
        log(f"Found Inno Setup Compiler: {iscc}")
        if not offline_only:
            web_exe = compile_installer(iscc, offline=False)
        if not web_only:
            offline_exe = compile_installer(iscc, offline=True)

    print_breakdown(components, web_exe, offline_exe)
    log(f"Completed in {time.time()-t_start:.1f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
