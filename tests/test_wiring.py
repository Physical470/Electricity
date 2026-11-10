import struct
import unittest

from tfastool import container, elements, wiring
from tests.test_elements import geometry

R = container.Record


def part(element_id: int, x: float, y: float, linked_to: int | None = None) -> list:
    frame = struct.pack("<II6d", 3, 1, x, y, x + 51, y, x, y + 51)
    records = [R(2, 5, struct.pack("<II", element_id, 0x00100120))]
    if linked_to is not None:
        records.append(R(4, 5, elements.ATTR_LINK + struct.pack("<I", linked_to) + b"\x01\x01\0\x04"))
    records += [
        R(4, 5, b"\0\0\0\0" + b"E0A10S290008\0" + b"\0" * 7),
        R(3, 5, geometry(0x39, frame)),
        R(2, 5, struct.pack("<II", element_id + 1, 0x20000040)),
        R(3, 5, geometry(0x40, struct.pack("<3d", x, y, 60))),
        R(5, 5, b""), R(5, 5, b""),
    ]
    return records


def template_wire() -> list:
    """(0,0) の部材 10 から (0,1000) の部材 20 への垂直配線。"""
    ends = bytearray(84)
    ends[:4] = wiring.ATTR_ENDS
    struct.pack_into("<d", ends, 36, 90.0)
    struct.pack_into("<d", ends, 76, 270.0)
    line = lambda a, b: R(3, 5, geometry(0x20, struct.pack("<3d", 0, a, b)))
    return [
        R(2, 5, struct.pack("<II", 30, wiring.WIRE_FLAGS)),
        R(4, 5, elements.ATTR_LINK + struct.pack("<I", 10) + wiring.END_START
          + struct.pack("<I", 20) + wiring.END_FINISH),
        R(4, 5, b"\x00\x04\0\0" + struct.pack("<2d", 1, 1)), R(4, 5, b"\x02\x04\0\0"), R(4, 5, b"\x0a\x04\0\0"),
        R(4, 5, bytes(ends)),
        R(3, 5, geometry(0x39, struct.pack("<II4d", 2, 1, 0, 0, 0, 1000))),
        R(2, 5, struct.pack("<II", 31, 0x410)), R(2, 5, struct.pack("<II", 32, 0x170)),
        R(4, 5, b"\xff\x01\0\0\0"),
        line(0, 1000), line(0, 60), line(60, 940), line(940, 1000),
        R(5, 5, b""), R(5, 5, b""),
        R(2, 5, struct.pack("<II", 33, 0x430)),
        R(4, 5, b"\x30\x04\0\0"), R(4, 5, b"\x32\x04\0\0"), R(4, 5, b"\x33\x04\0\0"),
        R(3, 5, geometry(0x39, struct.pack("<II4d", 2, 1, 0, 500, 0, 501))),
        R(3, 5, geometry(0x10, struct.pack("<4d", -26.5, 511.5, 26.5, 458.5))),
        R(3, 5, geometry(0x10, struct.pack("<4d", -26.5, 541.5, 26.5, 488.5))),
        R(5, 5, b""), R(5, 5, b""),
    ]


def make_tfs() -> container.TfsFile:
    children = (part(10, 0, 0, linked_to=30) + part(20, 0, 1000, linked_to=30)
                + part(40, 5000, 2000) + part(50, 4000, 2000) + part(60, 4100, 2100)
                + template_wire())
    sections = [None] * container.TABLE_ENTRIES
    sections[4] = container.Section(4, [R(1, 5, struct.pack("<II", 14, 100)),
                                        R(container.COMPRESSED, 5, b"", children)])
    return container.TfsFile(container.MAGIC + b"\0" * (container.HEADER_SIZE - 4), sections)


