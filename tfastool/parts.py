"""図面に配置されている Tfas 部材（機器・器具）の拾い出し。"""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

from .container import TfsFile

#: 部材定義が入っているセクション番号と、図形データが入っているセクション番号
MASTER_SECTION = 4
DRAWING_SECTION = 5
#: 部材定義レコード / 図形要素の属性レコード
KIND_MASTER = 0x0009
KIND_ATTRIBUTE = 0x0004

_CODE = re.compile(rb"[A-Z][0-9][0-9A-Z]{3}S[0-9]{6}")


def _cstring(data: bytes) -> str:
    return data.split(b"\0", 1)[0].decode("cp932", "replace")


def master_names(tfs: TfsFile) -> dict[str, str]:
    """部材コード -> 部材名称。"""
    names: dict[str, str] = {}
    section = tfs.section(MASTER_SECTION)
    if section is None:
        return names
    for top in section.records:
        for record in top.children or []:
            if record.kind == KIND_MASTER and _CODE.match(record.body):
                names[_cstring(record.body[:24])] = _cstring(record.body[24:])
    return names


@dataclass
class SheetParts:
    #: ファイル内での並び順（1 始まり）。Tfas 上のシート名との対応は未確認
    sheet: int
    counts: Counter


def count_parts(tfs: TfsFile) -> list[SheetParts]:
    """圧縮ブロック（シートに相当すると推定）ごとに部材コードの出現数を数える。"""
    result = []
    section = tfs.section(DRAWING_SECTION)
    if section is None:
        return result
    sheet = 0
    for top in section.records:
        if top.children is None:
            continue
        sheet += 1
        counts: Counter = Counter()
        for record in top.children:
            if record.kind != KIND_ATTRIBUTE:
                continue
            match = _CODE.search(record.body)
            if match:
                counts[match.group().decode("ascii")] += 1
        result.append(SheetParts(sheet, counts))
    return result
