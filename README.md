# Electricity

電気設備工事の図面作成（CADWe'll Tfas 14 E）を自動化するためのツール群です。

## tfastool

Tfas 図面ファイル（`.tfs`）を Python から直接読み書きする実験的なツールです。
ファイル形式は公開されていないため、実ファイルの解析結果に基づいています。
追加ライブラリは不要です（Python 3.10 以上）。

```bash
python -m tfastool parts 図面.tfs
```

図面に配置されている Tfas 部材（部材コード・名称・個数）を一覧表示します。`--csv` で CSV 出力になります。

```bash
python -m tfastool info 図面.tfs
```

ファイル内部の構造の概要を表示します。

```bash
python -m tfastool rewrite 入力.tfs 出力.tfs
```

読み込んだ内容をそのまま書き直します（書き込み処理の検証用）。

### 現状

- 読み込み：ファイル全体を解析でき、部材コードと個数を取り出せます。
- 書き込み：内容を変えずに書き直すと元ファイルと完全に一致します。
  内容を変更したファイルを Tfas が正常に開けるかは検証中です。
- ヘッダ内の 20 バイト（位置 0x40）の用途は不明です。

### テスト

```bash
python -m unittest discover -s tests
```

## 注意

実案件の図面ファイルはこのリポジトリにコミットしないでください（`.gitignore` で除外しています）。
