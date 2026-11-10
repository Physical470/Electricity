"""配線要素の追加。

解析で分かった配線要素の構造（非公式）:

* 要素フラグ ``0x00000400``。属性 ``01 01 00 00`` に接続先の
  ``(部材の要素ID, 01 00 00 04=始点 / 02 00 00 04=終点)`` が並ぶ。
* 部材側にも同じ種類の属性があり ``(配線の要素ID, [接続の通し番号] 01 00 04)`` が並ぶ。
* 属性 ``0c 04`` に始点・終点それぞれの配線の出る向き（度）が入る。
* 最初の図形（点列）が経路。部材の配置基準点どうしを結ぶ。
* 子要素 1: 実際に描かれる線。全体の線 1 本と、器具記号の内側に隠れる部分・
  見える部分に分割した線が並ぶ。
* 子要素 2: 条数記号（斜めの短線）。

現在は、水平または垂直の直線 1 本で 2 つの部材を結ぶ配線だけを追加できる。
"""
from __future__ import annotations

import struct

from .container import Record, TfsFile
from .elements import (ATTR_LINK, GEOMETRY_DATA_OFFSET, KIND_BEGIN, KIND_END,
                       KIND_GEOMETRY, Element, _counters, top_elements)
from .parts import KIND_ATTRIBUTE

WIRE_FLAGS = 0x00000400
END_START = b"\x01\x00\x00\x04"
END_FINISH = b"\x02\x00\x00\x04"
ATTR_ENDS = b"\x0c\x04\x00\x00"
SHAPE_POINTS, SHAPE_LINE, SHAPE_HORIZONTAL, SHAPE_VERTICAL, SHAPE_CIRCLE = 0x39, 0x10, 0x18, 0x20, 0x40
TOLERANCE = 0.01


def _flags(record: Record) -> int:
    return struct.unpack_from("<I", record.body, 4)[0]


def _doubles(record: Record, count: int, skip: int = 0) -> tuple[float, ...]:
    return struct.unpack_from(f"<{count}d", record.body, GEOMETRY_DATA_OFFSET + skip)


def _with_doubles(record: Record, values, skip: int = 0, shape: int | None = None) -> Record:
    body = bytearray(record.body)
    struct.pack_into(f"<{len(values)}d", body, GEOMETRY_DATA_OFFSET + skip, *values)
    if shape is not None:
        body[10] = shape
    return Record(record.kind, record.section, bytes(body))


def _is_template(records: list[Record]) -> bool:
    """垂直の直線 1 本で 2 部材を結び、条数記号を 1 組だけ持つ素直な配線か。"""
    signature = []
    for record in records:
        if record.kind == KIND_GEOMETRY:
            signature.append(f"g{record.body[10]:x}")
        elif record.kind == KIND_ATTRIBUTE:
            signature.append("a")
        else:
            signature.append("(" if record.kind == KIND_BEGIN else ")")
    if signature != list("(aaaaa") + ["g39", "(", "(", "a", "g20", "g20", "g20", "g20", ")", ")",
                                      "(", "a", "a", "a", "g39", "g10", "g10", ")", ")"]:
        return False
    if not (records[1].body.startswith(ATTR_LINK) and len(records[1].body) == 20):
        return False
    if not (records[5].body.startswith(ATTR_ENDS) and len(records[5].body) == 84):
        return False
    route = records[6]
    return (struct.unpack_from("<I", route.body, GEOMETRY_DATA_OFFSET)[0] == 2
            and len(route.body) == GEOMETRY_DATA_OFFSET + 8 + 32 + 5)


def find_template(sheet: Record) -> list[Record]:
    records = sheet.children
    for element in top_elements(sheet):
        if _flags(records[element.start]) != WIRE_FLAGS:
            continue
        candidate = records[element.start : element.end + 1]
        if _is_template(candidate):
            return candidate
    raise LookupError("ひな形にできる配線（垂直の直線で 2 部材を結ぶもの）がこのシートにありません")


def _symbol_radius(records: list[Record], element: Element, fallback: float) -> float:
    """器具記号の外形円の半径（配線が隠れる長さ）。円が無ければ fallback。"""
    radius = 0.0
    for record in records[element.start : element.end + 1]:
        if record.kind == KIND_GEOMETRY and record.body[10] == SHAPE_CIRCLE and len(record.body) == 55:
            radius = max(radius, _doubles(record, 3)[2])
    return radius or fallback


def _add_part_link(records: list[Record], element: Element, wire_id: int) -> int:
    """部材に配線へのリンクを追記する。レコードを 1 件挿入した場合は 1 を返す。"""
    for i in range(element.start + 1, element.end):
        record = records[i]
        if record.kind == KIND_BEGIN:
            break
        if record.kind == KIND_ATTRIBUTE and record.body.startswith(ATTR_LINK):
            ordinal = (len(record.body) - 4) // 8 + 1
            record.body += struct.pack("<IBBBB", wire_id, ordinal, 1, 0, 4)
            return 0
    body = ATTR_LINK + struct.pack("<IBBBB", wire_id, 1, 1, 0, 4)
    records.insert(element.start + 1, Record(KIND_ATTRIBUTE, records[element.start].section, body))
    return 1


