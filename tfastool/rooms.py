"""建築図のシートから、部屋名の文字と壁の線を読み取り、部屋の範囲と中心を求める。

方法: 部屋名の文字の位置から上下左右に探索線を伸ばし、最初に当たる壁の線までを
部屋の範囲とする。出入口などの開口で探索線が抜けないよう、位置をずらした複数の
探索線の中央値を使う。四角形でない部屋では、範囲は部屋名の周りの代表的な矩形になる。
"""
from __future__ import annotations

import re
import statistics
import struct
from dataclasses import dataclass

from .container import Record, TfsFile
from .elements import GEOMETRY_DATA_OFFSET, KIND_GEOMETRY

LAYER_SECTION = 2
SHAPE_LINE, SHAPE_HORIZONTAL, SHAPE_VERTICAL, SHAPE_TEXT = 0x10, 0x18, 0x20, 0x70
#: 壁として扱うレイヤ名の既定パターン（Jw_cad から読み込んだ建築図のレイヤ名を想定）
DEFAULT_WALL_PATTERN = "仕上|開口|建具"
_LAYER = re.compile(rb"\x04\x00\x00\x00(..)\x00\x83\x05\x0f\x04\x00\x00\x00\x11.\x00\x00\x14\x03"
                    rb"\x1e(....)\x0f(....)", re.S)
_FONT_END = re.compile(rb"\x00")
EPS = 1.0
SAMPLES = 9


def layer_names(tfs: TfsFile) -> dict[int, str]:
    """レイヤ番号 -> レイヤ名。"""
    names: dict[int, str] = {}
    section = tfs.section(LAYER_SECTION)
    for top in section.records if section else []:
        for record in top.children or []:
            for match in _LAYER.finditer(record.body):
                length = struct.unpack("<I", match.group(2))[0]
                if match.group(2) != match.group(3) or not 0 < length < 200:
                    continue
                raw = record.body[match.end() : match.end() + length]
                names[struct.unpack("<H", match.group(1))[0]] = raw.decode("cp932", "replace")
    return names


def _layer(record: Record) -> int:
    return struct.unpack_from("<H", record.body, 14)[0]


@dataclass
class Label:
    text: str
    x: float
    y: float
    layer: int


def labels(sheet: Record) -> list[Label]:
    """シート内の文字を位置つきで返す。"""
    result = []
    for record in sheet.children:
        body = record.body
        if record.kind != KIND_GEOMETRY or body[10] != SHAPE_TEXT or len(body) < 96:
            continue
        x, y = struct.unpack_from("<2d", body, GEOMETRY_DATA_OFFSET)
        # 文字列は書体名（NUL 終端）の直後にある
        font_start = 93
        font_end = body.find(b"\0", font_start)
        if font_end < 0:
            continue
        text = body[font_end + 1 :].split(b"\0", 1)[0].decode("cp932", "replace").strip()
        if text:
            result.append(Label(text, x, y, _layer(record)))
    return result


class Walls:
    """水平・垂直の壁の線の集まり。"""

    def __init__(self, sheet: Record, layers: set[int]):
        self.horizontal: list[tuple[float, float, float]] = []  # (y, x小, x大)
        self.vertical: list[tuple[float, float, float]] = []    # (x, y小, y大)
        for record in sheet.children:
            if record.kind != KIND_GEOMETRY or _layer(record) not in layers:
                continue
            body = record.body
            data_length = len(body) - GEOMETRY_DATA_OFFSET - 5
            if body[10] == SHAPE_HORIZONTAL and data_length == 24:
                y, x0, x1 = struct.unpack_from("<3d", body, GEOMETRY_DATA_OFFSET)
                self.add(x0, y, x1, y)
            elif body[10] == SHAPE_VERTICAL and data_length == 24:
                x, y0, y1 = struct.unpack_from("<3d", body, GEOMETRY_DATA_OFFSET)
                self.add(x, y0, x, y1)
            elif body[10] == SHAPE_LINE and data_length == 32:
                self.add(*struct.unpack_from("<4d", body, GEOMETRY_DATA_OFFSET))

    def add(self, x0: float, y0: float, x1: float, y1: float) -> None:
        if abs(y0 - y1) < EPS:
            self.horizontal.append((y0, min(x0, x1), max(x0, x1)))
        elif abs(x0 - x1) < EPS:
            self.vertical.append((x0, min(y0, y1), max(y0, y1)))

    def reach(self, x: float, y: float):
        """(x, y) から左・右・下・上に最初に当たる壁の位置（無ければ None）。"""
        left = max((vx for vx, a, b in self.vertical if vx < x - EPS and a - EPS <= y <= b + EPS), default=None)
        right = min((vx for vx, a, b in self.vertical if vx > x + EPS and a - EPS <= y <= b + EPS), default=None)
        down = max((hy for hy, a, b in self.horizontal if hy < y - EPS and a - EPS <= x <= b + EPS), default=None)
        up = min((hy for hy, a, b in self.horizontal if hy > y + EPS and a - EPS <= x <= b + EPS), default=None)
        return left, right, down, up


@dataclass
class Room:
    name: str
    left: float
    right: float
    bottom: float
    top: float

    @property
    def center(self) -> tuple[float, float]:
        return (self.left + self.right) / 2, (self.bottom + self.top) / 2

    @property
    def size(self) -> tuple[float, float]:
        return self.right - self.left, self.top - self.bottom


def _median(values):
    values = [v for v in values if v is not None]
    return statistics.median_low(values) if values else None


def room_box(walls: Walls, x: float, y: float):
    """(x, y) を含む部屋の範囲 (左, 右, 下, 上)。壁が見つからなければ None。"""
    left, right, down, up = walls.reach(x, y)
    for _ in range(3):
        if None in (left, right, down, up):
            return None
        ys = [down + (up - down) * k / (SAMPLES + 1) for k in range(1, SAMPLES + 1)]
        xs = [left + (right - left) * k / (SAMPLES + 1) for k in range(1, SAMPLES + 1)]
        left = _median(walls.reach(x, yy)[0] for yy in ys)
        right = _median(walls.reach(x, yy)[1] for yy in ys)
        down = _median(walls.reach(xx, y)[2] for xx in xs)
        up = _median(walls.reach(xx, y)[3] for xx in xs)
    if None in (left, right, down, up):
        return None
    return left, right, down, up


def wall_layers(tfs: TfsFile, pattern: str = DEFAULT_WALL_PATTERN) -> set[int]:
    return {number for number, name in layer_names(tfs).items() if re.search(pattern, name)}


def find_rooms(sheet: Record, layers: set[int]) -> list[Room]:
    """シート内の文字ごとに部屋の範囲を求める（範囲が求まった文字だけ返す）。"""
    walls = Walls(sheet, layers)
    rooms = []
    for label in labels(sheet):
        # 文字の基準点は左下なので、少し内側から探索する
        box = room_box(walls, label.x + 200.0, label.y + 60.0)
        if box:
            rooms.append(Room(label.text, *box))
    return rooms
