"""使い方:

    python -m tfastool parts 図面.tfs          部材の個数を一覧表示
    python -m tfastool parts 図面.tfs --csv    CSV で出力
    python -m tfastool list 図面.tfs           部材を 1 個ずつ座標つきで表示
    python -m tfastool place 入力.tfs 出力.tfs --sheet 2 --code E0A10S290008 --at 12000,9000
                                               部材を指定座標に追加（--at は複数指定可）
    python -m tfastool wire 入力.tfs 出力.tfs --sheet 2 --between 86564,86570
                                               2 つの部材（要素ID）を直線の配線で結ぶ（複数指定可）
    python -m tfastool info 図面.tfs           ファイル構造の概要を表示
    python -m tfastool rewrite 入力.tfs 出力.tfs [--level N]
                                               読み込んで書き直す（書き込み検証用）
"""
from __future__ import annotations

import argparse
import csv
import sys

from . import container, elements, parts, wiring


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


def cmd_list(args) -> int:
    tfs = container.load(args.file)
    names = parts.master_names(tfs)
    for number, sheet in enumerate(elements.sheets(tfs), 1):
        for element in elements.top_elements(sheet):
            if element.code and element.origin:
                x, y = element.origin
                print(f"シート{number}  ID={element.element_id}  {element.code}  X={x:9.1f}  Y={y:9.1f}  {names.get(element.code, '')}")
    return 0


def _point(text: str) -> tuple[float, float]:
    x, y = text.split(",")
    return float(x), float(y)


def cmd_place(args) -> int:
    tfs = container.load(args.input)
    all_sheets = elements.sheets(tfs)
    if not 1 <= args.sheet <= len(all_sheets):
        print(f"シート番号は 1〜{len(all_sheets)} で指定してください。", file=sys.stderr)
        return 1
    sheet = all_sheets[args.sheet - 1]
    try:
        for x, y in args.at:
            elements.place_part(tfs, sheet, args.code, x, y)
    except LookupError as error:
        print(error, file=sys.stderr)
        return 1
    container.save(tfs, args.output)
    print(f"{args.code} を {len(args.at)} 個追加して書き出しました: {args.output}")
    return 0


def _pair(text: str) -> tuple[int, int]:
    a, b = text.split(",")
    return int(a), int(b)


def cmd_wire(args) -> int:
    tfs = container.load(args.input)
    all_sheets = elements.sheets(tfs)
    if not 1 <= args.sheet <= len(all_sheets):
        print(f"シート番号は 1〜{len(all_sheets)} で指定してください。", file=sys.stderr)
        return 1
    try:
        for a, b in args.between:
            wiring.add_straight_wire(tfs, all_sheets[args.sheet - 1], a, b)
    except (LookupError, ValueError) as error:
        print(error, file=sys.stderr)
        return 1
    container.save(tfs, args.output)
    print(f"配線を {len(args.between)} 本追加して書き出しました: {args.output}")
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
    p = sub.add_parser("list"); p.add_argument("file"); p.set_defaults(func=cmd_list)
    p = sub.add_parser("place"); p.add_argument("input"); p.add_argument("output")
    p.add_argument("--sheet", type=int, required=True); p.add_argument("--code", required=True)
    p.add_argument("--at", type=_point, action="append", required=True, metavar="X,Y")
    p.set_defaults(func=cmd_place)
    p = sub.add_parser("wire"); p.add_argument("input"); p.add_argument("output")
    p.add_argument("--sheet", type=int, required=True)
    p.add_argument("--between", type=_pair, action="append", required=True, metavar="ID1,ID2")
    p.set_defaults(func=cmd_wire)
    p = sub.add_parser("info"); p.add_argument("file"); p.set_defaults(func=cmd_info)
    p = sub.add_parser("rewrite"); p.add_argument("input"); p.add_argument("output")
    p.add_argument("--level", type=int, default=6); p.set_defaults(func=cmd_rewrite)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
