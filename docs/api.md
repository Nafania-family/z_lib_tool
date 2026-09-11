# API・利用ガイド

[READMEへ戻る](../README.md)

## 初期化とパス

`Z_Lib(workspace_dir=None, verbose=False, on_progress=None)` でインスタンスを作ります。`workspace_dir` は作業領域の親フォルダで、省略時はOSの一時領域を使います。指定先は必要に応じて作成されます。

`on_progress` は `(phase: str, progress: float, detail: str)` を受け取るコールバックです。進捗値は処理段階ごとの目安で、転送バイト数から計算した割合ではありません。`verbose=True` で操作ログを出力します。

ZIP内パスは `archive.zip/folder/file.txt` と記述します。ロード済みZIPのルートは展開先ディレクトリとして解決されます。ZIPを含まないパスは通常のローカルファイルとして扱い、ZIP内部へのアクセスには事前のロードが必要です。

## ロード・解放

| API | 動作 |
|---|---|
| `load_zip(*paths, create=False, mode="r", exp_positive=None, exp_negative=None)` | ZIPをコピー・展開。`create=True` では既定モードも `rw` になる。既存ZIPを空にする指定ではない |
| `load_nest(folder, create=False, mode="r", exp_positive=None, exp_negative=None)` | ローカルフォルダを `rglob("*.zip")` で探索してロード。ZIP内ZIPの再帰展開ではない |
| `swap_zip(target_zips, create=False, mode="r", exp_positive=None, exp_negative=None)` | 対象外のセッションを保存付きで閉じ、対象リストに同期する |
| `unload_zip(*paths, save=True)` | 指定ZIPを閉じる。`rw` かつ変更済みなら既定で保存。引数なしでは対象なし |
| `close(*paths)` | 保存せず閉じる。引数なしでは全セッション |
| `get_status(path)` | `path`, `mode`, `status`, `is_dirty`, `temp_dir` の辞書。未ロードは `None` |

同じZIPと同じフィルターでの再ロードはスキップされ、指定したモードへの切り替えも行われません。モード変更は明示的に保存・解放してからロードしてください。フィルターの比較はリストそのものの比較なので、並び順や表記だけを変えても再ロードされる場合があります。

## 拡張子フィルター

`exp_positive` は許可リスト、`exp_negative` は除外リストです。両方指定すると許可されたものから除外対象を取り除きます。`None` と空リストは制限なしです。

拡張子は前後の空白を除去し、小文字と先頭ドット付きに正規化します。比較対象は最後の拡張子です。たとえば `data.tar.gz` は `.gz` に一致します。ワイルドカードやファイル名パターンは使えません。

空でないフィルターと `mode="rw"` の組み合わせは `ValueError` になります。フィルター指定時は対象ファイルの親フォルダだけが作られ、空ディレクトリは保持されません。

## ファイル操作

| 名前空間 | 対応する操作 |
|---|---|
| `z` | `open(path, mode="r", **kwargs)`, `resolve(path)` |
| `z.os` | `listdir`, `mkdir`, `makedirs`, `remove`, `rmdir`, `rename`, `walk` |
| `z.os.path` | `exists`, `isfile`, `isdir`, `join`, `basename`, `dirname`, `splitext`, `getsize` |
| `z.shutil` | `copy2`, `move`, `copytree`, `rmtree` |

`open()` の追加引数は組み込みの `open()` に渡されます。書き込み先の親ディレクトリは自動作成しません。読み取り専用ZIPへのAPI経由の変更は `ZipReadOnlyError` になります。通常のローカルファイルへの変更はこの制限の対象外です。

`walk()` はロード済みZIPをディレクトリとして走査し、ZIP内部の `root` は正規化した絶対仮想パスを返します。未ロードZIPは通常のファイルです。ZIP内部では `topdown=True` の `dirs[:]` 編集による枝刈りができますが、ローカル側の走査では同じ枝刈り動作にはなっていません。`copy2`, `move`, `copytree` の戻り値は展開先を含む実パス文字列です。

`exists`, `isfile`, `isdir` は解決時の例外も含めて `False` を返すため、エラー原因の確認には `resolve()` を使ってください。

## 外部ライブラリとの連携

`resolve()` は実ファイルの `pathlib.Path` を返します。読み取り例：

```python
from z_lib import Z_Lib

z = Z_Lib()
try:
    z.load_zip("dataset.zip")
    real_path = z.resolve("dataset.zip/table.csv")
    print(real_path.read_text(encoding="utf-8"))
finally:
    z.close()
```

Polarsなどへもこのパスを渡せます。外部ライブラリは利用側で別途導入してください。実パスはセッションの解放・再ロード後には使えません。ロールバックで内容が再作成されるため、開いたファイルを閉じてから操作してください。

実パス経由の書き込みは読み取り専用ガードと変更検出を通りません。外部ライブラリで編集する場合は、編集セッションを明示的に変更済みとします。

```python
from z_lib import Z_Lib

z = Z_Lib()
with z.edit("dataset.zip") as session:
    session.mark_dirty()
    z.resolve("dataset.zip/result.txt").write_text("結果", encoding="utf-8")
```

## 保存と復旧の制約

`commit(*paths)` は指定ZIPを保存します。引数なしでは全セッションが対象で、読み取り専用セッションが含まれると例外になります。複数ZIPの保存は順次処理で、一括トランザクションではありません。既存ZIPで変更フラグがない場合は保存をスキップします。

`rollback(*paths)` は変更フラグがあるセッションを**ロード時のコピー**に戻します。コミット後もコピーは更新されないため、次の編集でロールバックすると直前のコミット時点ではなくロード時点に戻ります。編集単位ごとに保存・解放・再ロードしてください。

`edit(path, create=False)` は正常終了時にコミットし、例外時にロールバックします。自身でロードしたセッションは終了時に閉じます。既存セッションは開いたままにしますが、読み取り専用の場合は編集できません。

現行の `edit()` は保存失敗もロールバック対象にするため、`ZipSaveError.recovery_path` の編集内容がそのまま残るとは限りません。復旧が必要な編集は明示的な `commit()` を使い、例外時にセッションを閉じたりロールバックしたりする前に、作業領域を別の場所へコピーしてください。`ZipConflictError` 時には `get_status(path)["temp_dir"]` で展開先を確認できます。

競合検出は元ZIPの存在・サイズ・更新時刻（1ミリ秒超の差）による比較です。圧縮前に一度確認するため、その後の更新や同一メタデータの変更を検出できません。ファイルロックや別端末の同時編集防止は提供していません。保存完了は同期フォルダ上の置換完了であり、クラウド同期完了ではありません。

## 例外

各例外は `from z_lib import ...` で取得できます。

| 例外 | 主な発生条件 |
|---|---|
| `ZipNotLoadedError` | 未ロードZIPを含むパスの解決 |
| `ZipReadOnlyError` | 読み取り専用ZIPへの変更・コミット |
| `ZipConflictError` | 元ZIPの外部作成・削除・更新の検出 |
| `ZipSaveError` | 再圧縮・検証・置換の失敗。`recovery_path` は復旧候補の実パス |
| `ZipPathError` | セッションの展開失敗。元の例外は `__cause__` に保持 |
| `ZipSecurityError` | 不正な内部パスなど。ロード中の展開エラーでは `ZipPathError` に包まれる |

存在しないZIPでは `FileNotFoundError`、通常のファイル操作では標準の例外も発生します。`ZipAlreadyLoadedError` は公開されていますが、現行の `load_zip()` は重複ロード時に送出しません。
