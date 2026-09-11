import os
from typing import Any, Iterator, List, Tuple, Optional, Set, TYPE_CHECKING
from pathlib import Path
from ..path_resolver import normalize_path, find_matching_session
from .z_os_path import Z_OS_Path

if TYPE_CHECKING:
    from ..core import Z_Lib


class Z_OS:
    def __init__(self, z_lib: "Z_Lib"):
        self._z_lib = z_lib
        self.path = Z_OS_Path(z_lib)

    def listdir(self, path: str) -> List[str]:
        real_path = self._z_lib.resolve(path)
        self._z_lib._log(f"  📁 [Z_OS] listdir   › {path}")
        result = os.listdir(real_path)
        self._z_lib._log(f"     └─ {len(result)} entries")
        return result

    def mkdir(self, path: str, mode: int = 0o777) -> None:
        session = self._z_lib._get_session_for_path(path)
        if session:
            session.check_writable("mkdir")
        real_path = self._z_lib.resolve(path)
        self._z_lib._log(f"  📂 [Z_OS] mkdir   › {path}")
        os.mkdir(real_path, mode)
        if session:
            session.mark_dirty()

    def makedirs(self, path: str, mode: int = 0o777, exist_ok: bool = False) -> None:
        session = self._z_lib._get_session_for_path(path)
        if session:
            session.check_writable("makedirs")
        real_path = self._z_lib.resolve(path)
        self._z_lib._log(f"  📂 [Z_OS] makedirs   exist_ok={exist_ok}   › {path}")
        os.makedirs(real_path, mode, exist_ok)
        if session:
            session.mark_dirty()

    def remove(self, path: str) -> None:
        session = self._z_lib._get_session_for_path(path)
        if session:
            session.check_writable("remove")
        real_path = self._z_lib.resolve(path)
        self._z_lib._log(f"  🗑  [Z_OS] remove   › {path}")
        os.remove(real_path)
        if session:
            session.mark_dirty()

    def rmdir(self, path: str) -> None:
        session = self._z_lib._get_session_for_path(path)
        if session:
            session.check_writable("rmdir")
        real_path = self._z_lib.resolve(path)
        self._z_lib._log(f"  🗑  [Z_OS] rmdir   › {path}")
        os.rmdir(real_path)
        if session:
            session.mark_dirty()

    def rename(self, src: str, dst: str) -> None:
        session_src = self._z_lib._get_session_for_path(src)
        session_dst = self._z_lib._get_session_for_path(dst)
        if session_src:
            session_src.check_writable("rename (source)")
        if session_dst:
            session_dst.check_writable("rename (destination)")

        real_src = self._z_lib.resolve(src)
        real_dst = self._z_lib.resolve(dst)
        self._z_lib._log(f"  ✏️  [Z_OS] rename   {src} → {dst}")
        os.rename(real_src, real_dst)

        if session_src:
            session_src.mark_dirty()
        if session_dst and session_dst != session_src:
            session_dst.mark_dirty()

    def walk(
        self,
        top: str,
        topdown: bool = True,
        onerror: Any = None,
        followlinks: bool = False
    ) -> Iterator[Tuple[str, List[str], List[str]]]:
        self._z_lib._log(f"  🔎 [Z_OS] walk   topdown={topdown}   › {top}")
        # 再帰走査中のPath.resolve()システムコール多発を防ぐため、走査開始時に1回だけセットを作成する
        loaded_zip_paths = {
            (s.original_path.resolve() if hasattr(s, "original_path") else Path(s["path"]).resolve())
            for s in self._z_lib._sessions.values()
        }
        yield from self._walk_recursive(
            normalize_path(top),
            topdown,
            onerror,
            followlinks,
            loaded_zip_paths=loaded_zip_paths,
        )

    def _walk_recursive(
        self,
        virtual_top: str,
        topdown: bool,
        onerror: Any,
        followlinks: bool,
        loaded_zip_paths: Optional[Set[Path]] = None,
    ) -> Iterator[Tuple[str, List[str], List[str]]]:
        sessions = self._z_lib._sessions

        session, internal_path = find_matching_session(virtual_top, sessions)
        if session:
            temp_dir_raw = session.temp_dir if hasattr(session, "temp_dir") else session["temp_dir"]
            temp_dir = Path(temp_dir_raw)
            real_top = temp_dir / internal_path
            orig_path_str = str(session.original_path) if hasattr(session, "original_path") else session["path"]
            canonical_zip_key = normalize_path(orig_path_str)

            for root, dirs, files in os.walk(real_top, topdown=topdown, onerror=onerror, followlinks=followlinks):
                try:
                    rel = Path(root).relative_to(temp_dir)
                except ValueError:
                    continue

                if str(rel) == ".":
                    virtual_root = canonical_zip_key
                else:
                    virtual_root = f"{canonical_zip_key}/{normalize_path(str(rel))}"
                yield virtual_root, dirs, files
            return

        real_top = Path(virtual_top).resolve()
        if not real_top.is_dir():
            if onerror:
                onerror(OSError(f"Not a directory: {virtual_top}"))
            return

        try:
            entries = list(real_top.iterdir())
        except OSError as e:
            if onerror:
                onerror(e)
            return

        if loaded_zip_paths is None:
            loaded_zip_paths = {
                (s.original_path.resolve() if hasattr(s, "original_path") else Path(s["path"]).resolve())
                for s in sessions.values()
            }

        sub_dirs: List[str] = []
        sub_files: List[str] = []
        zip_entries: List[str] = []

        for entry in entries:
            if entry.is_dir() and not (entry.is_symlink() and not followlinks):
                sub_dirs.append(entry.name)
            # ロード対象になり得る.zip拡張子のみresolve()を実行してシステムコールを抑制
            elif entry.name.lower().endswith(".zip") and entry.resolve() in loaded_zip_paths:
                zip_entries.append(entry.name)
            else:
                sub_files.append(entry.name)

        virtual_dirs = sub_dirs + zip_entries

        if topdown:
            yield virtual_top, virtual_dirs, sub_files

        for d in sub_dirs:
            child_virtual = f"{virtual_top}/{d}"
            yield from self._walk_recursive(child_virtual, topdown, onerror, followlinks, loaded_zip_paths)

        for zname in zip_entries:
            child_virtual = normalize_path(str((real_top / zname).resolve()))
            yield from self._walk_recursive(child_virtual, topdown, onerror, followlinks, loaded_zip_paths)

        if not topdown:
            yield virtual_top, virtual_dirs, sub_files
