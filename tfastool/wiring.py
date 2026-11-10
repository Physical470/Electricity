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

* 経路が折れ曲がる場合、角は円弧（始点→終点が反時計回り）で丸められ、
  最初と最後の区間だけがグループ（子要素）になり、中間の区間は線 1 本になる。

水平・垂直の区間だけでできた経路の配線を追加できる。
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
SHAPE_POINTS, SHAPE_LINE, SHAPE_HORIZONTAL, SHAPE_VERTICAL, SHAPE_CIRCLE, SHAPE_ARC = 0x39, 0x10, 0x18, 0x20, 0x40, 0x48
ATTR_WEIGHTS = b"\x00\x04\x00\x00"
GEOMETRY_OVERHEAD = 15  # 図形レコード内の長さ欄に含まれないバイト数
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


def _geometry(template: Record, shape: int, payload: bytes) -> Record:
    """template のレイヤ・線種などを引き継いだ、別の形状の図形レコードを作る。"""
    body = bytearray(template.body[:GEOMETRY_DATA_OFFSET] + payload + template.body[-5:])
    body[10] = shape
    struct.pack_into("<I", body, 1, len(body) - GEOMETRY_OVERHEAD)
    struct.pack_into("<I", body, 6, len(body) - GEOMETRY_OVERHEAD)
    return Record(template.kind, template.section, bytes(body))


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
    if not (records[2].body.startswith(ATTR_WEIGHTS) and len(records[2].body) == 20):
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


