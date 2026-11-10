"""使い方:

    python -m tfastool parts 図面.tfs          部材の個数を一覧表示
    python -m tfastool parts 図面.tfs --csv    CSV で出力
    python -m tfastool info 図面.tfs           ファイル構造の概要を表示
    python -m tfastool rewrite 入力.tfs 出力.tfs [--level N]
                                               読み込んで書き直す（書き込み検証用）
"""
from __future__ import annotations

import argparse
import csv
import sys

from . import container, parts


def cmd_parts(args) -> int:
    tfs = container.load(args.file)
    names = parts.master_names(tfs)
    rows = [
        (sheet.sheet, code, names.get(code, ""), count)
        for sheet in parts.count_parts(tfs)
        for code, count in sorted(sheet.counts.items())
    ]
    if args.csv:
        writer = csv.writer(sys.stdout, lineterminator="\n")
        writer.writerow(["シート順", "部材コード", "部材名称", "個数"])
        writer.writerows(rows)
    elif not rows:
        print("部材は見つかりませんでした。")
    else:
        for sheet, code, name, count in rows:
            print(f"シート{sheet}  {code}  {count:4d}  {name}")
    return 0


def cmd_info(args) -> int:
    tfs = container.load(args.file)
    for section in tfs.sections:
        if section is None or not section.records:
            continue
        print(f"セクション {section.index + 1}: レコード {len(section.records)} 件")
        for record in section.records:
            if record.children is not None:
                print(f"  圧縮ブロック: 内部レコード {len(record.children)} 件")
    return 0


def cmd_rewrite(args) -> int:
    tfs = container.load(args.input)
    container.save(tfs, args.output, level=args.level)
    with open(args.input, "rb") as a, open(args.output, "rb") as b:
        same = a.read() == b.read()
    print("書き出しました:", args.output, "（入力と完全一致）" if same else "（バイト列は入力と異なります）")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="tfastool", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("parts"); p.add_argument("file"); p.add_argument("--csv", action="store_true")
    p.set_defaults(func=cmd_parts)
    p = sub.add_parser("info"); p.add_argument("file"); p.set_defaults(func=cmd_info)
    p = sub.add_parser("rewrite"); p.add_argument("input"); p.add_argument("output")
    p.add_argument("--level", type=int, default=6); p.set_defaults(func=cmd_rewrite)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
