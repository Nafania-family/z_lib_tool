"""
拡張子フィルター（exp_positive / exp_negative）の単体テスト
"""
import zipfile
import pytest
from pathlib import Path
from z_lib.core import Z_Lib
from z_lib.path_resolver import normalize_path


@pytest.fixture
def z_lib():
    z = Z_Lib()
    yield z
    z._cleanup()


@pytest.fixture
def sample_zip(tmp_path):
    """
    テスト用ZIP構成:
    archive.zip
      ├── data.csv
      ├── info.json
      ├── readme.txt
      ├── image.PNG (大文字拡張子)
      └── temp.log
    """
    zp = tmp_path / "archive.zip"
    with zipfile.ZipFile(zp, "w") as zf:
        zf.writestr("data.csv", "id,val\n1,100")
        zf.writestr("info.json", '{"name": "test"}')
        zf.writestr("readme.txt", "readme text")
        zf.writestr("image.PNG", b"\x89PNG\r\n\x1a\n")
        zf.writestr("temp.log", "debug log")
        zf.writestr("sub/nested.csv", "id,sub\n2,200")
        zf.writestr("sub/nested.tmp", "temp data")
    return zp


def test_exp_positive_filter(z_lib, sample_zip):
    """exp_positive（ホワイトリスト）で指定拡張子のみ展開・アクセス可能になることを確認"""
    z_lib.load_zip(str(sample_zip), exp_positive=[".csv", ".json"])
    norm_zip = normalize_path(str(sample_zip))

    # 許可された拡張子は存在する
    assert z_lib.os.path.exists(f"{norm_zip}/data.csv")
    assert z_lib.os.path.exists(f"{norm_zip}/info.json")
    assert z_lib.os.path.exists(f"{norm_zip}/sub/nested.csv")

    # 対象外の拡張子は展開されず、存在しない扱いになる
    assert not z_lib.os.path.exists(f"{norm_zip}/readme.txt")
    assert not z_lib.os.path.exists(f"{norm_zip}/image.PNG")
    assert not z_lib.os.path.exists(f"{norm_zip}/temp.log")
    assert not z_lib.os.path.exists(f"{norm_zip}/sub/nested.tmp")

    # walk の結果にも対象外ファイルが含まれない
    found_files = []
    for root, dirs, files in z_lib.os.walk(norm_zip):
        found_files.extend(files)

    assert "data.csv" in found_files
    assert "info.json" in found_files
    assert "nested.csv" in found_files
    assert "readme.txt" not in found_files
    assert "temp.log" not in found_files

    # 読み取りテスト
    with z_lib.open(f"{norm_zip}/data.csv", "r", encoding="utf-8") as fp:
        content = fp.read()
    assert "id,val" in content


def test_exp_negative_filter(z_lib, sample_zip):
    """exp_negative（ブラックリスト）で指定拡張子が除外されることを確認"""
    z_lib.load_zip(str(sample_zip), exp_negative=[".log", ".tmp"])
    norm_zip = normalize_path(str(sample_zip))

    # 除外指定されていないファイルは存在
    assert z_lib.os.path.exists(f"{norm_zip}/data.csv")
    assert z_lib.os.path.exists(f"{norm_zip}/info.json")
    assert z_lib.os.path.exists(f"{norm_zip}/readme.txt")
    assert z_lib.os.path.exists(f"{norm_zip}/image.PNG")

    # 除外指定されたファイルは存在しない
    assert not z_lib.os.path.exists(f"{norm_zip}/temp.log")
    assert not z_lib.os.path.exists(f"{norm_zip}/sub/nested.tmp")


def test_case_and_dot_insensitivity(z_lib, sample_zip):
    """大文字小文字や先頭ドットの有無を吸収して正しく判定されることを確認"""
    # ドット無し、大文字指定
    z_lib.load_zip(str(sample_zip), exp_positive=["CSV", "png"])
    norm_zip = normalize_path(str(sample_zip))

    assert z_lib.os.path.exists(f"{norm_zip}/data.csv")
    assert z_lib.os.path.exists(f"{norm_zip}/image.PNG")
    assert not z_lib.os.path.exists(f"{norm_zip}/info.json")


def test_rw_mode_with_filter_raises_error(z_lib, sample_zip):
    """フィルター指定時に mode='rw' を指定すると、安全のため ValueError になることを確認"""
    with pytest.raises(ValueError, match="Selective extraction with exp_positive/exp_negative is only supported in read-only mode"):
        z_lib.load_zip(str(sample_zip), mode="rw", exp_positive=[".csv"])


def test_load_nest_with_filter(z_lib, tmp_path):
    """load_nest でフォルダ配下のZIP一括ロード時にもフィルターが正しく適用されることを確認"""
    folder = tmp_path / "nest"
    folder.mkdir()

    for i in range(2):
        zp = folder / f"pkg_{i}.zip"
        with zipfile.ZipFile(zp, "w") as zf:
            zf.writestr("table.csv", f"data_{i}")
            zf.writestr("ignore.bin", b"\x00\x01")

    z_lib.load_nest(str(folder), exp_positive=[".csv"])

    for i in range(2):
        zp_norm = normalize_path(str(folder / f"pkg_{i}.zip"))
        assert z_lib.os.path.exists(f"{zp_norm}/table.csv")
        assert not z_lib.os.path.exists(f"{zp_norm}/ignore.bin")


def test_swap_zip_filter_reload(z_lib, sample_zip):
    """swap_zip でフィルター条件を変更した場合に再ロードされることを確認"""
    # 最初は .csv のみ
    z_lib.swap_zip([str(sample_zip)], exp_positive=[".csv"])
    norm_zip = normalize_path(str(sample_zip))
    assert z_lib.os.path.exists(f"{norm_zip}/data.csv")
    assert not z_lib.os.path.exists(f"{norm_zip}/info.json")

    # 次に .json のみに変更
    z_lib.swap_zip([str(sample_zip)], exp_positive=[".json"])
    assert not z_lib.os.path.exists(f"{norm_zip}/data.csv")
    assert z_lib.os.path.exists(f"{norm_zip}/info.json")
