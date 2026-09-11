import atexit
from contextlib import contextmanager
from pathlib import Path
from typing import Dict, List, Optional, IO, Iterator, Any

from ._types import ZipHandle, OpenMode, ProgressCallback
from .exceptions import ZipReadOnlyError, ZipAlreadyLoadedError, ZipPathError, ZipSaveError
from .path_resolver import normalize_path, normalize_lookup_key, resolve_to_real_path, find_matching_session
from .session import ZipSession
from .namespaces.z_os import Z_OS
from .namespaces.z_shutil import Z_Shutil


class Z_Lib:
    def __init__(
        self,
        workspace_dir: Optional[str] = None,
        verbose: bool = False,
        on_progress: Optional[ProgressCallback] = None,
    ):
        self._sessions: Dict[str, ZipSession] = {}
        self.workspace_dir = workspace_dir
        self.verbose = verbose
        self.on_progress = on_progress

        atexit.register(self._cleanup)

        self.os = Z_OS(self)
        self.shutil = Z_Shutil(self)

    def _log(self, message: str) -> None:
        if self.verbose:
            print(message)

    @property
    def _loaded_zips(self) -> Dict[str, ZipHandle]:
        """旧APIやテストとの互換性のためのプロパティ"""
        handles: Dict[str, ZipHandle] = {}
        for key, session in self._sessions.items():
            norm_key = normalize_path(str(session.original_path))
            handles[norm_key] = ZipHandle(
                path=str(session.original_path),
                temp_dir=str(session.temp_dir) if session.temp_dir else "",
                mode=session.mode,
            )
            # lookup_key も登録して二重に引けるようにする
            handles[key] = handles[norm_key]
        return handles

    def _get_session_for_path(self, path: str) -> Optional[ZipSession]:
        session, _ = find_matching_session(path, self._sessions)
        return session

    def load_zip(
        self,
        *paths: str,
        create: bool = False,
        mode: OpenMode = "r",
        exp_positive: Optional[List[str]] = None,
        exp_negative: Optional[List[str]] = None,
    ) -> None:
        """
        ZIPファイルをマウントし、操作可能な状態にする。
        意図しない破損を防ぐため既定モードは "r" (読み取り専用)。
        create=True の新規作成時は自動的に "rw" モードとして扱う。
        """
        effective_mode: OpenMode = "rw" if create and mode == "r" else mode
        for path in paths:
            lookup_key = normalize_lookup_key(path)
            if lookup_key in self._sessions:
                existing = self._sessions[lookup_key]
                if existing.exp_positive == exp_positive and existing.exp_negative == exp_negative:
                    self._log(f"  📦 [Z_Lib] LOAD  ⚡ already loaded — skipped   › {lookup_key}")
                    continue
                self._log(f"  📦 [Z_Lib] LOAD  🔄 reloading with new filter   › {lookup_key}")
                existing.close(save=False)
                del self._sessions[lookup_key]

            action = "create" if create else "open"
            self._log(f"  📦 [Z_Lib] LOAD  ▶  mode={effective_mode!r}  [{action}]   › {path}")
            session = ZipSession(
                path=path,
                mode=effective_mode,
                create=create,
                workspace_dir=self.workspace_dir,
                on_progress=self.on_progress,
                exp_positive=exp_positive,
                exp_negative=exp_negative,
            )
            self._sessions[lookup_key] = session
            self._log(f"     └─ ✅ mounted   temp_dir={session.temp_dir}")

    def unload_zip(self, *paths: str, save: bool = True) -> None:
        """
        指定されたZIPファイルをアンロードし、mode="rw" かつ save=True の場合は変更を保存する。
        """
        for path in paths:
            lookup_key = normalize_lookup_key(path)
            if lookup_key not in self._sessions:
                continue

            session = self._sessions[lookup_key]
            will_save = save and session.mode == "rw" and session.is_dirty
            save_label = "💾 saving" if will_save else "🚫 closing"
            self._log(f"  📦 [Z_Lib] UNLOAD  ◀  {save_label}   › {session.original_path}")

            session.close(save=save)
            del self._sessions[lookup_key]
            self._log("     └─ ✅ closed")

    def swap_zip(
        self,
        target_zips: List[str],
        create: bool = False,
        mode: OpenMode = "r",
        exp_positive: Optional[List[str]] = None,
        exp_negative: Optional[List[str]] = None,
    ) -> None:
        """
        現在のロード状態を指定リストの状態と同期させる。
        差分のみをロード/アンロードし、同一ZIPの無駄な開閉を防ぐ。
        """
        target_map = {normalize_lookup_key(p): p for p in target_zips}
        current_keys = set(self._sessions.keys())
        target_keys = set(target_map.keys())

        to_unload = current_keys - target_keys
        to_load = target_keys - current_keys
        unchanged = current_keys & target_keys

        self._log(
            f"  🔄 [Z_Lib] SWAP  │  +load={len(to_load)}  -unload={len(to_unload)}  =keep={len(unchanged)}"
        )

        for key in to_unload:
            session = self._sessions[key]
            session.close(save=True)
            del self._sessions[key]

        for key in to_load:
            original_arg = target_map[key]
            self.load_zip(
                original_arg,
                create=create,
                mode=mode,
                exp_positive=exp_positive,
                exp_negative=exp_negative,
            )

        # 保持対象でもフィルター指定が変更されていれば load_zip 側で再ロードが行われる
        for key in unchanged:
            original_arg = target_map[key]
            self.load_zip(
                original_arg,
                create=create,
                mode=mode,
                exp_positive=exp_positive,
                exp_negative=exp_negative,
            )

        self._log(f"     └─ ✅ swap complete   loaded={len(self._sessions)} ZIP(s)")

    def load_nest(
        self,
        folder: str,
        create: bool = False,
        mode: OpenMode = "r",
        exp_positive: Optional[List[str]] = None,
        exp_negative: Optional[List[str]] = None,
    ) -> None:
        """フォルダ配下のすべての .zip ファイルを再帰的に検索してロードする"""
        folder_path = Path(folder)
        self._log(f"  🔍 [Z_Lib] LOAD_NEST  ▶  scanning   › {folder_path.resolve()}")

        if not folder_path.exists():
            if create:
                folder_path.mkdir(parents=True, exist_ok=True)
                self._log("     └─ 📁 folder created")
            else:
                raise FileNotFoundError(f"Folder not found: {folder}")

        zip_files = list(folder_path.rglob("*.zip"))
        self._log(f"     └─ 🗂  found {len(zip_files)} ZIP file(s)")
        for zip_file in zip_files:
            self.load_zip(
                str(zip_file),
                create=create,
                mode=mode,
                exp_positive=exp_positive,
                exp_negative=exp_negative,
            )

    def commit(self, *paths: str) -> None:
        """編集内容を元ZIPへ確定保存する。引数なしの場合は全セッションが対象"""
        target_sessions = (
            [self._sessions[normalize_lookup_key(p)] for p in paths if normalize_lookup_key(p) in self._sessions]
            if paths else list(self._sessions.values())
        )
        for session in target_sessions:
            session.commit()

    def rollback(self, *paths: str) -> None:
        """未保存の編集内容を破棄し、初期状態に巻き戻す。引数なしの場合は全セッションが対象"""
        target_sessions = (
            [self._sessions[normalize_lookup_key(p)] for p in paths if normalize_lookup_key(p) in self._sessions]
            if paths else list(self._sessions.values())
        )
        for session in target_sessions:
            session.rollback()

    def close(self, *paths: str) -> None:
        """セッション作業領域を安全に解放する。引数なしの場合は全セッションを閉じる"""
        keys_to_close = (
            [normalize_lookup_key(p) for p in paths if normalize_lookup_key(p) in self._sessions]
            if paths else list(self._sessions.keys())
        )
        for key in keys_to_close:
            self._sessions[key].close(save=False)
            del self._sessions[key]

    @contextmanager
    def edit(self, path: str, create: bool = False) -> Iterator[ZipSession]:
        """
        編集用トランザクションを提供するコンテキストマネージャ。
        正常終了時は自動でコミットし、例外発生時は元ZIPを破壊せずに未保存データを破棄する。
        """
        lookup_key = normalize_lookup_key(path)
        existed_before = lookup_key in self._sessions
        
        if not existed_before:
            self.load_zip(path, create=create, mode="rw")
        session = self._sessions[lookup_key]
        if session.mode != "rw":
            raise ZipReadOnlyError(f"Cannot edit ZIP loaded in read-only mode: {path}")

        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            if not existed_before and lookup_key in self._sessions:
                session.close(save=False)
                del self._sessions[lookup_key]

    def open(self, path: str, mode: str = "r", **kwargs) -> IO:
        """ローカルファイルまたはZIP内ファイルを透過的に開く"""
        session = self._get_session_for_path(path)
        if session:
            # 書込・追記・更新モードの場合のガード
            is_write = any(m in mode for m in ("w", "a", "+", "x"))
            if is_write:
                session.check_writable(f"open(mode={mode!r})")
                session.mark_dirty()

        real_path = self.resolve(path)
        self._log(f"  📂 [Z_Lib] OPEN   mode={mode!r}   › {path}")
        return open(real_path, mode, **kwargs)

    def resolve(self, path: str) -> Path:
        """仮想パスを展開先実パス（Path）に解決する"""
        return resolve_to_real_path(path, self._sessions)

    def get_status(self, path: str) -> Optional[dict[str, Any]]:
        """指定パスのセッション状態・作業ディレクトリ等を取得する"""
        lookup_key = normalize_lookup_key(path)
        session = self._sessions.get(lookup_key)
        if not session:
            return None
        return {
            "path": str(session.original_path),
            "mode": session.mode,
            "status": session.status,
            "is_dirty": session.is_dirty,
            "temp_dir": str(session.temp_dir) if session.temp_dir else None,
        }

    def _cleanup(self) -> None:
        """終了時のクリーンアップ処理。未コミットの変更は安全に破棄または保存"""
        remaining = list(self._sessions.keys())
        if remaining:
            self._log(f"  🧹 [Z_Lib] CLEANUP  ▶  closing {len(remaining)} session(s) ...")
            for key in remaining:
                try:
                    self._sessions[key].close(save=True)
                except Exception:
                    pass
                del self._sessions[key]
            self._log("     └─ ✅ all sessions closed")

    def __del__(self) -> None:
        self._cleanup()