def add_wire(tfs: TfsFile, sheet: Record, start_id: int, end_id: int,
             via: list[tuple[float, float]] | tuple = ()) -> int:
    """要素ID start_id の部材から end_id の部材へ配線を 1 本追加する。

    via は途中の折れ点。経路（始点の部材 → via → 終点の部材）の各区間は
    水平か垂直でなければならない。レイヤ・線種・条数はシート内の既存の配線
    （ひな形）と同じになる。追加した配線の要素ID を返す。
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
    points = [tuple(a.origin), *[(float(x), float(y)) for x, y in via], tuple(b.origin)]

    # ひな形から寸法とレコードの型を取り出す
    group_begin, group_attr, full_line, hidden_line, visible_line = template[8:13]
    hidden_values = _doubles(hidden_line, 3)
    t_tick = _doubles(template[20], 4, skip=8)
    strokes = [_doubles(r, 4) for r in template[21:23]]
    corner = abs(strokes[1][1] - strokes[0][1])  # 条数記号の間隔 = 角の丸め半径（紙面 1mm）
    fallback = abs(hidden_values[2] - hidden_values[1])
    radius_a = _symbol_radius(records, a, fallback)
    radius_b = _symbol_radius(records, b, fallback)

    # 区間ごとの向きと、両端で切り詰める長さ
    segments = []
    last = len(points) - 2
    for i, ((x0, y0), (x1, y1)) in enumerate(zip(points, points[1:])):
        vertical = abs(x0 - x1) <= TOLERANCE and abs(y0 - y1) > TOLERANCE
        horizontal = abs(y0 - y1) <= TOLERANCE and abs(x0 - x1) > TOLERANCE
        if not (vertical or horizontal):
            raise ValueError("経路に水平でも垂直でもない区間があります（折れ点を指定してください）")
        fixed, s0, s1 = (x0, y0, y1) if vertical else (y0, x0, x1)
        sign = 1.0 if s1 > s0 else -1.0
        trim0 = radius_a if i == 0 else corner
        trim1 = radius_b if i == last else corner
        if abs(s1 - s0) <= trim0 + trim1:
            raise ValueError("経路の区間が短すぎて配線を描けません")
        unit = (0.0, sign) if vertical else (sign, 0.0)
        segments.append((vertical, fixed, s0, s1, sign, unit))
    for before, after in zip(segments, segments[1:]):
        if before[0] == after[0]:
            raise ValueError("折れ点の前後で向きが変わっていません")

    counters = _counters(tfs)
    count, next_id = struct.unpack("<II", counters.body)
    wire_id = next_id

    def begin(template_record: Record) -> Record:
        nonlocal count, next_id
        record = Record(template_record.kind, template_record.section,
                        struct.pack("<I", next_id) + template_record.body[4:])
        next_id += 1
        count += 1
        return record

    def copy(template_record: Record) -> Record:
        return Record(template_record.kind, template_record.section, template_record.body)

    def line(template_record: Record, segment, start: float, end: float) -> Record:
        shape = SHAPE_VERTICAL if segment[0] else SHAPE_HORIZONTAL
        return _geometry(template_record, shape, struct.pack("<3d", segment[1], start, end))

    def direction(unit) -> float:
        return {(1.0, 0.0): 0.0, (0.0, 1.0): 90.0, (-1.0, 0.0): 180.0, (0.0, -1.0): 270.0}[unit]

    ends = bytearray(template[5].body)
    struct.pack_into("<d", ends, 36, direction(segments[0][5]))
    struct.pack_into("<d", ends, 76, (direction(segments[-1][5]) + 180.0) % 360.0)
    flat = [value for point in points for value in point]

    new = [
        begin(template[0]),
        Record(template[1].kind, template[1].section,
               ATTR_LINK + struct.pack("<I", start_id) + END_START
               + struct.pack("<I", end_id) + END_FINISH),
        Record(template[2].kind, template[2].section,
               ATTR_WEIGHTS + struct.pack(f"<{len(points)}d", *[1.0] * len(points))),
        copy(template[3]), copy(template[4]),
        Record(template[5].kind, template[5].section, bytes(ends)),
        _geometry(template[6], SHAPE_POINTS,
                  struct.pack(f"<II{len(flat)}d", len(points), 1, *flat)),
        begin(template[7]),
    ]
    for i, segment in enumerate(segments):
        _, _, s0, s1, sign, unit = segment
        if i > 0:  # 角の円弧
            previous = segments[i - 1][5]
            cx, cy = points[i]
            tangent_in = (cx - corner * previous[0], cy - corner * previous[1])
            tangent_out = (cx + corner * unit[0], cy + corner * unit[1])
            center = (tangent_in[0] + corner * unit[0], tangent_in[1] + corner * unit[1])
            left_turn = previous[0] * unit[1] - previous[1] * unit[0] > 0
            first, second = (tangent_in, tangent_out) if left_turn else (tangent_out, tangent_in)
            new.append(_geometry(visible_line, SHAPE_ARC,
                                 struct.pack("<7d", *center, corner, *first, *second)))
        start = s0 + sign * (0.0 if i == 0 else corner)
        end = s1 - sign * (0.0 if i == last else corner)
        if 0 < i < last:
            new.append(line(visible_line, segment, start, end))
            continue
        new += [begin(group_begin), copy(group_attr), line(full_line, segment, start, end)]
        if i == 0:
            inner = start + sign * radius_a
            new.append(line(hidden_line, segment, start, inner))
            start = inner
        if i == last:
            inner = end - sign * radius_b
            new += [line(visible_line, segment, start, inner), line(hidden_line, segment, inner, end)]
        else:
            new.append(line(visible_line, segment, start, end))
        new.append(copy(template[14]))
    new.append(copy(template[15]))

    # 条数記号は最も長い区間の中央に置く
    vertical, fixed, s0, s1, _, _ = max(segments, key=lambda s: abs(s[3] - s[2]))
    middle = (s0 + s1) / 2
    px, py = (fixed, middle) if vertical else (middle, fixed)
    new += [begin(template[16]), copy(template[17]), copy(template[18]), copy(template[19]),
            _geometry(template[20], SHAPE_POINTS,
                      struct.pack("<II4d", 2, 1, px, py,
                                  px + (0 if vertical else 1), py + (1 if vertical else 0)))]
    for record, (sx0, sy0, sx1, sy1) in zip(template[21:23], strokes):
        # ひな形（垂直）の短線を記号位置からの相対値で写す。水平の区間では 90 度回す
        rel = [(sx0 - t_tick[0], sy0 - t_tick[1]), (sx1 - t_tick[0], sy1 - t_tick[1])]
        if not vertical:
            rel = [(ry, -rx) for rx, ry in rel]
        new.append(_geometry(record, SHAPE_LINE,
                             struct.pack("<4d", px + rel[0][0], py + rel[0][1],
                                         px + rel[1][0], py + rel[1][1])))
    new += [copy(template[23]), copy(template[24])]

    records.extend(new)
    # 挿入で位置がずれないよう、後ろにある部材から先にリンクを追記する
    for element in sorted((a, b), key=lambda e: e.start, reverse=True):
        _add_part_link(records, element, wire_id)
    counters.body = struct.pack("<II", count, next_id)
    return wire_id


def l_shaped_via(sheet: Record, start_id: int, end_id: int,
                 vertical_first: bool = True) -> list[tuple[float, float]]:
    """2 部材が水平・垂直に並んでいないとき、L 字で結ぶための折れ点を返す。"""
    by_id = {e.element_id: e for e in top_elements(sheet)}
    a, b = by_id.get(start_id), by_id.get(end_id)
    if not a or not b or a.origin is None or b.origin is None:
        return []
    (x0, y0), (x1, y1) = a.origin, b.origin
    if abs(x0 - x1) <= TOLERANCE or abs(y0 - y1) <= TOLERANCE:
        return []
    return [(x0, y1)] if vertical_first else [(x1, y0)]
