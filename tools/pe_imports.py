#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""最小 PE 导入表/导出表解析器（不依赖 pefile）。

用途：为"代理 DLL 选型"统计每个模块静态导入的 DLL 与函数名，
以及某个 DLL 的导出表长什么样（判断能否被代理）。

用法:
    python tools/pe_imports.py <file.exe|dll>            # 打印导入表摘要
    python tools/pe_imports.py <file> --detail winmm.dll # 打印该 DLL 的导入函数
    python tools/pe_imports.py <file> --exports          # 打印自身导出表
    python tools/pe_imports.py <file> --json             # JSON 输出
"""
from __future__ import print_function

import json
import struct
import sys

DIR32 = 0x20  # IMAGE_FILE_MACHINE_I386
DIR_IMPORT = 1
DIR_EXPORT = 0


class PE(object):
    def __init__(self, path):
        with open(path, "rb") as f:
            self.data = f.read()
        d = self.data
        if d[:2] != b"MZ":
            raise ValueError("not MZ")
        e_lfanew = struct.unpack_from("<I", d, 0x3C)[0]
        if d[e_lfanew:e_lfanew + 4] != b"PE\0\0":
            raise ValueError("not PE")
        self.nt = e_lfanew
        coff = e_lfanew + 4
        self.machine, self.nsec, _, _, _, self.opt_size, self.characteristics = \
            struct.unpack_from("<HHIIIHH", d, coff)
        self.opt = coff + 20
        magic = struct.unpack_from("<H", d, self.opt)[0]
        self.pe32plus = (magic == 0x20B)
        if self.pe32plus:
            self.image_base = struct.unpack_from("<Q", d, self.opt + 24)[0]
            self.ndir = struct.unpack_from("<I", d, self.opt + 108)[0]
            self.dir = self.opt + 112
        else:
            self.image_base = struct.unpack_from("<I", d, self.opt + 28)[0]
            self.ndir = struct.unpack_from("<I", d, self.opt + 92)[0]
            self.dir = self.opt + 96
        self.sections = []
        sec = self.opt + self.opt_size
        for i in range(self.nsec):
            o = sec + i * 40
            name = d[o:o + 8].rstrip(b"\0").decode("latin-1")
            vsize, va, rsize, rptr = struct.unpack_from("<IIII", d, o + 8)
            self.sections.append((name, va, max(vsize, rsize), rptr, rsize))

    def rva2off(self, rva):
        for name, va, size, rptr, rsize in self.sections:
            if va <= rva < va + size:
                off = rva - va
                if off >= rsize:      # 落在未初始化区
                    return None
                return rptr + off
        return None

    def read_str(self, rva):
        off = self.rva2off(rva)
        if off is None:
            return ""
        end = self.data.find(b"\0", off)
        return self.data[off:end].decode("latin-1", "replace")

    def dir_entry(self, idx):
        if idx >= self.ndir:
            return 0, 0
        return struct.unpack_from("<II", self.data, self.dir + idx * 8)

    def imports(self):
        """-> [(dll_name, [func_name or '#ordinal', ...]), ...]"""
        rva, size = self.dir_entry(DIR_IMPORT)
        if not rva:
            return []
        out = []
        o = self.rva2off(rva)
        if o is None:
            return []
        p = o
        while True:
            oft, ts, fc, name_rva, ft = struct.unpack_from("<IIIII", self.data, p)
            if not (oft or name_rva or ft):
                break
            dll = self.read_str(name_rva)
            funcs = []
            thunk_rva = oft or ft
            t = self.rva2off(thunk_rva)
            while t is not None:
                if self.pe32plus:
                    val = struct.unpack_from("<Q", self.data, t)[0]
                    step = 8
                else:
                    val = struct.unpack_from("<I", self.data, t)[0]
                    step = 4
                if not val:
                    break
                if val & (1 << (63 if self.pe32plus else 31)):
                    funcs.append("#%d" % (val & 0xFFFF))
                else:
                    # IMAGE_IMPORT_BY_NAME: WORD hint + char name
                    funcs.append(self.read_str(val + 2))
                t += step
            out.append((dll, funcs))
            p += 20
        return out

    def exports(self):
        rva, size = self.dir_entry(DIR_EXPORT)
        if not rva:
            return []
        o = self.rva2off(rva)
        if o is None:
            return []
        (_, _, _, _, name_rva, base, nfunc, nname, addr_rva, namep_rva,
         ord_rva) = struct.unpack_from("<IIHHIIIIIII", self.data, o)
        names = []
        addrs = []
        a = self.rva2off(addr_rva)
        if a is not None:
            for i in range(nfunc):
                addrs.append(struct.unpack_from("<I", self.data, a + i * 4)[0])
        npo = self.rva2off(namep_rva)
        oo = self.rva2off(ord_rva)
        if npo is not None and oo is not None:
            for i in range(nname):
                nr = struct.unpack_from("<I", self.data, npo + i * 4)[0]
                idx = struct.unpack_from("<H", self.data, oo + i * 2)[0]
                names.append((self.read_str(nr), idx, addrs[idx] if idx < len(addrs) else 0))
        return names


def main(argv):
    if len(argv) < 2:
        print(__doc__)
        return 1
    path = argv[1]
    pe = PE(path)
    flags = argv[2:]
    if "--exports" in flags:
        ex = pe.exports()
        print("%s 导出 %d 项" % (path, len(ex)))
        for name, idx, va in ex:
            print("  %-40s ord=%-4d VA=0x%X" % (name, idx, va))
        return 0

    imps = pe.imports()
    if "--json" in flags:
        print(json.dumps([{"dll": d, "funcs": f} for d, f in imps],
                         ensure_ascii=False, indent=1))
        return 0

    detail = None
    if "--detail" in flags:
        detail = flags[flags.index("--detail") + 1].lower()

    print("%s" % path)
    print("  ImageBase 0x%X  节数 %d  %s" %
          (pe.image_base, pe.nsec, "PE32+" if pe.pe32plus else "PE32"))
    print("  静态导入 %d 个 DLL:" % len(imps))
    for dll, funcs in imps:
        mark = ""
        if detail and dll.lower() == detail:
            mark = "  <== 目标"
        print("    %-22s %3d 个函数%s" % (dll, len(funcs), mark))
    if detail:
        for dll, funcs in imps:
            if dll.lower() != detail:
                continue
            print("\n  %s 的导入函数:" % dll)
            for f in funcs:
                print("    %s" % f)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
