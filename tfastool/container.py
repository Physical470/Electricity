"""Tfas 図面ファイル (.tfs) のコンテナ構造の読み書き。

実ファイルの解析から分かった構造（非公式・Tfas 14 E 保存ファイル 1 件で確認）:

* 先頭 0x58 バイト: ヘッダ（"TFAS" で始まる。0x40 からの 20 バイトは用途不明）
* 0x58〜0x148: セクション表。(開始位置, 終了位置) の u32 の組が 30 個。
  未使用のセクションは 0xFFFFFFFF。
* 各セクション: レコードの並び。レコードは
  ``[u32 長さ(ヘッダ 8 バイトを含む)] [u16 種別] [u16 セクション番号] [本体]``
  で、次のレコードは 8 バイト境界から始まる。
* 種別 0x2001 のレコード本体は zlib 圧縮されており、展開すると
  同じ形式のレコード列（境界詰めなし）になる。
"""
from __future__ import annotations

import struct
import zlib
from dataclasses import dataclass, field

MAGIC = b"TFAS"
HEADER_SIZE = 0x58
TABLE_ENTRIES = 30
TABLE_END = HEADER_SIZE + TABLE_ENTRIES * 8
ABSENT = 0xFFFFFFFF
COMPRESSED = 0x2001


@dataclass
class Record:
    kind: int
    section: int
    body: bytes
    #: 種別 0x2001 のとき、展開後のレコード列
    children: list["Record"] | None = None


@dataclass
class Section:
    index: int
    records: list[Record] = field(default_factory=list)


@dataclass
class TfsFile:
    header: bytes
    #: 長さ 30。未使用セクションは None
    sections: list[Section | None]

    def section(self, number: int) -> Section | None:
        """レコードに書かれているセクション番号（1 始まり）で取得する。"""
        return self.sections[number - 1]


def parse_records(data: bytes, aligned: bool) -> list[Record]:
    records = []
    pos = 0
    while pos < len(data):
        if pos + 8 > len(data):
            raise ValueError(f"レコードヘッダが途中で切れています (位置 {pos:#x})")
        length, kind, section = struct.unpack_from("<IHH", data, pos)
        if length < 8 or pos + length > len(data):
            raise ValueError(f"レコード長が不正です (位置 {pos:#x}, 長さ {length})")
        body = data[pos + 8 : pos + length]
        record = Record(kind, section, body)
        if kind == COMPRESSED:
            record.children = parse_records(zlib.decompress(body), aligned=False)
        records.append(record)
        pos += length
        if aligned:
            pos = (pos + 7) & ~7
    return records


def build_records(records: list[Record], aligned: bool, level: int = 6) -> bytes:
    out = bytearray()
    for record in records:
        body = record.body
        if record.children is not None:
            body = zlib.compress(build_records(record.children, False, level), level)
        out += struct.pack("<IHH", len(body) + 8, record.kind, record.section)
        out += body
        if aligned:
            out += b"\0" * (-len(out) % 8)
    return bytes(out)


def load(path: str) -> TfsFile:
    with open(path, "rb") as fh:
        data = fh.read()
    return parse(data)


def parse(data: bytes) -> TfsFile:
    if data[:4] != MAGIC:
        raise ValueError("TFAS 図面ファイルではありません")
    table = struct.unpack_from(f"<{TABLE_ENTRIES * 2}I", data, HEADER_SIZE)
    sections: list[Section | None] = []
    for i in range(TABLE_ENTRIES):
        start, end = table[2 * i], table[2 * i + 1]
        if start == ABSENT:
            sections.append(None)
            continue
        sections.append(Section(i, parse_records(data[start:end], aligned=True)))
    return TfsFile(data[:HEADER_SIZE], sections)


def serialize(tfs: TfsFile, level: int = 6) -> bytes:
    """ファイル全体を組み立て直す。level は zlib 圧縮レベル（Tfas 自身は 6 相当）。"""
    blobs = []
    table = []
    pos = TABLE_END
    for section in tfs.sections:
        if section is None:
            table += [ABSENT, ABSENT]
            continue
        blob = build_records(section.records, aligned=True, level=level)
        table += [pos, pos + len(blob)]
        blobs.append(blob)
        pos += len(blob)
    return tfs.header + struct.pack(f"<{len(table)}I", *table) + b"".join(blobs)


def save(tfs: TfsFile, path: str, level: int = 6) -> None:
    with open(path, "wb") as fh:
        fh.write(serialize(tfs, level))