class WiringTest(unittest.TestCase):
    def test_horizontal_wire(self):
        tfs = make_tfs()
        sheet = elements.sheets(tfs)[0]
        wire_id = wiring.add_wire(tfs, sheet, 40, 50)
        self.assertEqual(wire_id, 100)
        self.assertEqual(struct.unpack("<II", tfs.section(5).records[0].body), (18, 104))
        by_id = {e.element_id: e for e in elements.top_elements(sheet)}
        wire = sheet.children[by_id[100].start : by_id[100].end + 1]
        self.assertEqual(wire[1].body[4:], struct.pack("<I", 40) + wiring.END_START
                         + struct.pack("<I", 50) + wiring.END_FINISH)
        self.assertEqual(struct.unpack_from("<d", wire[5].body, 36)[0], 180.0)
        self.assertEqual(struct.unpack_from("<d", wire[5].body, 76)[0], 0.0)
        self.assertEqual(struct.unpack_from("<4d", wire[6].body, 34), (5000, 2000, 4000, 2000))
        lines = [(r.body[10], struct.unpack_from("<3d", r.body, 26)) for r in wire[10:14]]
        self.assertEqual(lines, [(0x18, (2000, 5000, 4000)), (0x18, (2000, 5000, 4940)),
                                 (0x18, (2000, 4940, 4060)), (0x18, (2000, 4060, 4000))])
        self.assertEqual(struct.unpack_from("<4d", wire[20].body, 34), (4500, 2000, 4501, 2000))
        self.assertEqual(struct.unpack_from("<4d", wire[21].body, 26), (4511.5, 2026.5, 4458.5, 1973.5))
        # 部材側にリンクが追加される
        for part_id in (40, 50):
            element = by_id[part_id]
            link = sheet.children[element.start + 1]
            self.assertEqual(link.body, elements.ATTR_LINK + struct.pack("<I", 100) + b"\x01\x01\0\x04")
        # ひな形は変更されない
        self.assertEqual(struct.unpack_from("<3d", sheet.children[by_id[30].start + 10].body, 26), (0, 0, 1000))

    def test_second_link_gets_next_ordinal(self):
        tfs = make_tfs()
        sheet = elements.sheets(tfs)[0]
        wiring.add_wire(tfs, sheet, 20, 10)
        link = sheet.children[1]
        self.assertEqual(link.body[12:], struct.pack("<I", 100) + b"\x02\x01\0\x04")

    def test_l_shaped_wire(self):
        tfs = make_tfs()
        sheet = elements.sheets(tfs)[0]
        via = wiring.l_shaped_via(sheet, 40, 60)          # (5000,2000) → (4100,2100)
        self.assertEqual(via, [(5000.0, 2100.0)])          # 縦→横、左折
        wire_id = wiring.add_wire(tfs, sheet, 40, 60, via)
        by_id = {e.element_id: e for e in elements.top_elements(sheet)}
        wire = sheet.children[by_id[wire_id].start : by_id[wire_id].end + 1]
        shapes = [r.body[10] if r.kind == 3 else {2: "(", 5: ")", 4: "a"}[r.kind] for r in wire]
        self.assertEqual(shapes, ["(", "a", "a", "a", "a", "a", 0x39,
                                  "(", "(", "a", 0x20, 0x20, 0x20, ")", 0x48,
                                  "(", "a", 0x18, 0x18, 0x18, ")", ")",
                                  "(", "a", "a", "a", 0x39, 0x10, 0x10, ")", ")"])
        self.assertEqual(len(wire[2].body), 4 + 24)
        self.assertEqual(struct.unpack_from("<II6d", wire[6].body, 26),
                         (3, 1, 5000, 2000, 5000, 2100, 4100, 2100))
        self.assertEqual(struct.unpack_from("<I", wire[6].body, 1)[0], len(wire[6].body) - 15)
        self.assertEqual([struct.unpack_from("<3d", r.body, 26) for r in wire[10:13]],
                         [(5000, 2000, 2070), (5000, 2000, 2060), (5000, 2060, 2070)])
        # 左折なので円弧は進行方向の順（反時計回り）
        self.assertEqual(struct.unpack_from("<7d", wire[14].body, 26),
                         (4970, 2070, 30, 5000, 2070, 4970, 2100))
        self.assertEqual([struct.unpack_from("<3d", r.body, 26) for r in wire[17:20]],
                         [(2100, 4970, 4100), (2100, 4970, 4160), (2100, 4160, 4100)])
        self.assertEqual(struct.unpack_from("<d", wire[5].body, 36)[0], 90.0)
        self.assertEqual(struct.unpack_from("<d", wire[5].body, 76)[0], 0.0)
        self.assertEqual(struct.unpack("<II", tfs.section(5).records[0].body), (19, 105))

    def test_right_turn_arc_is_reversed(self):
        tfs = make_tfs()
        sheet = elements.sheets(tfs)[0]
        via = wiring.l_shaped_via(sheet, 60, 40, vertical_first=False)   # 右へ進み、下へ右折
        self.assertEqual(via, [(5000.0, 2100.0)])
        wire_id = wiring.add_wire(tfs, sheet, 60, 40, via)
        by_id = {e.element_id: e for e in elements.top_elements(sheet)}
        arc = sheet.children[by_id[wire_id].start + 14]
        self.assertEqual(struct.unpack_from("<7d", arc.body, 26),
                         (4970, 2070, 30, 5000, 2070, 4970, 2100))

    def test_rejects_diagonal_and_unknown(self):
        tfs = make_tfs()
        sheet = elements.sheets(tfs)[0]
        with self.assertRaises(ValueError):
            wiring.add_wire(tfs, sheet, 40, 60)
        with self.assertRaises(LookupError):
            wiring.add_wire(tfs, sheet, 40, 999)


if __name__ == "__main__":
    unittest.main()
