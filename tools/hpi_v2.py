#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""TAK 41BB 的 HPI v2 容器读取器（纯 Python，不需要 32 位 HPIUtil.dll）。

格式来源
--------
1. G:/Projects/TAK_41BB_CHS/TAUtil (MIT, Copyright (c) 2014 ArmouredFish)
   - TAUtil/Hpi/HpiChunk.cs   : SQSH 块头 19 字节
   - TAUtil/Hpi/HpiArchive.cs : DecryptInner、zlib / LZ77 解压
   TAUtil 自带的 HpiArchive 只支持 HPI v1 (0x00010000)；
   本工具补齐了 TAK 实际使用的 v2 (0x00020000)。
2. 对 41BB 干净安装 16 个 HPI 的实测（2026-11-13）。

HPI v2 结构（实测，2026-11-13 第二版）
------------------------------------
文件头 32 字节 = "HAPI" + 7 个 uint32：

    version, dirEnd, key, dirStart, dirLen, headerSize(32), dataEnd

* 名字表   @dirStart，长度 dirLen。通常是一个 SQSH 块；Jersey.hpi /
  terrain.hpi 是未压缩明文。内容为 NUL 结尾的名字串（目录名与文件名混排）。
* 索引块   @dirEnd（= dirStart + dirLen），通常也是一个 SQSH 块。
  解压后是一串 24 字节的文件记录，但**记录起点不固定**（前面穿插目录树
  节点，随包内目录数量变化）。因此本工具不假定固定起点，而是逐 4 字节
  扫描整个索引块，用强约束筛出真记录：

      uint32 NameOffset;       指向名字表里一个含 "." 的 NUL 结尾名字
      uint32 DataOffset;       指向文件数据区的 SQSH 块起点
      uint32 DecompressedSize; 必须等于该 SQSH 头的 DecompressedSize
      uint32 BlockSize;        必须等于 19 + 该 SQSH 头的 CompressedSize
      uint32 Unknown1;         常量（各包不同），疑为校验/时间戳
      uint32 Unknown2;         逐条变化，疑为内容校验

  四项同时吻合时几乎不可能误报。english.hpi 实测 599/601 条命中。
* 数据区   从 headerSize 起排列 SQSH 块。部分文件以明文直存（不套 SQSH），
  这类文件不在索引记录里，本工具暂不导出（详见 docs/01）。

SQSH 块头 19 字节 = "SQSH" + version(u8) + compression(u8) + encrypted(u8)
                    + compressedSize(u32) + decompressedSize(u32) + checksum(u32)
compression: 0=stored, 1=LZ77, 2=zlib
checksum 是「加密后」压缩数据的字节求和。

DecryptInner（TAUtil 原名，Encrypted=1 时作用于压缩数据）
--------------------------------------------------------
    pos = i & 0xFF                      # i 为块内相对下标
    plain[i] = ((cipher[i] - pos) & 0xFF) ^ pos

命令
----
    python hpi_v2.py list      <file.hpi> [--limit 30]
    python hpi_v2.py extract   <file.hpi> <outdir> [--text-only]
    python hpi_v2.py survey    <game_dir>
    python hpi_v2.py inventory <game_dir> <out.csv>
    python hpi_v2.py verify    <game_dir>
