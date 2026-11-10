import struct
import unittest

from tfastool import container, elements, parts


def geometry(shape: int, data: bytes) -> bytes:
    return b"\x1e" + b"\0" * 9 + bytes([shape]) + b"\0" * 15 + data + b"\x1e\0\0\0\0"


def make_tfs() -> container.TfsFile:
    R = container.Record
    frame = struct.pack("<II6d", 3, 1, 100, 200, 151, 200, 100, 251)
    sheet = R(container.COMPRESSED, 5, b"", [
        R(0x1001, 5, b""),
        R(elements.KIND_BEGIN, 5, struct.pack("<II", 10, 0x00100120)),
        R(4, 5, elements.ATTR_LINK + struct.pack("<I", 99) + b"\x01\x01\0\x04"),
        R(4, 5, b"\0\0\0\0" + b"E0A10S290008\0" + b"\0" * 7),
        R(4, 5, elements.ATTR_GUID + b"\xaa" * 16),
        R(elements.KIND_GEOMETRY, 5, geometry(0x39, frame)),
        R(elements.KIND_BEGIN, 5, struct.pack("<II", 11, 0x20000040)),
        R(elements.KIND_GEOMETRY, 5, geometry(0x40, struct.pack("<3d", 100, 200, 60))),
        R(elements.KIND_END, 5, b""),
        R(elements.KIND_END, 5, b""),
    ])
    sections = [None] * container.TABLE_ENTRIES
    sections[4] = container.Section(4, [R(elements.KIND_COUNTERS, 5, struct.pack("<II", 2, 12)), sheet])
    return container.TfsFile(container.MAGIC + b"\0" * (container.HEADER_SIZE - 4), sections)


class ElementsTest(unittest.TestCase):
    def test_top_elements(self):
        sheet = elements.sheets(make_tfs())[0]
        (element,) = elements.top_elements(sheet)
        self.assertEqual((element.start, element.end, element.element_id), (1, 9, 10))
        self.assertEqual(element.code, "E0A10S290008")
        self.assertEqual(element.origin, (100.0, 200.0))

    def test_translate_lines(self):
        moved = elements.translate_geometry(geometry(0x20, struct.pack("<3d", 1, 2, 3)), 10, 100)
        self.assertEqual(struct.unpack_from("<3d", moved, 26), (11, 102, 103))
        moved = elements.translate_geometry(geometry(0x18, struct.pack("<3d", 1, 2, 3)), 10, 100)
        self.assertEqual(struct.unpack_from("<3d", moved, 26), (101, 12, 13))
        with self.assertRaises(NotImplementedError):
            elements.translate_geometry(geometry(0x48, b"\0" * 40), 1, 1)

    def test_duplicate(self):
        tfs = make_tfs()
        sheet = elements.sheets(tfs)[0]
        original = elements.top_elements(sheet)[0]
        copy = elements.duplicate_element(tfs, sheet, original, 300, -50)
        self.assertEqual(copy.element_id, 12)
        self.assertEqual(copy.origin, (400.0, 150.0))
        records = sheet.children[copy.start : copy.end + 1]
        self.assertEqual(len(records), 8)  # リンク属性は引き継がない
        self.assertFalse(any(r.body.startswith(elements.ATTR_LINK) for r in records))
        guid = next(r for r in records if r.body.startswith(elements.ATTR_GUID))
        self.assertNotEqual(guid.body[4:], b"\xaa" * 16)
        self.assertEqual(struct.unpack_from("<3d", records[-3].body, 26), (400, 150, 60))
        self.assertEqual(struct.unpack("<II", tfs.section(5).records[0].body), (4, 14))
        again = container.parse(container.serialize(tfs))
        self.assertEqual(parts.count_parts(again)[0].counts["E0A10S290008"], 2)
        self.assertEqual(elements.top_elements(sheet)[0].origin, (100.0, 200.0))


if __name__ == "__main__":
    unittest.main()
