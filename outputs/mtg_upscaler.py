from __future__ import annotations

import hashlib
import os
import shutil
import sqlite3
import subprocess
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from PIL import Image


MODEL_ID = "realesr-animevideov3-x2-detail-v1"
OUTPUT_SCALE = 2
NCNN_SCALE = 2
_GPU_LOCK = threading.Lock()


class UpscaleError(RuntimeError):
    pass


class UpscaleCache:
    """Persistent, non-destructive AI-derived image cache."""

    def __init__(self, cache_dir: Path, app_dir: Path | None = None) -> None:
        self.cache_dir = cache_dir.expanduser().resolve()
        self.app_dir = (app_dir or Path(__file__).resolve().parent).resolve()
        self.tool_dir = self.app_dir / "tools" / "realesrgan-ncnn-vulkan"
        self.executable = self.tool_dir / "realesrgan-ncnn-vulkan.exe"
        self.models_dir = self.tool_dir / "models"
        self.root = self.cache_dir / "upscaled"
        self.output_dir = self.root / MODEL_ID
        self.database_path = self.root / "upscale_cache.sqlite3"
        self.work_dir = self.root / ".work"
        self.process_lock_path = self.root / ".gpu-upscale.lock"
        self._initialize_database()

    @property
    def available(self) -> bool:
        return self.executable.exists() and (self.models_dir / "realesr-animevideov3-x2.param").exists()

    def _initialize_database(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS upscales (
                    source_key TEXT PRIMARY KEY,
                    source_path TEXT NOT NULL,
                    source_size INTEGER NOT NULL,
                    source_mtime_ns INTEGER NOT NULL,
                    source_sha256 TEXT NOT NULL,
                    model_id TEXT NOT NULL,
                    output_scale INTEGER NOT NULL,
                    output_path TEXT NOT NULL,
                    output_size INTEGER NOT NULL,
                    completed_at TEXT NOT NULL
                )
                """
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=30)
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout=30000")
        return connection

    def _source_key(self, source: Path) -> str:
        resolved = source.expanduser().resolve()
        try:
            return resolved.relative_to(self.cache_dir).as_posix()
        except ValueError:
            digest = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:16]
            return f"external/{digest}/{resolved.name}"

    def output_path(self, source: Path) -> Path:
        key = Path(self._source_key(source))
        return self.output_dir / key.parent / f"{key.stem}.webp"

    def cached_path(self, source: Path) -> Path:
        source = source.expanduser().resolve()
        try:
            stat = source.stat()
        except OSError:
            return source
        key = self._source_key(source)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT source_size, source_mtime_ns, model_id, output_scale, output_path
                   FROM upscales WHERE source_key = ?""",
                (key,),
            ).fetchone()
        if not row:
            return source
        size, mtime_ns, model_id, scale, output_path = row
        derived = Path(output_path)
        if (
            size == stat.st_size
            and mtime_ns == stat.st_mtime_ns
            and model_id == MODEL_ID
            and scale == OUTPUT_SCALE
            and derived.exists()
            and derived.stat().st_size > 0
        ):
            return derived
        return source

    def is_cached(self, source: Path) -> bool:
        return self.cached_path(source).resolve() != source.expanduser().resolve()

    def ensure_upscaled(self, source: Path, status_callback=None) -> Path:
        results = self.ensure_batch([source], status_callback=status_callback)
        return results.get(source.expanduser().resolve(), source.expanduser().resolve())

    def ensure_batch(self, sources: list[Path], status_callback=None, progress_callback=None) -> dict[Path, Path]:
        normalized = list(dict.fromkeys(path.expanduser().resolve() for path in sources if path.exists()))
        results = {source: self.cached_path(source) for source in normalized}
        pending = [source for source in normalized if results[source] == source]
        if not pending:
            if progress_callback:
                progress_callback(0, 0)
            return results
        if not self.available:
            raise UpscaleError(f"Real-ESRGAN runtime is missing at {self.executable}")

        with _GPU_LOCK:
            # Another thread may have completed work while this one waited.
            results.update({source: self.cached_path(source) for source in pending})
            pending = [source for source in pending if results[source] == source]
            if not pending:
                if progress_callback:
                    progress_callback(0, 0)
                return results
            if status_callback:
                status_callback(f"AI upscaling {len(pending)} new image(s)")
            with self._process_lock(status_callback):
                completed = self._run_batch(pending, progress_callback=progress_callback)
            results.update(completed)
            if status_callback:
                status_callback(f"AI upscaling complete for {len(completed)}/{len(pending)} image(s)")
        return results

    @contextmanager
    def _process_lock(self, status_callback=None):
        announced = False
        while True:
            try:
                descriptor = os.open(self.process_lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                    handle.write(f"{os.getpid()}\n{time.time()}\n")
                break
            except FileExistsError:
                try:
                    age = time.time() - self.process_lock_path.stat().st_mtime
                    owner_pid = self._lock_owner_pid()
                    if (owner_pid is not None and not self._process_exists(owner_pid)) or age > 30 * 60:
                        self.process_lock_path.unlink(missing_ok=True)
                        continue
                except OSError:
                    continue
                if status_callback and not announced:
                    status_callback("Waiting for the active AI-upscale batch")
                    announced = True
                time.sleep(1)
        try:
            yield
        finally:
            self.process_lock_path.unlink(missing_ok=True)

    def _lock_owner_pid(self) -> int | None:
        try:
            first_line = self.process_lock_path.read_text(encoding="utf-8").splitlines()[0]
            return int(first_line.strip())
        except (OSError, ValueError, IndexError):
            return None

    @staticmethod
    def _process_exists(pid: int) -> bool:
        if pid <= 0:
            return False
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True

    def _run_batch(self, sources: list[Path], progress_callback=None) -> dict[Path, Path]:
        batch_root = self.work_dir / uuid4().hex
        input_dir = batch_root / "input"
        model_output_dir = batch_root / "model-output"
        input_dir.mkdir(parents=True)
        model_output_dir.mkdir(parents=True)
        source_names: dict[str, Path] = {}
        try:
            for index, source in enumerate(sources):
                name = f"{index:05d}_{source.stem}{source.suffix.lower()}"
                staged = input_dir / name
                try:
                    os.link(source, staged)
                except OSError:
                    shutil.copy2(source, staged)
                source_names[Path(name).stem] = source

            command = [
                str(self.executable),
                "-i",
                str(input_dir),
                "-o",
                str(model_output_dir),
                "-n",
                "realesr-animevideov3",
                "-s",
                str(NCNN_SCALE),
                "-m",
                str(self.models_dir),
                "-g",
                "0",
                "-t",
                "256",
                "-j",
                "1:2:2",
                "-f",
                "webp",
            ]
            log_path = batch_root / "realesrgan.log"
            with log_path.open("w", encoding="utf-8", errors="replace") as log:
                process = subprocess.Popen(
                    command,
                    cwd=self.tool_dir,
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    text=True,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                last_completed = -1
                while process.poll() is None:
                    completed_count = sum(1 for _ in model_output_dir.glob("*.webp"))
                    if progress_callback and completed_count != last_completed:
                        progress_callback(completed_count, len(sources))
                    last_completed = completed_count
                    time.sleep(0.25)
            completed_count = sum(1 for _ in model_output_dir.glob("*.webp"))
            if progress_callback:
                progress_callback(completed_count, len(sources))
            if process.returncode:
                try:
                    details = log_path.read_text(encoding="utf-8", errors="replace").strip()
                except OSError:
                    details = ""
                details = details or "Unknown Real-ESRGAN error"
                raise UpscaleError(details[-2000:])

            completed: dict[Path, Path] = {}
            for stem, source in source_names.items():
                model_output = model_output_dir / f"{stem}.webp"
                if not model_output.exists():
                    continue
                final_path = self.output_path(source)
                final_path.parent.mkdir(parents=True, exist_ok=True)
                temporary_final = final_path.with_name(f".{final_path.stem}.{uuid4().hex}.tmp.webp")
                with Image.open(source) as original, Image.open(model_output) as upscaled:
                    target_size = (original.width * OUTPUT_SCALE, original.height * OUTPUT_SCALE)
                    if upscaled.size == target_size:
                        shutil.copy2(model_output, temporary_final)
                    else:
                        reduced = upscaled.resize(target_size, Image.Resampling.LANCZOS)
                        reduced.save(temporary_final, format="WEBP", lossless=True, quality=100, method=4)
                os.replace(temporary_final, final_path)
                self._record(source, final_path)
                completed[source] = final_path
            return completed
        finally:
            shutil.rmtree(batch_root, ignore_errors=True)

    def _record(self, source: Path, output: Path) -> None:
        stat = source.stat()
        digest = hashlib.sha256()
        with source.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO upscales (
                    source_key, source_path, source_size, source_mtime_ns,
                    source_sha256, model_id, output_scale, output_path,
                    output_size, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(source_key) DO UPDATE SET
                    source_path=excluded.source_path,
                    source_size=excluded.source_size,
                    source_mtime_ns=excluded.source_mtime_ns,
                    source_sha256=excluded.source_sha256,
                    model_id=excluded.model_id,
                    output_scale=excluded.output_scale,
                    output_path=excluded.output_path,
                    output_size=excluded.output_size,
                    completed_at=excluded.completed_at
                """,
                (
                    self._source_key(source),
                    str(source),
                    stat.st_size,
                    stat.st_mtime_ns,
                    digest.hexdigest(),
                    MODEL_ID,
                    OUTPUT_SCALE,
                    str(output),
                    output.stat().st_size,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )

    def stats(self) -> dict[str, int]:
        with self._connect() as connection:
            count, size = connection.execute(
                "SELECT COUNT(*), COALESCE(SUM(output_size), 0) FROM upscales WHERE model_id = ?",
                (MODEL_ID,),
            ).fetchone()
        return {"count": int(count), "bytes": int(size)}
