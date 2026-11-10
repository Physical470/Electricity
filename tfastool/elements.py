"""シート内の図形要素（部材・線・文字など）の走査と複製。

解析で分かった構造（非公式）:

* シート（圧縮ブロック）の中身は、要素開始(種別 2)〜要素終了(種別 5) の入れ子。
* 種別 2 の本体は ``[u32 要素ID] [u32 フラグ]``。要素ID は図面全体で一意。
* 種別 4 は属性。先頭 4 バイトが属性の種類:
  ``00 00 00 00`` 部材コード、``80 01 00 00`` 部材名称、
  ``96 01 01 00`` 部材ごとの固有識別子(16 バイト)、
  ``01 01 00 00`` 配線など他要素へのリンク。
* 種別 3 は図形。位置 10 の u32 の下位 1 バイトが図形の種類、位置 26 から座標(倍精度)。
* 図形セクション先頭の種別 1 レコードは ``[u32 要素総数] [u32 次に使う要素ID]``。
"""
from __future__ import annotations

import os
import struct
from dataclasses import dataclass

from .container import Record, TfsFile
from .parts import DRAWING_SECTION, KIND_ATTRIBUTE, _CODE

KIND_BEGIN = 0x0002
KIND_GEOMETRY = 0x0003
KIND_END = 0x0005
KIND_COUNTERS = 0x0001

ATTR_LINK = b"\x01\x01\x00\x00"
ATTR_GUID = b"\x96\x01\x01\x00"

GEOMETRY_DATA_OFFSET = 26
GEOMETRY_TRAILER = 5


@dataclass
class Element:
    """シート直下の要素 1 個。records[start:end+1] がその範囲。"""
    start: int
    end: int
    element_id: int
    #: 部材の場合の部材コード（部材でなければ None）
    code: str | None
    #: 配置基準点（部材の場合のみ）
    origin: tuple[float, float] | None


def sheets(tfs: TfsFile) -> list[Record]:
    """シートに相当する圧縮ブロックを順に返す。"""
    section = tfs.section(DRAWING_SECTION)
    return [r for r in section.records if r.children is not None] if section else []


def top_elements(sheet: Record) -> list[Element]:
    records = sheet.children
    result = []
    depth = 0
    start = 0
    for i, record in enumerate(records):
        if record.kind == KIND_BEGIN:
            if depth == 0:
                start = i
            depth += 1
        elif record.kind == KIND_END:
            depth -= 1
            if depth == 0:
                result.append(_describe(records, start, i))
    return result


def _describe(records: list[Record], start: int, end: int) -> Element:
    code = None
    origin = None
    for record in records[start + 1 : end]:
        if record.kind == KIND_BEGIN:
            break  # 子要素に入ったら終わり
        if record.kind == KIND_ATTRIBUTE and code is None:
            match = _CODE.search(record.body)
            if match:
                code = match.group().decode("ascii")
        if record.kind == KIND_GEOMETRY and origin is None and _shape(record) == 0x39:
            origin = struct.unpack_from("<2d", record.body, GEOMETRY_DATA_OFFSET + 8)
    element_id = struct.unpack_from("<I", records[start].body)[0]
    return Element(start, end, element_id, code, origin if code else None)


def _shape(record: Record) -> int:
    return record.body[10]


def translate_geometry(body: bytes, dx: float, dy: float) -> bytes:
    """図形レコード 1 件を (dx, dy) だけ平行移動した本体を返す。"""
    shape = body[10]
    data = bytearray(body[GEOMETRY_DATA_OFFSET : len(body) - GEOMETRY_TRAILER])

    def shift(offset: int, delta: float) -> None:
        value = struct.unpack_from("<d", data, offset)[0]
        struct.pack_into("<d", data, offset, value + delta)

    if shape in (0x38, 0x39, 0x68):  # 多角形 / 点列（2 次元） / 点列（3 次元）
        count = struct.unpack_from("<I", data, 0)[0]
        stride = 24 if shape == 0x68 else 16
        if len(data) < 8 + count * stride:  # 点列の後ろに追加データが付くことがある
            raise ValueError("点列の長さが想定より短いです")
        for i in range(count):
            shift(8 + i * stride, dx)
            shift(8 + i * stride + 8, dy)
    elif shape == 0x40 and len(data) == 24:  # 円: 中心 x, y, 半径
        shift(0, dx); shift(8, dy)
    elif shape == 0x20 and len(data) == 24:  # 垂直線: x, y1, y2
        shift(0, dx); shift(8, dy); shift(16, dy)
    elif shape == 0x18 and len(data) == 24:  # 水平線: y, x1, x2
        shift(0, dy); shift(8, dx); shift(16, dx)
    elif shape == 0x48 and len(data) == 56:  # 円弧: 中心 x, y, 半径, 始点 x, y, 終点 x, y
        shift(0, dx); shift(8, dy); shift(24, dx); shift(32, dy); shift(40, dx); shift(48, dy)
    elif shape == 0x10 and len(data) == 32:  # 線分: x1, y1, x2, y2
        shift(0, dx); shift(8, dy); shift(16, dx); shift(24, dy)
    else:
        raise NotImplementedError(f"未対応の図形種別です: {shape:#x} (データ長 {len(data)})")
    return body[:GEOMETRY_DATA_OFFSET] + bytes(data) + body[len(body) - GEOMETRY_TRAILER :]


def _counters(tfs: TfsFile) -> Record:
    for record in tfs.section(DRAWING_SECTION).records:
        if record.kind == KIND_COUNTERS and len(record.body) == 8:
            return record
    raise ValueError("要素数カウンタが見つかりません")


def duplicate_element(tfs: TfsFile, sheet: Record, element: Element,
                      dx: float, dy: float) -> Element:
    """要素を複製し、(dx, dy) ずらして元の直後に挿入する。

    複製には新しい要素ID と固有識別子を割り当てる。他要素（配線など）への
    リンクは引き継がない（複製は未結線の状態になる）。
    """
    counters = _counters(tfs)
    count, next_id = struct.unpack("<II", counters.body)
    records = sheet.children
    copies = []
    for record in records[element.start : element.end + 1]:
        body = record.body
        if record.kind == KIND_BEGIN:
            body = struct.pack("<I", next_id) + body[4:]
            next_id += 1
            count += 1
        elif record.kind == KIND_ATTRIBUTE:
            if body.startswith(ATTR_LINK):
                continue
            if body.startswith(ATTR_GUID):
                body = body[:4] + os.urandom(len(body) - 4)
        elif record.kind == KIND_GEOMETRY:
            body = translate_geometry(body, dx, dy)
        copies.append(Record(record.kind, record.section, body))
    records[element.end + 1 : element.end + 1] = copies
    counters.body = struct.pack("<II", count, next_id)
    return _describe(records, element.end + 1, element.end + len(copies))


def place_part(tfs: TfsFile, sheet: Record, code: str, x: float, y: float) -> Element:
    """部材コード code の部材を、配置基準点が (x, y) になるように 1 個追加する。

    同じシートにすでに配置されている同じ部材をひな形として複製するため、
    その部材が 1 個も無いシートには追加できない。向きはひな形と同じになる。
    """
    for element in top_elements(sheet):
        if element.code == code and element.origin is not None:
            return duplicate_element(tfs, sheet, element,
                                     x - element.origin[0], y - element.origin[1])
    raise LookupError(f"ひな形にできる部材 {code} がこのシートにありません")
