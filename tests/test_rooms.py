import struct
import unittest

from tfastool import container, rooms
from tests.test_elements import geometry

R = container.Record


def on_layer(body: bytes, layer: int) -> bytes:
    return body[:14] + struct.pack("<H", layer) + body[16:]


def text(x: float, y: float, value: str, layer: int) -> R:
    body = bytearray(geometry(0x70, struct.pack("<2d", x, y) + b"\0" * 51))
    body = bytes(body[:93]) + "ＭＳ ゴシック".encode("cp932") + b"\0" + value.encode("cp932") + b"\n\0"
    return R(3, 5, on_layer(body, layer))


def wall(x0, y0, x1, y1, layer=65) -> R:
    if y0 == y1:
        return R(3, 5, on_layer(geometry(0x18, struct.pack("<3d", y0, x0, x1)), layer))
    return R(3, 5, on_layer(geometry(0x20, struct.pack("<3d", x0, y0, y1)), layer))


class RoomsTest(unittest.TestCase):
    def sheet(self) -> R:
        return R(container.COMPRESSED, 5, b"", [
            # 4000 x 3000 の部屋。下の壁に 800 の出入口、右に別の部屋が続く
            wall(0, 0, 1000, 0), wall(1800, 0, 4000, 0), wall(0, 3000, 4000, 3000),
            wall(0, 0, 0, 3000), wall(4000, 0, 4000, 3000),
            wall(1000, -2000, 1800, -2000),          # 出入口の先にある遠い壁
            wall(2000, 100, 2000, 2900, layer=67),   # 部屋を横切る梁の線（壁レイヤではない）
            wall(4000, 0, 9000, 0), wall(4000, 3000, 9000, 3000), wall(9000, 0, 9000, 3000),
            text(1200, 1500, "洋室(1)", 68), text(6000, 1000, "納戸", 68),
            text(20000, 20000, "外", 68),
        ])

    def test_labels(self):
        found = rooms.labels(self.sheet())
        self.assertEqual([(l.text, l.x, l.y, l.layer) for l in found][:2],
                         [("洋室(1)", 1200.0, 1500.0, 68), ("納戸", 6000.0, 1000.0, 68)])

    def test_room_center_ignores_door_opening_and_other_layers(self):
        found = rooms.find_rooms(self.sheet(), {65})
        self.assertEqual([room.name for room in found], ["洋室(1)", "納戸"])
        self.assertEqual(found[0].center, (2000.0, 1500.0))
        self.assertEqual(found[0].size, (4000.0, 3000.0))
        self.assertEqual(found[1].center, (6500.0, 1500.0))

    def test_layer_names(self):
        def layer(number: int, name: str) -> bytes:
            raw = name.encode("cp932")
            return (b"\x04\0\0\0" + struct.pack("<H", number) + b"\0\x83\x05\x0f\x04\0\0\0\x11\x90\0\0\x14\x03"
                    + b"\x1e" + struct.pack("<I", len(raw)) + b"\x0f" + struct.pack("<I", len(raw))
                    + raw + b"\x0a\x05\x14\x03")
        sections = [None] * container.TABLE_ENTRIES
        sections[1] = container.Section(1, [R(container.COMPRESSED, 2, b"", [
            R(0x1001, 2, b"\0" * 7 + layer(65, "5-9:仕上ﾗｲﾝ") + layer(64, "5-2:壁芯") + layer(71, "5-6:開口姿図"))])])
        tfs = container.TfsFile(container.MAGIC + b"\0" * (container.HEADER_SIZE - 4), sections)
        self.assertEqual(rooms.layer_names(tfs), {65: "5-9:仕上ﾗｲﾝ", 64: "5-2:壁芯", 71: "5-6:開口姿図"})
        self.assertEqual(rooms.wall_layers(tfs), {65, 71})


if __name__ == "__main__":
    unittest.main()
