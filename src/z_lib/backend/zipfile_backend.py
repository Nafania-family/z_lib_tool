import os
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Optional, List, Set

from .._types import ZipHandle, OpenMode
from ..exceptions import ZipPathError, ZipSecurityError, ZipSaveError

# Windows製ZIPのCP932(Shift-JIS)ダメ文字(\x5c)化けを回避するため、
# UTF-8フラグがない場合はLocal File Headerから生のバイト列を取得する。
_FLAG_UTF8 = 0x800


def _decode_zip_filename(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> str:
    if info.flag_bits & _FLAG_UTF8:
        return info.filename

    try:
        current_pos = zf.fp.tell()
        zf.fp.seek(info.header_offset)
        header = zf.fp.read(30)
        if header[:4] == b'PK\x03\x04':
            name_len = int.from_bytes(header[26:28], 'little')
            raw_bytes = zf.fp.read(name_len)
            zf.fp.seek(current_pos)
            return raw_bytes.decode("cp932").replace("\\", "/")
        zf.fp.seek(current_pos)
    except Exception:
        pass

    try:
        raw_bytes = info.filename.encode("cp437")
        return raw_bytes.decode("cp932")
    except (UnicodeDecodeError, ValueError):
        return info.filename


def _sanitize_and_validate_path(entry_name: str, dest_dir: Path) -> Path:
    # Zip Slip（親ディレクトリへの脱出）および絶対パス攻撃を防御する
    cleaned = entry_name.replace("\\", "/").lstrip("/")
    if cleaned.startswith("../") or "/../" in cleaned or cleaned == "..":
        raise ZipSecurityError(f"Directory traversal detected in ZIP entry: {entry_name}")

    dest_root = dest_dir.resolve()
    target_path = (dest_root / cleaned).resolve()
    try:
        target_path.relative_to(dest_root)
    except ValueError:
        raise ZipSecurityError(f"Path traversal outside destination directory: {entry_name}")

    return target_path


def _normalize_extensions(exts: Optional[List[str]]) -> Optional[Set[str]]:
    if not exts:
        return None
    normalized = set()
    for e in exts:
        s = str(e).strip().lower()
        if not s.startswith("."):
            s = f".{s}"
        normalized.add(s)
    return normalized


def extract_zip_safely(
    zip_path: Path,
    dest_dir: Path,
    exp_positive: Optional[List[str]] = None,
    exp_negative: Optional[List[str]] = None,
) -> None:
    dest_dir.mkdir(parents=True, exist_ok=True)
    pos_set = _normalize_extensions(exp_positive)
    neg_set = _normalize_extensions(exp_negative)
    has_filter = (pos_set is not None) or (neg_set is not None)

    try:
        with zipfile.ZipFile(zip_path, "r") as zf:
            for info in zf.infolist():
                decoded_name = _decode_zip_filename(zf, info)
                is_directory = decoded_name.endswith("/") or info.is_dir()

                if is_directory:
                    # フィルター指定時は空ディレクトリの無駄な生成を抑止し、ファイル展開時の親ディレクトリ作成に委ねる
                    if not has_filter:
                        target_path = _sanitize_and_validate_path(decoded_name, dest_dir)
                        target_path.mkdir(parents=True, exist_ok=True)
                    continue

                suffix = Path(decoded_name).suffix.lower()
                if pos_set is not None and suffix not in pos_set:
                    continue
                if neg_set is not None and suffix in neg_set:
                    continue

                target_path = _sanitize_and_validate_path(decoded_name, dest_dir)
                target_path.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, open(target_path, "wb") as dst:
                    shutil.copyfileobj(src, dst)
    except Exception:
        if dest_dir.exists():
            shutil.rmtree(dest_dir, ignore_errors=True)
        raise


def compress_directory_to_zip(source_dir: Path, target_zip_path: Path) -> None:
    target_zip_path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target_zip_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(source_dir):
            for d in dirs:
                dir_path = Path(root) / d
                rel_dir = dir_path.relative_to(source_dir).as_posix() + "/"
                # 空ディレクトリを欠落させないため、ディレクトリエントリとして登録する
                zf.writestr(rel_dir, b"")
            for f in files:
                file_path = Path(root) / f
                arcname = file_path.relative_to(source_dir).as_posix()
                zf.write(file_path, arcname)

    # 書き込み直後に整合性を検証し、壊れたアーカイブによる元ファイル上書きを防ぐ
    with zipfile.ZipFile(target_zip_path, "r") as zf:
        bad_file = zf.testzip()
        if bad_file is not None:
            raise ZipSaveError(f"Generated ZIP verification failed at entry: {bad_file}")


class ZipFileBackend:
    """後方互換性および単体利用のためのバックエンドクラス"""
    def open(self, path: str, create: bool, mode: OpenMode = "r") -> ZipHandle:
        path_obj = Path(path).resolve()
        if not path_obj.exists() and not create:
            raise FileNotFoundError(f"ZIP file not found: {path}")

        temp_dir = Path(tempfile.mkdtemp(prefix="z_lib_"))

        if path_obj.exists():
            if not zipfile.is_zipfile(path_obj):
                shutil.rmtree(temp_dir, ignore_errors=True)
                raise ZipPathError(f"File exists but is not a valid ZIP file: {path}")
            extract_zip_safely(path_obj, temp_dir)

        return ZipHandle(
            path=str(path_obj),
            temp_dir=str(temp_dir),
            mode=mode,
        )

    def close(self, handle: ZipHandle, save: bool) -> None:
        temp_dir = Path(handle["temp_dir"])
        original_path = Path(handle["path"])
        mode = handle["mode"]

        try:
            if save and mode == "rw" and temp_dir.exists():
                tmp_zip = temp_dir.parent / f"{temp_dir.name}_temp.zip"
                compress_directory_to_zip(temp_dir, tmp_zip)
                shutil.move(str(tmp_zip), str(original_path))
        finally:
            if temp_dir.exists():
                shutil.rmtree(temp_dir, ignore_errors=True)