"""

import argparse
import bisect
import csv
import glob
import os
import struct
import sys
import zlib

SQSH_MAGIC = b"SQSH"
SQSH_HEAD = 19

# 需要翻译或需要检查的文本类资源
TEXT_EXT = {
    ".tdf", ".txt", ".htm", ".html", ".ota", ".fbi", ".gui",
    ".ini", ".def", ".msg", ".sct", ".dsc", ".str",
}


def decrypt_inner(data):
    """还原 Encrypted=1 的 SQSH 数据。逐字节，pos 取块内相对下标的低 8 位。"""
    return bytes(
        ((((c - (i & 0xFF)) & 0xFF) ^ (i & 0xFF)) & 0xFF)
        for i, c in enumerate(data)
    )


def decompress_lz77(data, expected_size):
    """TAUtil 里的 LZ77 滑动窗口解压（41BB 目前只用 zlib，保留以兼容其它包）。"""
    window = bytearray(4096)
    out = bytearray()
    pos = 0
    wpos = 1
    while pos < len(data) and len(out) < expected_size:
        tag = data[pos]
        pos += 1
        for _ in range(8):
            if (tag & 1) == 0:
                if pos >= len(data):
                    return bytes(out)
                value = data[pos]
                pos += 1
                out.append(value)
                window[wpos] = value
                wpos = (wpos + 1) & 0xFFF
            else:
                if pos + 2 > len(data):
                    return bytes(out)
                packed = data[pos] | (data[pos + 1] << 8)
                pos += 2
                read_pos = packed >> 4
                if read_pos == 0:
                    return bytes(out)
                count = (packed & 0x0F) + 2
                for _ in range(count):
                    value = window[read_pos]
                    out.append(value)
                    window[wpos] = value
                    read_pos = (read_pos + 1) & 0xFFF
                    wpos = (wpos + 1) & 0xFFF
            tag >>= 1
    return bytes(out)


class FileRecord(object):
    """索引块里的一条文件记录。"""

    __slots__ = ("name", "path", "name_offset", "data_offset",
                 "decompressed_size", "block_size", "compression", "encrypted")

    def __init__(self, name, path, name_offset, data_offset,
                 decompressed_size, block_size, compression, encrypted):
        self.name = name
        self.path = path
        self.name_offset = name_offset
        self.data_offset = data_offset
        self.decompressed_size = decompressed_size
        self.block_size = block_size
        self.compression = compression
        self.encrypted = encrypted

    def as_row(self, hpi_name):
        return [hpi_name, self.path, "1" if self.is_text() else "0",
                self.decompressed_size, "0x%x" % self.data_offset]

    def is_text(self):
        return os.path.splitext(self.name)[1].lower() in TEXT_EXT


class HpiV2(object):
    def __init__(self, path):
        self.path = path
        self.size = os.path.getsize(path)
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        header = d[:32]
        if len(header) < 32 or header[:4] != b"HAPI":
            raise ValueError("不是 HPI 文件")
        (_, self.version, self.dir_end, self.key, self.dir_start,
         self.dir_len, self.header_size, self.data_end) = struct.unpack("<4s7I", header)

        # ---- 名字表 ----
        name_chunk = self._chunk_info(self.dir_start)
        if name_chunk:
            self.name_blob, _ = self._read_chunk(self.dir_start)
            self.directory_compressed = True
        else:
            self.name_blob = d[self.dir_start:self.dir_start + self.dir_len]
            self.directory_compressed = False
        self.names = [x.decode("latin1") for x in self.name_blob.split(b"\0") if x]
        self.files = [n for n in self.names if "." in n]

        # ---- 索引块 ----
        self.index_blob = None
        if self._chunk_info(self.dir_end):
            self.index_blob, _ = self._read_chunk(self.dir_end)
        else:
            # 明文索引：长度由 dataEnd - dirEnd 推断，且需满足 12 + n*24
            cand = d[self.dir_end:self.data_end] if self.data_end > self.dir_end else b""
            if len(cand) >= 12 and (len(cand) - 12) % 24 == 0:
                self.index_blob = cand
        self.records = self._parse_index() if self.index_blob else []
        self.blocks = self._scan_blocks()
        self.data_blocks = self.blocks[:-2] if self.directory_compressed else self.blocks
        self.aligned = len(self.data_blocks) == len(self.files)

    # ---------- 底层 ----------

    def _chunk_info(self, off):
        """若 off 处是合法 SQSH 块头，返回 (comp, enc, csz, dsz, ck)，否则 None。"""
        d = self.data
        if off + SQSH_HEAD > len(d) or d[off:off + 4] != SQSH_MAGIC:
            return None
        _, _, comp, enc, csz, dsz, ck = struct.unpack_from("<4sBBBIII", d, off)
        if off + SQSH_HEAD + csz > len(d):
            return None
        return comp, enc, csz, dsz, ck

    def _read_chunk(self, off):
        info = self._chunk_info(off)
        if not info:
            raise ValueError("0x%x 处不是 SQSH 块" % off)
        comp, enc, csz, dsz, ck = info
        d = self.data
        raw = d[off + SQSH_HEAD:off + SQSH_HEAD + csz]
        if sum(raw) & 0xFFFFFFFF != ck:
            raise ValueError("SQSH 校验和不匹配 @0x%x" % off)
        payload = decrypt_inner(raw) if enc else raw
        if comp == 0:
            out = payload
        elif comp == 1:
            out = decompress_lz77(payload, dsz)
        elif comp == 2:
            out = zlib.decompress(payload)
        else:
            raise ValueError("未知压缩方式 %d @0x%x" % (comp, off))
        if len(out) != dsz:
            raise ValueError("解压长度不符 @0x%x: %d != %d" % (off, len(out), dsz))
        return out, info

    def _scan_blocks(self):
        """从 headerSize 起顺序扫描 SQSH 块，用于 survey 显示。"""
        blocks = []
        d = self.data
        off = self.header_size
        while off + SQSH_HEAD <= len(d):
            info = self._chunk_info(off)
            if not info:
                break
            comp, enc, csz, dsz, ck = info
            blocks.append((off, comp, enc, csz, dsz))
            off += SQSH_HEAD + csz
        return blocks

    # ---------- 索引块 ----------

    def _name_positions(self):
        """返回 [(offset, name)]，名字表按目录分组排列，用于推断所属目录。"""
        out = []
        off = 0
        b = self.name_blob
        while off < len(b):
            z = b.find(b"\0", off)
            if z < 0:
                break
            if z > off:
                out.append((off, b[off:z].decode("latin1")))
            off = z + 1
        return out

    def _infer_dir(self, name_offset, positions, pos_offsets):
        """取名字表中该名字之前最近一个不含点的名字作为目录名（近似）。"""
        i = bisect.bisect_right(pos_offsets, name_offset) - 1
        while i >= 0:
            _, nm = positions[i]
            if "." not in nm:
                return nm
            i -= 1
        return ""

    def _parse_index(self):
        """逐 4 字节扫描索引块，用强约束筛出文件记录。"""
        d = self.data
        idx = self.index_blob
        b = self.name_blob
        positions = self._name_positions()
        pos_offsets = [p[0] for p in positions]
        pos_offsets_set = set(pos_offsets)
        found = {}
        for o in range(0, len(idx) - 24 + 1, 4):
            no, do, dsz, bsz, _u1, _u2 = struct.unpack_from("<6I", idx, o)
            if no >= len(b) or dsz == 0 or (bsz != 0 and bsz <= SQSH_HEAD):
                continue
            z = b.find(b"\0", no)
            if z < 0:
                continue
            nm = b[no:z].decode("latin1")
            if "." not in nm:
                continue
            info = self._chunk_info(do) if self.header_size <= do < self.dir_start else None
            if info:
                comp, enc, csz, real_dsz, _ck = info
                if real_dsz != dsz or SQSH_HEAD + csz != bsz:
                    continue
            elif (bsz == 0 and no in pos_offsets_set
                  and self.header_size <= do and do + dsz <= self.dir_start):
                # 少数文件以裸字节存盘（无 SQSH 头，block_size 记 0）。
                # 旧版解析器要求 SQSH 头，把这些条目整条丢掉了 ——
                # 例如 missions.hpi 的 takmission02_mt.txt。
                comp, enc = 0, 0
            else:
                continue
            key = (no, do)
            if key in found:
                continue
            folder = self._infer_dir(no, positions, pos_offsets)
            path = (folder + "/" + nm) if folder else nm
            found[key] = FileRecord(nm, path, no, do, dsz, bsz, comp, enc)
        return sorted(found.values(), key=lambda r: r.data_offset)

    # ---------- 对外 ----------

    def entries(self):
        return self.records

    def read_file(self, rec):
        """按记录读出一个文件的完整内容。"""
        if rec.block_size == 0 and self._chunk_info(rec.data_offset) is None:
            # 裸字节条目：内容直接躺在 data 区
            return self.data[rec.data_offset:rec.data_offset + rec.decompressed_size]
        payload, _ = self._read_chunk(rec.data_offset)
        return payload

    def is_text(self, name):
        return os.path.splitext(name)[1].lower() in TEXT_EXT


def safe_name(path):
    return path.replace("\\", "__").replace("/", "__").replace(":", "_")


def cmd_list(args):
    hpi = HpiV2(args.hpi)
    print("文件: %s  (%d 字节)" % (hpi.path, hpi.size))
    print("版本: 0x%08x  名字表: %s  名字数: %d  索引记录: %d  顺序块: %d  对齐: %s"
          % (hpi.version,
             "SQSH 压缩" if hpi.directory_compressed else "明文",
             len(hpi.names), len(hpi.records), len(hpi.blocks),
             "是" if hpi.aligned else "否"))
    limit = args.limit
    for i, rec in enumerate(hpi.entries()):
        if limit and i >= limit:
            print("  ...(省略 %d 条，可用 --limit 0 看全部)" % (len(hpi.records) - limit))
            break
        print("  %-4d %-56s %9d B  off=0x%-9x comp=%d enc=%d"
              % (i, rec.path, rec.decompressed_size, rec.data_offset,
                 rec.compression, rec.encrypted))


def cmd_extract(args):
    hpi = HpiV2(args.hpi)
    if not hpi.records:
        print("无法导出：%s 没有可用的索引记录" % hpi.path)
        return 1
    os.makedirs(args.outdir, exist_ok=True)
    count = 0
    for rec in hpi.entries():
        if args.text_only and not rec.is_text():
            continue
        data = hpi.read_file(rec)
        with open(os.path.join(args.outdir, safe_name(rec.path)), "wb") as f:
            f.write(data)
        count += 1
    print("已导出 %d 个文件到 %s" % (count, args.outdir))
    return 0


def cmd_survey(args):
    paths = sorted(glob.glob(os.path.join(args.game_dir, "*.hpi")))
    print("%-18s %9s %8s %8s %8s %12s  %s"
          % ("HPI", "size", "名字数", "索引记录", "文本类", "文本解压B", "索引"))
    total_rec = total_text = total_bytes = 0
    for path in paths:
        try:
            hpi = HpiV2(path)
        except Exception as exc:
            print("%-18s 解析失败: %s" % (os.path.basename(path), exc))
            continue
        texts = [r for r in hpi.records if r.is_text()]
        size_sum = sum(r.decompressed_size for r in texts)
        print("%-18s %9d %8d %8d %8d %12d  %s"
              % (os.path.basename(path), hpi.size, len(hpi.names), len(hpi.records),
                 len(texts), size_sum, "有" if hpi.index_blob else "无"))
        total_rec += len(hpi.records)
        total_text += len(texts)
        total_bytes += size_sum
    print("-" * 78)
    print("合计: %d 个 HPI, %d 条索引记录(可导出文件), %d 个文本类文件, %d 字节解压文本"
          % (len(paths), total_rec, total_text, total_bytes))
    return 0


def cmd_inventory(args):
    """导出全部 HPI 的可导出文件清单（真实偏移与解压大小）。"""
    paths = sorted(glob.glob(os.path.join(args.game_dir, "*.hpi")))
    rows = []
    for path in paths:
        try:
            hpi = HpiV2(path)
        except Exception as exc:
            print("跳过 %s: %s" % (os.path.basename(path), exc))
            continue
        for rec in hpi.records:
            rows.append(rec.as_row(os.path.basename(path)))
    with open(args.out_csv, "w", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(["hpi", "path", "is_text", "decompressed_size", "data_offset"])
        writer.writerows(rows)
    print("已写出 %d 条记录到 %s（文本类 %d 条）"
          % (len(rows), args.out_csv, sum(1 for r in rows if r[2] == "1")))
    return 0


def cmd_verify(args):
    """独立校验：逐条重读 SQSH 块，确认校验和、解压长度、路径可读。"""
    paths = sorted(glob.glob(os.path.join(args.game_dir, "*.hpi")))
    bad = 0
    total = 0
    for path in paths:
        hpi = HpiV2(path)
        for rec in hpi.records:
            total += 1
            try:
                data = hpi.read_file(rec)
                if len(data) != rec.decompressed_size:
                    raise ValueError("长度 %d != %d" % (len(data), rec.decompressed_size))
            except Exception as exc:
                bad += 1
                if bad <= 10:
                    print("  FAIL %s :: %s :: %s" % (os.path.basename(path), rec.path, exc))
    print("校验 %d 条记录，失败 %d 条" % (total, bad))
    return 1 if bad else 0


def main():
    parser = argparse.ArgumentParser(description="TAK 41BB HPI v2 读取器")
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("list", help="列出 HPI 内文件")
    p.add_argument("hpi")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_list)

    p = sub.add_parser("extract", help="导出 HPI 内文件")
    p.add_argument("hpi")
    p.add_argument("outdir")
    p.add_argument("--text-only", action="store_true")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("survey", help="普查游戏目录下所有 HPI")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_survey)

    p = sub.add_parser("inventory", help="导出全部 HPI 文件清单为 CSV")
    p.add_argument("game_dir")
    p.add_argument("out_csv")
    p.set_defaults(func=cmd_inventory)

    p = sub.add_parser("verify", help="逐条校验 SQSH 块")
    p.add_argument("game_dir")
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    if not getattr(args, "func", None):
        parser.print_help()
        return 1
    return args.func(args) or 0


if __name__ == "__main__":
    sys.exit(main())
