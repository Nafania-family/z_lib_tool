# Z_Lib

ZIP内のファイルを `dataset.zip/data/table.csv` のようなパスで扱うPythonライブラリです。Box Driveなどの同期フォルダにあるZIPをローカル作業領域へコピーし、展開後のファイルを読み書きします。

Python **3.12以上**が必要です。実行時の追加依存はありません。取得時間や速度改善の幅は、ZIPの内容・同期状態・ストレージによって変わります。

## インストール

リポジトリを取得したディレクトリで、既存のロックファイルを使って環境を作成します。

```console
uv sync --locked
```

別のプロジェクトからローカルのチェックアウトを利用する場合：

```console
uv add /path/to/z_lib_tool
```

## 読み取り

`mode="r"` が既定です。先にZIPをロードし、処理後は `close()` で作業領域を解放します。

```python
from z_lib import Z_Lib

z = Z_Lib()
try:
    z.load_zip("dataset.zip")
    with z.open("dataset.zip/meta.json", encoding="utf-8") as stream:
        print(stream.read())

    for root, dirs, files in z.os.walk("dataset.zip"):
        print(root, files)
finally:
    z.close()
```

通常のローカルパスも扱えます。`z.os` と `z.shutil` は標準ライブラリの一部の操作を提供しており、完全な置き換えではありません。

## 編集と新規作成

`edit()` は正常終了時に保存します。下の例は `result.zip` がなければ新規作成し、あればその内容を読み込んで編集します。

```python
from z_lib import Z_Lib

z = Z_Lib()
with z.edit("result.zip", create=True):
    z.os.makedirs("result.zip/output", exist_ok=True)
    with z.open("result.zip/output/result.txt", "w", encoding="utf-8") as stream:
        stream.write("処理結果\n")
```

保存を明示的に制御する場合は `load_zip(..., mode="rw")` と `commit()` を使います。**`close()` は未保存の変更を破棄します。** `unload_zip()`、`swap_zip()` の対象から外れたZIP、終了時の自動処理は、変更済みの編集セッションを保存しようとするため、終了時の処理に任せず明示的に保存・解放してください。

`edit()` の例外処理ではロールバックが実行されます。保存失敗時の編集データ保持には制約があります。[保存・復旧の注意点](docs/api.md#保存と復旧の制約)を参照してください。

## 拡張子を指定して展開

読み取り専用で、必要なファイルだけを展開できます。ZIP自体のコピーは全体に対して行います。

```python
from z_lib import Z_Lib

z = Z_Lib()
try:
    z.load_zip(
        "dataset.zip",
        exp_positive=[".csv", ".json"],
        exp_negative=[".json"],
    )
    for root, dirs, files in z.os.walk("dataset.zip"):
        print(root, files)  # この例ではCSVのみ
finally:
    z.close()
```

大文字小文字と先頭のドットの有無は正規化され、除外指定が優先されます。対象外ファイルと空ディレクトリは展開されません。ロード済みZIPのフィルターを変更すると、未保存変更を破棄して再ロードするため、先に保存・解放してください。

## ドキュメント

| 文書 | 内容 |
|---|---|
| [API・利用ガイド](docs/api.md) | 各操作、外部ライブラリ連携、保存・復旧の制約 |
| [設計書](設計書.md) | 現行の構成、データフロー、保証範囲 |
| [開発ガイド](docs/development.md) | 環境構築、テスト、性能計測 |
| [今後の改善](docs/roadmap.md) | 未実装事項と改善候補 |

## ライセンス

現時点でリポジトリに `LICENSE` ファイルはありません。利用許諾条件は管理者に確認してください。
