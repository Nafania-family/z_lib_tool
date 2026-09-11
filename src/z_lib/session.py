import os
import shutil
import tempfile
from pathlib import Path
from typing import Optional, List

from ._types import OpenMode, ProgressCallback, SessionStatus
from .backend.zipfile_backend import extract_zip_safely, compress_directory_to_zip
from .exceptions import ZipReadOnlyError, ZipSaveError, ZipPathError
from .transfer import get_file_metadata, check_conflict, copy_to_local, atomic_replace


class ZipSession:
    def __init__(
        self,
        path: str,
        mode: OpenMode = "r",
        create: bool = False,
        workspace_dir: Optional[str] = None,
        on_progress: Optional[ProgressCallback] = None,
        exp_positive: Optional[List[str]] = None,
        exp_negative: Optional[List[str]] = None,
    ):
        self.original_path = Path(path).resolve()
        self.mode = mode
        self.create = create
        self.workspace_dir = Path(workspace_dir) if workspace_dir else None
        self.on_progress = on_progress
        self.exp_positive = exp_positive
        self.exp_negative = exp_negative

        # 展開から除外されたファイルが存在する状態で再圧縮すると元データが欠落するため、フィルターは読み取り専用に限定する
        if (self.exp_positive or self.exp_negative) and self.mode == "rw":
            raise ValueError(
                "Selective extraction with exp_positive/exp_negative is only supported in "
                "read-only mode ('r') to prevent accidental loss of unextracted files on commit."
            )

        self.status: SessionStatus = "loaded"
        self.is_dirty: bool = False

        self.temp_dir: Optional[Path] = None
        self.local_zip_copy: Optional[Path] = None
        self.session_dir: Optional[Path] = None
        self.initial_meta = get_file_metadata(self.original_path)

        self._initialize()

    def _notify(self, phase: str, progress: float, detail: str) -> None:
        if self.on_progress:
            self.on_progress(phase, progress, detail)

    def _initialize(self) -> None:
        if not self.original_path.exists():
            if not self.create:
                raise FileNotFoundError(f"ZIP file not found: {self.original_path}")
            if self.mode == "r":
                self.mode = "rw"

        # 指定の一時ストレージまたはOS既定のTemp内にセッション専用作業域を作成
        base_parent = self.workspace_dir if self.workspace_dir else None
        if base_parent:
            base_parent.mkdir(parents=True, exist_ok=True)
        self.session_dir = Path(tempfile.mkdtemp(prefix="z_lib_sess_", dir=base_parent))
        self.temp_dir = self.session_dir / "extracted"
        self.temp_dir.mkdir(parents=True, exist_ok=True)

        if self.original_path.exists():
            self._notify("transfer", 0.1, f"Copying {self.original_path.name} to local workspace")
            self.local_zip_copy = self.session_dir / "source_copy.zip"
            # クラウド同期ドライブからのI/Oアクセスを1回に集約するため、まずローカルへ全コピーする
            copy_to_local(self.original_path, self.local_zip_copy)

            self._notify("extract", 0.4, f"Extracting {self.original_path.name} locally")
            try:
                extract_zip_safely(
                    self.local_zip_copy,
                    self.temp_dir,
                    exp_positive=self.exp_positive,
                    exp_negative=self.exp_negative,
                )
            except Exception as e:
                self.close(save=False)
                raise ZipPathError(f"Failed to extract ZIP safely: {self.original_path} ({e})") from e

        self._notify("ready", 1.0, f"Mounted {self.original_path.name} ({self.mode})")

    def check_writable(self, operation: str = "write") -> None:
        if self.mode == "r":
            raise ZipReadOnlyError(
                f"Cannot perform {operation} operation on read-only ZIP: {self.original_path}"
            )

    def mark_dirty(self) -> None:
        self.check_writable("modification")
        self.is_dirty = True
        self.status = "dirty"

    def commit(self) -> None:
        if self.mode == "r":
            raise ZipReadOnlyError(f"Cannot commit changes to read-only ZIP: {self.original_path}")

        if not self.is_dirty and self.original_path.exists():
            return

        self._notify("conflict_check", 0.1, "Checking external modifications")
        check_conflict(self.original_path, self.initial_meta)

        self._notify("compress", 0.4, "Compressing local workspace to ZIP")
        staging_zip = self.session_dir / "staging_output.zip"
        try:
            compress_directory_to_zip(self.temp_dir, staging_zip)
        except Exception as e:
            self.status = "failed"
            # 障害発生時に作業中のデータを消滅させず、ユーザーが救出できるように退避パスを保持する
            raise ZipSaveError(
                f"Failed to compress or validate ZIP: {e}",
                recovery_path=str(self.temp_dir)
            ) from e

        self._notify("writeback", 0.8, f"Writing back to destination: {self.original_path}")
        try:
            atomic_replace(staging_zip, self.original_path)
        except Exception as e:
            self.status = "failed"
            raise ZipSaveError(
                f"Failed to write back ZIP to destination: {e}",
                recovery_path=str(self.temp_dir)
            ) from e

        # 次回commit時の競合誤検知を防ぐため、書き戻し完了後のタイムスタンプを基準値として再取得する
        self.initial_meta = get_file_metadata(self.original_path)
        self.is_dirty = False
        self.status = "committed"
        self._notify("commit_done", 1.0, f"Committed changes to {self.original_path.name}")

    def rollback(self) -> None:
        if not self.is_dirty:
            return

        self._notify("rollback", 0.1, "Discarding changes and restoring initial state")
        if self.temp_dir and self.temp_dir.exists():
            shutil.rmtree(self.temp_dir, ignore_errors=True)
            self.temp_dir.mkdir(parents=True, exist_ok=True)

        if self.local_zip_copy and self.local_zip_copy.exists():
            extract_zip_safely(self.local_zip_copy, self.temp_dir)

        self.is_dirty = False
        self.status = "loaded"
        self._notify("rollback_done", 1.0, "Rollback completed")

    def close(self, save: bool = False) -> None:
        try:
            if save and self.mode == "rw" and self.is_dirty:
                self.commit()
        finally:
            if self.session_dir and self.session_dir.exists():
                # 保存に失敗した状態の場合はリカバリのためディレクトリを削除せずに残す
                if self.status != "failed":
                    shutil.rmtree(self.session_dir, ignore_errors=True)
            self.status = "closed"
