import struct
import unittest

from tfastool import container, parts


def make_file() -> bytes:
    """実ファイルと同じ構造の最小限の合成データを作る。"""
    master = container.Record(parts.KIND_MASTER, 4,
                              b"E0A10S290008".ljust(24, b"\0") + "ﾀﾞｳﾝﾗｲﾄ".encode("cp932") + b"\0")
    attr = container.Record(parts.KIND_ATTRIBUTE, 5, b"\0\0\0\0" + b"E0A10S290008\0" + b"\x01" * 7)
    other = container.Record(0x0003, 5, b"abc")  # 長さが 8 の倍数でない本体
    sections = [None] * container.TABLE_ENTRIES
    sections[3] = container.Section(3, [container.Record(container.COMPRESSED, 4, b"", [master])])
    sections[4] = container.Section(4, [
        container.Record(0x0001, 5, b"\x01\x02\x03"),
        container.Record(container.COMPRESSED, 5, b"", [attr, other, attr]),
    ])
    header = container.MAGIC + b"\0" * (container.HEADER_SIZE - 4)
    return container.serialize(container.TfsFile(header, sections))


class ContainerTest(unittest.TestCase):
    def test_roundtrip_is_byte_identical(self):
        data = make_file()
        self.assertEqual(container.serialize(container.parse(data)), data)

    def test_section_table_points_at_aligned_sections(self):
        data = make_file()
        table = struct.unpack_from("<60I", data, container.HEADER_SIZE)
        self.assertEqual(table[0], container.ABSENT)
        self.assertEqual(table[6], container.TABLE_END)
        self.assertEqual(table[9], len(data))
        self.assertEqual(table[7] % 8, 0)

    def test_other_compression_level_keeps_content(self):
        tfs = container.parse(make_file())
        again = container.parse(container.serialize(tfs, level=9))
        self.assertEqual(again.section(5).records[1].children[1].body, b"abc")

    def test_rejects_non_tfas(self):
        with self.assertRaises(ValueError):
            container.parse(b"JwwData." + b"\0" * 400)

    def test_count_parts(self):
        tfs = container.parse(make_file())
        self.assertEqual(parts.master_names(tfs), {"E0A10S290008": "ﾀﾞｳﾝﾗｲﾄ"})
        sheets = parts.count_parts(tfs)
        self.assertEqual(len(sheets), 1)
        self.assertEqual(sheets[0].counts["E0A10S290008"], 2)


if __name__ == "__main__":
    unittest.main()