def add_straight_wire(tfs: TfsFile, sheet: Record, start_id: int, end_id: int) -> int:
    """要素ID start_id の部材から end_id の部材へ、直線の配線を 1 本追加する。

    2 つの部材は X か Y のどちらかが同じ位置（水平または垂直に並ぶ）でなければならない。
    線種・レイヤ・条数はシート内の既存の配線（ひな形）と同じになる。追加した配線の要素ID を返す。
    """
    template = find_template(sheet)
    records = sheet.children
    by_id = {e.element_id: e for e in top_elements(sheet)}
    try:
        a, b = by_id[start_id], by_id[end_id]
    except KeyError as error:
        raise LookupError(f"要素ID {error.args[0]} がこのシートにありません") from None
    if a.origin is None or b.origin is None:
        raise ValueError("配線の両端は部材でなければなりません")
    (x0, y0), (x1, y1) = a.origin, b.origin
    vertical = abs(x0 - x1) <= TOLERANCE and abs(y0 - y1) > TOLERANCE
    horizontal = abs(y0 - y1) <= TOLERANCE and abs(x0 - x1) > TOLERANCE
    if not (vertical or horizontal):
        raise ValueError("2 つの部材が水平または垂直に並んでいません")

    # ひな形の寸法
    t_hidden = abs(_doubles(template[11], 3)[2] - _doubles(template[11], 3)[1])
    t_tick = _doubles(template[20], 4, skip=8)
    radius_a = _symbol_radius(records, a, t_hidden)
    radius_b = _symbol_radius(records, b, t_hidden)

    if vertical:
        fixed, s0, s1, shape = x0, y0, y1, SHAPE_VERTICAL
    else:
        fixed, s0, s1, shape = y0, x0, x1, SHAPE_HORIZONTAL
    sign = 1.0 if s1 > s0 else -1.0
    if abs(s1 - s0) <= radius_a + radius_b:
        raise ValueError("2 つの部材が近すぎて配線を描けません")
    angle = (90.0 if sign > 0 else 270.0) if vertical else (0.0 if sign > 0 else 180.0)
    middle = (s0 + s1) / 2

    def point(along: float, across: float = 0.0) -> tuple[float, float]:
        """配線方向の位置 along、直交方向のずれ across から図面座標を得る。"""
        return (fixed + across, along) if vertical else (along, fixed + across)

    counters = _counters(tfs)
    count, next_id = struct.unpack("<II", counters.body)
    wire_id = next_id
    pieces = [(s0, s1), (s0, s0 + sign * radius_a),
              (s0 + sign * radius_a, s1 - sign * radius_b), (s1 - sign * radius_b, s1)]
    piece_index = 0
    stroke_index = 0
    new = []
    for i, record in enumerate(template):
        body = record.body
        if record.kind == KIND_BEGIN:
            body = struct.pack("<I", next_id) + body[4:]
            next_id += 1
            count += 1
            record = Record(record.kind, record.section, body)
        elif i == 1:
            record = Record(record.kind, record.section,
                            ATTR_LINK + struct.pack("<I", start_id) + END_START
                            + struct.pack("<I", end_id) + END_FINISH)
        elif i == 5:
            patched = bytearray(body)
            struct.pack_into("<d", patched, 36, angle)
            struct.pack_into("<d", patched, 76, (angle + 180.0) % 360.0)
            record = Record(record.kind, record.section, bytes(patched))
        elif i == 6:
            record = _with_doubles(record, (x0, y0, x1, y1), skip=8)
        elif record.kind == KIND_GEOMETRY and record.body[10] == SHAPE_VERTICAL:
            record = _with_doubles(record, (fixed, *pieces[piece_index]), shape=shape)
            piece_index += 1
        elif i == 20:
            px, py = point(middle)
            record = _with_doubles(record, (px, py, px + (0 if vertical else 1), py + (1 if vertical else 0)), skip=8)
        elif record.kind == KIND_GEOMETRY and record.body[10] == SHAPE_LINE:
            # ひな形（垂直）の条数記号の短線を、記号位置からの相対値で写す
            sx0, sy0, sx1, sy1 = _doubles(record, 4)
            rel = [(sx0 - t_tick[0], sy0 - t_tick[1]), (sx1 - t_tick[0], sy1 - t_tick[1])]
            if not vertical:
                rel = [(ry, -rx) for rx, ry in rel]  # 水平の配線では 90 度回す
            px, py = point(middle)
            record = _with_doubles(record, (px + rel[0][0], py + rel[0][1], px + rel[1][0], py + rel[1][1]))
            stroke_index += 1
        else:
            record = Record(record.kind, record.section, body)
        new.append(record)
    assert piece_index == 4 and stroke_index == 2

    records.extend(new)
    # 挿入で位置がずれないよう、後ろにある部材から先にリンクを追記する
    for element in sorted((a, b), key=lambda e: e.start, reverse=True):
        _add_part_link(records, element, wire_id)
    counters.body = struct.pack("<II", count, next_id)
    return wire_id
