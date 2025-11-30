import os
import shutil
import time
import threading
import uuid
from datetime import datetime
from typing import Optional, List, Dict, TYPE_CHECKING

try:
    import libtorrent as lt  # type: ignore
    LIBTORRENT_AVAILABLE = True
except ImportError:  # pragma: no cover - runtime check
    lt = None
    LIBTORRENT_AVAILABLE = False

if TYPE_CHECKING:
    from .torrent_downloader import TorrentManager  # type: ignore  # forward ref


class TorrentJob:
    """
    Represents a single torrent download job.

    - Downloads from a magnet URI into a temporary directory.
    - While downloading, it seeds normally.
    - As soon as the download completes, the torrent is removed from the session
      so no more uploading occurs.
    - After completion, it extracts video files (by extension) into `dest_dir`
      and deletes everything else, including the temp directory.
    - Once finished (completed / error / cancelled), it notifies the manager so
      it can be removed from the job list.
    """

    def __init__(
        self,
        magnet_uri: str,
        dest_dir: str,
        temp_dir: str,
        video_exts: List[str],
        manager: "TorrentManager" = None,
    ) -> None:
        self.id = uuid.uuid4().hex
        self.magnet_uri = magnet_uri
        self.dest_dir = dest_dir
        self.temp_dir = temp_dir
        self.video_exts = {e.lower().lstrip(".") for e in video_exts if e.strip()}
        self._manager = manager

        self.name: Optional[str] = None
        self.status: str = "queued"  # queued, downloading, processing, completed, error, cancelled
        self.error: Optional[str] = None

        self.progress: float = 0.0  # 0.0–1.0
        self.download_rate: int = 0  # bytes/sec
        self.upload_rate: int = 0  # bytes/sec
        self.elapsed_seconds: float = 0.0
        self.eta_seconds: Optional[float] = None

        self.created_at: datetime = datetime.utcnow()
        self.started_at: Optional[datetime] = None
        self.completed_at: Optional[datetime] = None

        self._cancel_requested: bool = False
        self._thread = threading.Thread(target=self._run, daemon=True)

    # ------------ public API ------------

    def start(self) -> None:
        self._thread.start()

    def cancel(self) -> None:
        self._cancel_requested = True

    def join(self, timeout: Optional[float] = None) -> None:
        self._thread.join(timeout=timeout)

    def force_cleanup(self) -> None:
        """
        Best-effort cleanup of temp directory. Used when deleting a job from UI.
        """
        try:
            if os.path.isdir(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass

    def to_dict(self) -> Dict:
        """
        Serialize minimal status for JSON polling.
        """
        return {
            "id": self.id,
            "magnet": self.magnet_uri,
            "name": self.name or "",
            "status": self.status,
            "error": self.error,
            "progress": round(self.progress * 100.0, 1),
            "download_rate": self.download_rate,
            "upload_rate": self.upload_rate,
            "elapsed_seconds": int(self.elapsed_seconds),
            "eta_seconds": int(self.eta_seconds) if self.eta_seconds is not None else None,
            "created_at": self.created_at.isoformat() + "Z",
            "started_at": self.started_at.isoformat() + "Z" if self.started_at else None,
            "completed_at": self.completed_at.isoformat() + "Z" if self.completed_at else None,
        }

    # ------------ internal helpers ------------

    def _notify_manager_finished(self) -> None:
        """
        Tell the manager this job is done so it can be dropped from the list.
        """
        if self._manager is not None:
            try:
                self._manager._job_finished(self)  # type: ignore[attr-defined]
            except Exception:
                # Don't let a callback error break the job thread
                pass

    def _run(self) -> None:
        if not LIBTORRENT_AVAILABLE:
            self.status = "error"
            self.error = "python-libtorrent is not installed."
            self._notify_manager_finished()
            return

        os.makedirs(self.temp_dir, exist_ok=True)

        try:
            self._run_libtorrent()
        except Exception as exc:  # pragma: no cover - defensive
            self.status = "error"
            self.error = f"Unexpected error: {exc!r}"
            try:
                shutil.rmtree(self.temp_dir, ignore_errors=True)
            except Exception:
                pass
        finally:
            # Always notify the manager that this job is in a terminal state
            if self.status in ("completed", "error", "cancelled"):
                self._notify_manager_finished()

    def _run_libtorrent(self) -> None:
        assert lt is not None  # for type checkers

        self.status = "downloading"
        self.started_at = datetime.utcnow()
        start_ts = time.time()

        ses = lt.session()
        ses.listen_on(6881, 6891)

        params = lt.parse_magnet_uri(self.magnet_uri)
        params.save_path = self.temp_dir

        handle = ses.add_torrent(params)

        # Main download loop
        while not self._cancel_requested:
            s = handle.status()

            # Set name when metadata is available
            if not self.name:
                try:
                    ti = handle.get_torrent_info()
                    self.name = ti.name()
                except Exception:
                    pass

            self.progress = float(s.progress)
            self.download_rate = int(s.download_rate)
            self.upload_rate = int(s.upload_rate)
            self.elapsed_seconds = time.time() - start_ts

            remaining_bytes = max(0, s.total_wanted - s.total_wanted_done)
            if self.download_rate > 0 and remaining_bytes > 0:
                self.eta_seconds = remaining_bytes / float(self.download_rate)
            else:
                self.eta_seconds = None

            # seeding or finished
            if s.is_seeding or s.state == lt.torrent_status.seeding:
                break

            time.sleep(0.5)

        # Stop any further seeding
        try:
            ses.remove_torrent(handle)
        except Exception:
            pass
        try:
            ses.pause()
        except Exception:
            pass

        if self._cancel_requested:
            self.status = "cancelled"
            self._cleanup_temp()
            return

        # Post-process files
        self.status = "processing"
        self._process_files()

        self.status = "completed"
        self.progress = 1.0
        self.completed_at = datetime.utcnow()

    def _process_files(self) -> None:
        """
        Move video files to dest_dir and delete everything else.
        """
        os.makedirs(self.dest_dir, exist_ok=True)

        for root, dirs, files in os.walk(self.temp_dir):
            for fname in files:
                fpath = os.path.join(root, fname)
                ext = os.path.splitext(fname)[1].lower().lstrip(".")

                if ext in self.video_exts:
                    dest_name = fname
                    dest_path = os.path.join(self.dest_dir, dest_name)

                    # Avoid overwrite: add suffix if needed
                    base, ext_full = os.path.splitext(dest_name)
                    counter = 1
                    while os.path.exists(dest_path):
                        dest_name = f"{base}_{counter}{ext_full}"
                        dest_path = os.path.join(self.dest_dir, dest_name)
                        counter += 1

                    shutil.move(fpath, dest_path)
                else:
                    try:
                        os.remove(fpath)
                    except Exception:
                        pass

        self._cleanup_temp()

    def _cleanup_temp(self) -> None:
        try:
            if os.path.isdir(self.temp_dir):
                shutil.rmtree(self.temp_dir, ignore_errors=True)
        except Exception:
            pass


class TorrentManager:
    """
    Simple in-memory manager for multiple TorrentJob instances.

    Not persistent across process restarts. Intended for a single Flask instance.
    """

    def __init__(self, temp_root: str) -> None:
        self.temp_root = temp_root
        os.makedirs(self.temp_root, exist_ok=True)
        self._jobs: Dict[str, TorrentJob] = {}
        self._lock = threading.Lock()

    def add_job(self, magnet_uri: str, dest_dir: str, video_exts: List[str]) -> TorrentJob:
        job_temp_dir = os.path.join(self.temp_root, uuid.uuid4().hex)
        job = TorrentJob(
            magnet_uri=magnet_uri,
            dest_dir=dest_dir,
            temp_dir=job_temp_dir,
            video_exts=video_exts,
            manager=self,
        )

        with self._lock:
            self._jobs[job.id] = job

        job.start()
        return job

    def list_jobs(self) -> List[Dict]:
        with self._lock:
            # Only return jobs that are still known; finished jobs will be
            # auto-removed via _job_finished
            return [job.to_dict() for job in self._jobs.values()]

    def get_job(self, job_id: str) -> Optional[TorrentJob]:
        with self._lock:
            return self._jobs.get(job_id)

    def delete_job(self, job_id: str) -> bool:
        """
        Cancel job (if still running), clean up temp data, and remove from manager.
        """
        with self._lock:
            job = self._jobs.pop(job_id, None)

        if not job:
            return False

        job.cancel()
        job.join(timeout=1.0)
        job.force_cleanup()
        return True

    # internal: called by TorrentJob when it reaches a terminal state
    def _job_finished(self, job: TorrentJob) -> None:
        with self._lock:
            # Only remove if still present (it might have been manually deleted).
            if job.id in self._jobs:
                self._jobs.pop(job.id, None)
