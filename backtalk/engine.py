from __future__ import annotations

import datetime
import os
import subprocess
import threading
import time
from pathlib import Path

import numpy as np
import requests
import sounddevice as sd
from PySide6.QtCore import QCoreApplication, QObject, QThread, QTimer, Signal

from . import config, engine_identity, hwinfo, wavmeta
from . import __version__
from .textclean import chunk_for_synthesis, clean_for_speech, has_speakable

_NO_WINDOW = 0x08000000


class KokoroClient(QObject):

    _sig_arm = Signal()
    _sig_disarm = Signal()
    engine_reloading = Signal()
    engine_reloaded = Signal(bool)

    def __init__(self, base_url: str = config.KOKORO_URL) -> None:
        super().__init__()
        self.base_url = base_url.rstrip("/")
        self._proc: subprocess.Popen | None = None
        self._log = None
        self._external = False
        self.launch_state = "not-attempted"
        self._settings: dict | None = None
        self.gpu_fallback = False
        self.identity: engine_identity.EngineIdentity | None = None
        self._watchdog_armed = False
        self._watchdog_restarted = False
        self._watchdog_timer: QTimer | None = None
        self._unloaded = False
        self._reload_lock = threading.Lock()
        self._sig_arm.connect(self._start_timer_direct)
        self._sig_disarm.connect(self._stop_timer_direct)

    def health(self, timeout: float = 2.0) -> bool:
        try:
            r = requests.get(f"{self.base_url}/health", timeout=timeout)
            return r.ok
        except requests.RequestException:
            return False

    def voices(self) -> list[str]:
        try:
            r = requests.get(f"{self.base_url}/v1/audio/voices", timeout=10)
            r.raise_for_status()
            data = r.json()
        except (requests.RequestException, ValueError):
            return []
        found = data.get("voices", data) if isinstance(data, dict) else data
        names: list[str] = []
        for item in found or []:
            if isinstance(item, str):
                names.append(item)
            elif isinstance(item, dict):
                name = item.get("id") or item.get("name")
                if isinstance(name, str):
                    names.append(name)
        return sorted(set(names))

    def engine_problem(self) -> str | None:
        if not config.KOKORO_ROOT.exists():
            return "The voice engine is not installed."
        if config.KOKORO_PYTHON is None or not config.KOKORO_PYTHON.exists():
            return ("The voice engine is installed but its Python runtime is "
                    "missing.")
        if not config.KOKORO_HOST.exists():
            return "The voice engine launcher is missing."
        return None

    def _open_log(self):
        try:
            path = config.DATA / "engine.log"
            config.DATA.mkdir(parents=True, exist_ok=True)
            if path.exists() and path.stat().st_size > 1_000_000:
                r1 = path.with_name("engine.log.1")
                r2 = path.with_name("engine.log.2")
                legacy = path.with_name("engine.log.old")
                if r1.exists():
                    r1.replace(r2)
                elif legacy.exists():
                    legacy.replace(r2)
                path.replace(r1)
            fh = open(path, "a", encoding="utf-8", errors="replace")
            fh.write("[launch] " + time.strftime("%Y-%m-%d %H:%M:%S") + "\n")
            fh.flush()
            return fh
        except OSError:
            return subprocess.DEVNULL

    def start_server(self, *, known_dead: bool = False) -> None:
        if not known_dead and self.health():
            self._external = True
            self.launch_state = "adopted"
            return
        if self.engine_problem() is not None:
            self.launch_state = "refused"
            return

        env = os.environ.copy()
        for leak in ("VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH"):
            env.pop(leak, None)
        env["PYTHONNOUSERSITE"] = "1"
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8:replace"
        if env.get("API_LOG_LEVEL", "").upper() not in ("WARNING", "ERROR", "CRITICAL"):
            env["API_LOG_LEVEL"] = "WARNING"
        device = str(self._settings.get("compute_device", "auto")) if self._settings else "auto"
        if device not in ("auto", "cpu", "gpu"):
            device = "auto"
        if device == "gpu" and not self._engine_has_cuda():
            device = "cpu"
            self.gpu_fallback = True
        else:
            self.gpu_fallback = False
        if device != "auto":
            env["USE_GPU"] = "true" if device == "gpu" else "false"
        threads = self._settings.get("cpu_threads", 0) if self._settings else 0
        try:
            threads = int(threads)
        except (TypeError, ValueError):
            threads = 0
        if threads > 0:
            env["OMP_NUM_THREADS"] = str(threads)
        if self.gpu_fallback:
            self._log_gpu_fallback_reason()

        log = self._open_log()
        self._log = log if log is not subprocess.DEVNULL else None
        self._proc = subprocess.Popen(
            [
                str(config.KOKORO_PYTHON),
                str(config.KOKORO_HOST),
                str(config.KOKORO_PORT),
                str(config.DATA),
            ],
            cwd=str(config.KOKORO_ROOT),
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
            creationflags=_NO_WINDOW,
        )
        self._arm_watchdog()
        self.launch_state = "launched"

    def _engine_has_cuda(self) -> bool:
        try:
            if config.KOKORO_PYTHON is None:
                return False
            base = config.KOKORO_PYTHON.parent
            candidates = (
                base / "Lib" / "site-packages" / "torch" / "version.py",
                base.parent / "Lib" / "site-packages" / "torch" / "version.py",
            )
            for version_file in candidates:
                if version_file.exists():
                    text = version_file.read_text(
                        encoding="utf-8", errors="replace")
                    return "+cpu" not in text
            return False
        except Exception:
            return False

    def _log_gpu_fallback_reason(self) -> None:
        try:
            path = config.DATA / "engine.log"
            config.DATA.mkdir(parents=True, exist_ok=True)
            line = time.strftime("%Y-%m-%d %H:%M:%S") + \
                " compute_device=gpu requested but engine torch is CPU-only; " + \
                "running on CPU\n"
            with open(path, "a", encoding="utf-8", errors="replace") as fh:
                fh.write(line)
        except Exception:
            pass

    def identify(self, voice_count: int | None = None) -> engine_identity.EngineIdentity:
        try:
            ident = engine_identity.describe(
                data_dir=config.DATA,
                port=config.KOKORO_PORT,
                root=config.KOKORO_ROOT,
                python=config.KOKORO_PYTHON,
                root_source=config.KOKORO_ROOT_SOURCE,
                python_source=config.KOKORO_PYTHON_SOURCE,
                launch_state=self.launch_state,
                voice_count=voice_count,
            )
        except Exception as exc:
            ident = engine_identity.EngineIdentity(
                origin="unknown",
                trusted=False,
                root=None,
                python=None,
                version=None,
                pid=None,
                port=config.KOKORO_PORT,
                started=None,
                voices=voice_count,
                root_source=getattr(config, "KOKORO_ROOT_SOURCE", "unknown"),
                python_source=getattr(config, "KOKORO_PYTHON_SOURCE", "unknown"),
                notes=[f"unverified: identify() failed: {exc}"],
            )
        self.identity = ident
        return ident

    def log_identity(self, ident: engine_identity.EngineIdentity) -> None:
        line = ""
        try:
            line = engine_identity.identity_line(ident)
        except Exception as exc:
            line = "[identity] line could not be built: " + repr(exc)
        for name in ("engine.log", "identity.log"):
            try:
                config.DATA.mkdir(parents=True, exist_ok=True)
                with open(config.DATA / name, "a", encoding="utf-8",
                          errors="replace") as fh:
                    fh.write(line + "\n")
                    fh.flush()
            except Exception as exc:
                try:
                    with open(config.DATA / "identity-error.log", "a",
                              encoding="utf-8", errors="replace") as efh:
                        efh.write(name + ": " + type(exc).__name__ + ": "
                                  + str(exc) + "\n")
                except Exception:
                    pass

    @staticmethod
    def _kill_tree(pid: int) -> None:
        root = os.environ.get("SystemRoot", r"C:\Windows")
        taskkill = os.path.join(root, "System32", "taskkill.exe")
        try:
            subprocess.run(
                [taskkill, "/PID", str(pid), "/T", "/F"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                creationflags=_NO_WINDOW,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError):
            pass

    def stop_server(self) -> bool:
        self._disarm_watchdog()
        proc, self._proc = self._proc, None
        stopped = False
        try:
            if proc is not None:
                if proc.poll() is None:
                    try:
                        self._kill_tree(proc.pid)
                        proc.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    except OSError:
                        pass
                    stopped = True
                return stopped
            if self._external:
                ident = self.identity
                if (
                    ident is not None
                    and ident.origin == "adopted"
                    and ident.trusted
                    and ident.pid is not None
                ):
                    try:
                        owner = engine_identity.port_owner_pid(
                            config.KOKORO_PORT
                        )
                    except Exception:
                        owner = None
                    if owner == ident.pid:
                        self._kill_tree(ident.pid)
                        stopped = True
                return stopped
            return False
        finally:
            if self._log is not None:
                try:
                    self._log.close()
                except OSError:
                    pass
                self._log = None

    def unload(self) -> bool:
        if not self.stop_server():
            return False
        self._unloaded = True
        return True

    @property
    def unloaded(self) -> bool:
        return self._unloaded

    def ensure_loaded(self) -> bool:
        if not self._unloaded:
            return True
        with self._reload_lock:
            if not self._unloaded:
                return True
            try:
                self.engine_reloading.emit()
            except (AttributeError, RuntimeError):
                pass
            self._external = False
            self.start_server()
            ok = (self.launch_state in ("launched", "adopted")
                  and self.wait_until_ready(timeout=240.0))
            if ok:
                self._unloaded = False
            try:
                self.engine_reloaded.emit(ok)
            except (AttributeError, RuntimeError):
                pass
            return ok

    def _is_owner_thread(self) -> bool:
        app = QCoreApplication.instance()
        return app is None or QThread.currentThread() == app.thread()

    def _arm_watchdog(self) -> None:
        if self._proc is None or self._external:
            return
        self._watchdog_armed = True
        if self._is_owner_thread():
            self._start_timer_direct()
        else:
            try:
                self._sig_arm.emit()
            except (AttributeError, RuntimeError):
                pass

    def _start_timer_direct(self) -> None:
        if not self._watchdog_armed:
            return
        if self._watchdog_timer is None:
            timer = QTimer()
            timer.setInterval(1000)
            timer.timeout.connect(self._watchdog_tick)
            self._watchdog_timer = timer
        self._watchdog_timer.start()

    def _disarm_watchdog(self) -> None:
        self._watchdog_armed = False
        if self._is_owner_thread():
            self._stop_timer_direct()
        else:
            try:
                self._sig_disarm.emit()
            except (AttributeError, RuntimeError):
                pass

    def _stop_timer_direct(self) -> None:
        if self._watchdog_timer is not None:
            self._watchdog_timer.stop()

    engine_died = Signal(object)

    def _watchdog_tick(self) -> None:
        if not self._watchdog_armed:
            return
        proc = self._proc
        if proc is None:
            return
        code = proc.poll()
        if code is None:
            return
        self._watchdog_armed = False
        if self._watchdog_timer is not None:
            self._watchdog_timer.stop()
        self._log_death(code)
        if not self._watchdog_restarted:
            self._watchdog_restarted = True
            self.start_server(known_dead=True)
        try:
            self.engine_died.emit(code)
        except (AttributeError, RuntimeError):
            pass

    def _log_death(self, code: int) -> None:
        try:
            config.DATA.mkdir(parents=True, exist_ok=True)
            stamp = time.strftime("%Y-%m-%d %H:%M:%S")
            with open(config.DATA / "engine.log", "a", encoding="utf-8",
                      errors="replace") as fh:
                fh.write(f"[{stamp}] engine exited with code {code}\n")
                fh.flush()
        except OSError:
            pass

    def wait_until_ready(self, timeout: float = 180.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.health(timeout=1.5):
                return True
            time.sleep(1.0)
        return False

    def _payload(self, text: str, voice: str, speed: float, fmt: str) -> dict:
        return {
            "model": "kokoro",
            "input": text,
            "voice": voice,
            "speed": max(0.25, min(4.0, float(speed))),
            "response_format": fmt,
            "stream": False,
        }

    def synth_pcm(self, text: str, voice: str, speed: float) -> np.ndarray:
        self.ensure_loaded()
        last_exc = None
        for attempt in range(4):
            try:
                r = requests.post(
                    f"{self.base_url}/v1/audio/speech",
                    json=self._payload(text, voice, speed, "pcm"),
                    timeout=180,
                )
                r.raise_for_status()
                if not r.content:
                    return np.zeros(0, dtype=np.float32)
                body = r.content
                if len(body) % 2:
                    body = body[:-1]
                pcm = np.frombuffer(body, dtype="<i2")
                return (pcm.astype(np.float32) / 32768.0).copy()
            except requests.HTTPError as exc:
                if exc.response is not None and exc.response.status_code == 500 and attempt < 3:
                    last_exc = exc
                    time.sleep(1 << attempt)
                    continue
                raise
        raise last_exc

    def synth_bytes(self, text: str, voice: str, speed: float, fmt: str) -> bytes:
        self.ensure_loaded()
        r = requests.post(
            f"{self.base_url}/v1/audio/speech",
            json=self._payload(text, voice, speed, fmt),
            timeout=600,
        )
        r.raise_for_status()
        return r.content


def log_synth_error(voice: str, text: str, reason: str) -> None:
    try:
        config.DATA.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        snippet = repr(text[:200])
        line = f"[{stamp}] voice={voice} reason={reason} text={snippet}\n"
        with open(config.DATA / "synth-error.log", "a", encoding="utf-8",
                  errors="replace") as fh:
            fh.write(line)
            fh.flush()
    except OSError:
        pass


class _SynthWorker(QThread):

    chunk_ready = Signal(object, str, int, int)
    failed = Signal(str, int)
    done = Signal(int, int, int)

    def __init__(
        self,
        client: KokoroClient,
        chunks: list[tuple[str, str]],
        speed: float,
        gen_id: int,
        start_index: int = 0,
    ) -> None:
        super().__init__()
        self._client = client
        self._chunks = chunks
        self._speed = speed
        self._gen_id = gen_id
        self._cancelled = False
        self._start_index = start_index

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        failed_count = 0
        skipped_count = 0
        for offset, (chunk, voice) in enumerate(self._chunks):
            if self._cancelled:
                return
            idx = self._start_index + offset
            if not has_speakable(chunk):
                log_synth_error(voice, chunk, "no speakable characters")
                skipped_count += 1
                continue
            try:
                samples = self._client.synth_pcm(chunk, voice, self._speed)
            except requests.HTTPError as exc:
                status = exc.response.status_code if exc.response is not None else None
                if status == 500:
                    log_synth_error(voice, chunk, f"HTTP 500: {exc}")
                    failed_count += 1
                    continue
                self.failed.emit(str(exc), self._gen_id)
                return
            except requests.RequestException as exc:
                self.failed.emit(str(exc), self._gen_id)
                return
            except Exception as exc:
                log_synth_error(voice, chunk, f"{type(exc).__name__}: {exc}")
                failed_count += 1
                continue
            if self._cancelled:
                return
            self.chunk_ready.emit(samples, chunk, idx, self._gen_id)
        self.done.emit(self._gen_id, failed_count, skipped_count)


class Player(QObject):

    position_changed = Signal(float, float)
    state_changed = Signal(str)
    chunk_spoken = Signal(str)
    error = Signal(str)
    finished = Signal()

    def __init__(self, client: KokoroClient) -> None:
        super().__init__()
        self.client = client
        self._lock = threading.Lock()
        self._buffer = np.zeros(0, dtype=np.float32)
        self._marks: list[tuple[int, str, int]] = []
        self._pos = 0
        self._volume = 1.0
        self._stream: sd.OutputStream | None = None
        self._workers: list[_SynthWorker] = []
        self._generating = False
        self._state = "idle"
        self._last_mark = -1

        self._chunks: list[str] = []
        self._voice = "af_heart"
        self._speed = 1.0
        self._gen_id = 0

        self._ticker = QTimer(self)
        self._ticker.setInterval(80)
        self._ticker.timeout.connect(self._tick)


    @property
    def state(self) -> str:
        return self._state

    @property
    def generating(self) -> bool:
        return self._generating

    def _set_state(self, value: str) -> None:
        if value != self._state:
            self._state = value
            self.state_changed.emit(value)

    def set_volume(self, value: float) -> None:
        self._volume = max(0.0, min(1.0, float(value)))

    def duration(self) -> float:
        with self._lock:
            return len(self._buffer) / config.SAMPLE_RATE


    def load(self, text: str, voice: str, speed: float) -> bool:
        cleaned = clean_for_speech(text)
        chunks = chunk_for_synthesis(cleaned)
        return self._start([(c, voice) for c in chunks], voice, speed)

    def load_mixed(self, segments: list[tuple[str, str]], speed: float) -> bool:
        chunks: list[tuple[str, str]] = []
        for text, voice in segments:
            cleaned = clean_for_speech(text)
            for chunk in chunk_for_synthesis(cleaned):
                chunks.append((chunk, voice))
        first = segments[0][1] if segments else ""
        return self._start(chunks, first, speed)

    def _start(self, chunks: list[tuple[str, str]], voice: str, speed: float) -> bool:
        self.stop()
        if not chunks:
            return False
        with self._lock:
            self._buffer = np.zeros(0, dtype=np.float32)
            self._marks = []
            self._pos = 0
        self._last_mark = -1
        self._chunks = chunks
        self._voice = voice
        self._speed = speed
        self._set_state("synthesizing")
        self._spawn_worker(chunks)
        return True

    def _spawn_worker(self, chunks: list[tuple[str, str]], start_index: int = 0) -> None:
        self._gen_id += 1
        self._generating = True
        worker = _SynthWorker(
            self.client, chunks, self._speed, self._gen_id, start_index=start_index
        )
        worker.chunk_ready.connect(self._on_chunk)
        worker.failed.connect(self._on_failed)
        worker.done.connect(self._on_generation_done)
        worker.finished.connect(lambda w=worker: self._retire(w))
        self._workers.append(worker)
        worker.start()

    def _retire(self, worker: _SynthWorker) -> None:
        if worker in self._workers:
            self._workers.remove(worker)

    def _cancel_workers(self) -> None:
        for worker in self._workers:
            worker.cancel()
        self._generating = False

    def _on_chunk(self, samples: np.ndarray, text: str, idx: int, gen_id: int) -> None:
        if gen_id != self._gen_id or samples.size == 0:
            return
        with self._lock:
            offset = len(self._buffer)
            self._buffer = np.concatenate((self._buffer, samples))
            self._marks.append((offset, text, idx))
        if self._state == "synthesizing":
            self.play()

    def _on_failed(self, message: str, gen_id: int) -> None:
        if gen_id != self._gen_id:
            return
        self._generating = False
        self.error.emit(message)
        self.stop()

    def _on_generation_done(self, gen_id: int, failed_count: int, skipped_count: int) -> None:
        if gen_id != self._gen_id:
            return
        self._generating = False
        with self._lock:
            total = len(self._buffer)
        if total == 0:
            if failed_count > 0:
                self.error.emit("Voice engine could not read this text")
            self._ticker.stop()
            if self._stream is not None:
                self._stream.stop()
            self._set_state("idle")
            self.finished.emit()


    def set_speed(self, speed: float) -> None:
        speed = max(0.25, min(4.0, float(speed)))
        if abs(speed - self._speed) < 0.01:
            return
        self._speed = speed

        if self._state not in ("playing", "paused", "synthesizing"):
            return
        if not self._chunks:
            return

        with self._lock:
            pos = self._pos
            marks = list(self._marks)
            buffered = len(self._buffer)

        if not marks:
            self._cancel_workers()
            self._spawn_worker(self._chunks)
            return

        mark_pos = 0
        for i, (offset, _text, _idx) in enumerate(marks):
            if pos >= offset:
                mark_pos = i
            else:
                break

        current_chunk_idx = marks[mark_pos][2]

        remaining = self._chunks[current_chunk_idx + 1:]
        if not remaining:
            return

        keep_to = marks[mark_pos + 1][0] if mark_pos + 1 < len(marks) else buffered

        self._cancel_workers()
        with self._lock:
            self._buffer = self._buffer[:keep_to].copy()
            self._marks = marks[: mark_pos + 1]
        self._last_mark = mark_pos
        self._spawn_worker(remaining, start_index=current_chunk_idx + 1)


    def play(self) -> None:
        if self.duration() <= 0:
            return
        if self._stream is None:
            self._stream = sd.OutputStream(
                samplerate=config.SAMPLE_RATE,
                channels=1,
                dtype="float32",
                blocksize=1024,
                callback=self._audio_callback,
            )
            self._stream.start()
        elif not self._stream.active:
            self._stream.start()
        self._ticker.start()
        self._set_state("playing")

    def pause(self) -> None:
        if self._stream is not None and self._stream.active:
            self._stream.stop()
        self._ticker.stop()
        if self._state == "playing":
            self._set_state("paused")

    def toggle(self) -> None:
        if self._state == "playing":
            self.pause()
        else:
            self.play()

    def stop(self) -> None:
        self._cancel_workers()
        self._gen_id += 1
        self._ticker.stop()
        if self._stream is not None:
            self._stream.stop()
            self._stream.close()
            self._stream = None
        with self._lock:
            self._pos = 0
        self._set_state("idle")
        self.position_changed.emit(0.0, self.duration())

    def reset(self) -> None:
        self.stop()
        with self._lock:
            self._buffer = np.zeros(0, dtype=np.float32)
            self._marks = []
            self._pos = 0
        self._last_mark = -1
        self._chunks = []
        self.position_changed.emit(0.0, 0.0)

    def seek(self, seconds: float) -> None:
        with self._lock:
            target = int(max(0.0, seconds) * config.SAMPLE_RATE)
            self._pos = min(target, len(self._buffer))
            pos, total = self._pos, len(self._buffer)
        self._last_mark = -1
        self.position_changed.emit(
            pos / config.SAMPLE_RATE, total / config.SAMPLE_RATE
        )

    def skip(self, seconds: float) -> None:
        with self._lock:
            now = self._pos / config.SAMPLE_RATE
        self.seek(now + seconds)


    def _audio_callback(self, outdata, frames, _time, _status) -> None:
        with self._lock:
            available = len(self._buffer) - self._pos
            n = max(0, min(frames, available))
            if n:
                outdata[:n, 0] = self._buffer[self._pos:self._pos + n] * self._volume
                self._pos += n
            if n < frames:
                outdata[n:, 0] = 0.0

    def _tick(self) -> None:
        with self._lock:
            pos, total = self._pos, len(self._buffer)
            marks = list(self._marks)
        self.position_changed.emit(
            pos / config.SAMPLE_RATE, total / config.SAMPLE_RATE
        )

        index = -1
        for i, (offset, _text, _idx) in enumerate(marks):
            if pos >= offset:
                index = i
            else:
                break
        if index >= 0 and index != self._last_mark:
            self._last_mark = index
            self.chunk_spoken.emit(marks[index][1])

        if pos >= total and not self._generating and total > 0:
            self._ticker.stop()
            if self._stream is not None:
                self._stream.stop()
            self._set_state("idle")
            self.finished.emit()


    def save_wav(self, path: Path) -> bool:
        import wave

        with self._lock:
            data = self._buffer.copy()
        if data.size == 0:
            return False
        pcm = np.clip(data, -1.0, 1.0)
        pcm = (pcm * 32767.0).astype("<i2")
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(config.SAMPLE_RATE)
            wf.writeframes(pcm.tobytes())
        wavmeta.stamp_in_place(
            path,
            Path(path).stem.replace("|", "-"),
            __version__,
            datetime.date.today().year,
        )
        return True
