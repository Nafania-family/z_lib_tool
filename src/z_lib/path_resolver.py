import os
import re
from pathlib import Path
from typing import Tuple, Optional, Any, Dict
from .exceptions import ZipPathError, ZipNotLoadedError
from ._types import ZipHandle

def normalize_path(path: Any) -> str:
    """
    Normalize path separators to forward slashes for internal consistency.
    Accepts both str and pathlib.Path objects.
    """
    return str(path).replace("\\", "/")

def split_zip_path(path: str) -> Tuple[Optional[str], str]:
    """
    Split a path into the ZIP file path and the internal path.
    Use simple heuristic: first component ending with .zip is the ZIP path.
    """
    norm_path = normalize_path(path)
    parts = norm_path.split("/")
    
    current_path_parts = []
    for i, part in enumerate(parts):
        current_path_parts.append(part)
        if part.lower().endswith(".zip"):
            zip_path = "/".join(current_path_parts)
            internal_parts = parts[i+1:]
            internal_path = "/".join(internal_parts)
            return zip_path, internal_path
            
    return None, path

def find_longest_match_handle(path: str, loaded_zips: Dict[str, ZipHandle]) -> Tuple[Optional[ZipHandle], str]:
    """
    Find the best matching loaded ZIP handle for the given path using longest match.
    """
    norm_path = normalize_path(path)
    parts = norm_path.split("/")
    
    # 1. 文字列としての単純な最長一致を試す (高速化 & モックパス/テスト用)
    for i in range(len(parts), 0, -1):
        potential = "/".join(parts[:i])
        if potential in loaded_zips:
            handle = loaded_zips[potential]
            internal_path = "/".join(parts[i:])
            return handle, internal_path

    # 2. 物理パス（絶対パス）に解決して一致を試す (絶対・相対混在対応)
    for i in range(len(parts), 0, -1):
        potential_zip_path_str = "/".join(parts[:i])
        try:
            # 物理パスとして解決。存在しないパスの場合は失敗 or カレントディレクトリベースの解決になる
            abs_potential = normalize_path(str(Path(potential_zip_path_str).resolve()))
            if abs_potential in loaded_zips:
                handle = loaded_zips[abs_potential]
                internal_path = "/".join(parts[i:])
                return handle, internal_path
        except Exception:
            continue
            
    return None, path

def resolve_to_real_path(path: str, loaded_zips: Dict[str, ZipHandle]) -> Path:
    """
    Resolve a virtual path to a real temporary filesystem path.
    
    Args:
        path: The virtual path string.
        loaded_zips: A dictionary mapping resolved ZIP paths to ZipHandle.
        
    Returns:
        A pathlib.Path object pointing to the real file on disk.
        If path corresponds to a loaded ZIP root, returns the temp_dir.
        
    Raises:
        ZipNotLoadedError: If the ZIP file part of the path is not loaded.
    """
    handle, internal_path = find_longest_match_handle(path, loaded_zips)
    
    if handle:
        return Path(handle["temp_dir"]) / internal_path
    
    # If no handle matched, check if it looks like a zip path to give a better error
    potential_zip, _ = split_zip_path(path)
    if potential_zip:
        raise ZipNotLoadedError(f"ZIP file '{potential_zip}' is not loaded (or path '{path}' is invalid).")
    
    # Not a zip path, return absolute path
    return Path(path).resolve()
