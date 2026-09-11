import os
from pathlib import Path
from typing import Tuple, Optional, Any, Dict
from .exceptions import ZipNotLoadedError, ZipSecurityError


def normalize_path(path: Any) -> str:
    """内部表記の一貫性のために区切り文字をスラッシュに統一する"""
    return str(path).replace("\\", "/")


def normalize_lookup_key(path: Any) -> str:
    """
    Windows等での大文字小文字の差異や相対・絶対パスの揺らぎを吸収し、
    同一ZIPファイルを確実に同一キーとして照合するための正規化キーを生成する。
    """
    p = Path(path)
    try:
        resolved = p.resolve()
        return os.path.normcase(str(resolved)).replace("\\", "/")
    except Exception:
        return os.path.normcase(str(p)).replace("\\", "/")


def split_zip_path(path: str) -> Tuple[Optional[str], str]:
    """パス文字列からZIPファイルパスとZIP内相対パスを分離する"""
    norm_path = normalize_path(path)
    parts = norm_path.split("/")

    current_path_parts = []
    for i, part in enumerate(parts):
        current_path_parts.append(part)
        if part.lower().endswith(".zip"):
            zip_path = "/".join(current_path_parts)
            internal_parts = parts[i + 1:]
            internal_path = "/".join(internal_parts)
            return zip_path, internal_path

    return None, path


def find_matching_session(path: str, sessions: Dict[str, Any]) -> Tuple[Optional[Any], str]:
    """
    指定パスに該当するロード済みセッション（またはZipHandle）を最長一致で検索する。
    キーの照合はnormalize_lookup_keyで行い、環境依存の表記揺れを防ぐ。
    システムコールのオーバーヘッドを避けるため、メモリ辞書一致を最優先する。
    """
    norm_path = normalize_path(path)
    parts = norm_path.split("/")

    # 1. メモリ上の辞書キー直接ヒット（システムコールゼロ）
    for i in range(len(parts), 0, -1):
        potential = "/".join(parts[:i])
        if potential in sessions:
            return sessions[potential], "/".join(parts[i:])

    # 2. .zip候補に限定した物理パス解決によるキー照合
    for i in range(len(parts), 0, -1):
        if parts[i - 1].lower().endswith(".zip"):
            potential = "/".join(parts[:i])
            lookup_key = normalize_lookup_key(potential)
            if lookup_key in sessions:
                return sessions[lookup_key], "/".join(parts[i:])

    # 3. 拡張子が.zip以外の特殊ファイル名や相対パスに対するフォールバック
    if sessions:
        for i in range(len(parts), 0, -1):
            potential = "/".join(parts[:i])
            lookup_key = normalize_lookup_key(potential)
            if lookup_key in sessions:
                return sessions[lookup_key], "/".join(parts[i:])

    return None, path


def resolve_to_real_path(path: str, sessions: Dict[str, Any]) -> Path:
    """
    仮想パスをディスク上の実パス（展開先一時ディレクトリ内）に解決する。
    パス脱出（Zip Slip）の検証も行う。
    """
    session, internal_path = find_matching_session(path, sessions)

    if session:
        temp_dir_raw = session.temp_dir if hasattr(session, "temp_dir") else session["temp_dir"]
        temp_dir = Path(temp_dir_raw)

        # internal_path の相対パストラバーサルを検証
        cleaned_internal = internal_path.replace("\\", "/").lstrip("/")
        if cleaned_internal.startswith("../") or "/../" in cleaned_internal or cleaned_internal == "..":
            raise ZipSecurityError(f"Directory traversal detected in internal path: {path}")

        # 相対パーツに '..' が含まれていないかをパーツ単位でも念入りに確認
        parts = Path(cleaned_internal).parts
        if ".." in parts:
            raise ZipSecurityError(f"Directory traversal detected in internal path: {path}")

        return temp_dir / cleaned_internal

    potential_zip, _ = split_zip_path(path)
    if potential_zip:
        raise ZipNotLoadedError(f"ZIP file '{potential_zip}' is not loaded (or path '{path}' is invalid).")

    return Path(path).resolve()


# 後方互換性用エイリアス
find_longest_match_handle = find_matching_session
