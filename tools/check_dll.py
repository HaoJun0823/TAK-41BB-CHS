#!/usr/bin/env python3
"""发布前门禁：检查 version.dll 是不是「能装进游戏」的那一份。

为什么需要这个脚本
    version.dll 顶在系统 DLL 的位置上，靠 17 个裸跳板把调用转给
    %SystemRoot%\\SysWOW64\\version.dll。这里任何一条不对，玩家那边看到的
    都是「游戏双击没反应」或「找不到入口点」，而且发生在他们机器上、没有
    日志。所以把能静态验证的东西全部放在 CI 里验：

      1. PE32 / i386 —— 64 位或 ARM 产物根本不会被 32 位游戏加载；
      2. 导出表与 version.def 逐条对齐（名字 + 序号），不多不少；
      3. 没有导出转发 —— .def 写成 x=模块.y 会让 loader 又找到自己，无限递归；
      4. 每个导出落在可执行节里 —— 指向数据区的跳板一调用就崩；
      5. linker 版本是 14.16 (v141) —— msvc-wine 若静默回退新版工具集，
         产物会依赖新版 CRT，Windows 98/XP 上加载失败；
      6. 不静态导入 version.dll —— 那等于自己导入自己。

用法
    python tools/check_dll.py --dll build_dll/version.dll
    python tools/check_dll.py --dll dist/version.dll --def version.def
"""

import argparse
import hashlib
import os
import re
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pe_imports import PE  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_FILE_MACHINE_I386 = 0x014C
IMAGE_SCN_MEM_EXECUTE = 0x20000000
DIR_EXPORT = 0


def parse_def(path):
    """-> [(name, ordinal)]，按 .def 里出现的顺序。"""
    rows = []
    pat = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*chs_px_\w+\s*@(\d+)\s*$")
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split(";", 1)[0]
            m = pat.match(line)
            if m:
                rows.append((m.group(1), int(m.group(2))))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dll", default=os.path.join(ROOT, "build_dll", "version.dll"))
    ap.add_argument("--def", dest="deffile", default=os.path.join(ROOT, "version.def"))
    ap.add_argument("--linker", default="14.16",
                    help="要求的 linker 版本，默认 14.16 (v141)")
    ap.add_argument("--allow-any-linker", action="store_true",
                    help="跳过 linker 版本检查（仅本地调试用）")
    a = ap.parse_args()

    errs = []

    def bad(msg):
        errs.append(msg)
        print("  [错误] " + msg)

    if not os.path.isfile(a.dll):
        print("!! 找不到 %s" % a.dll)
        return 1
    if not os.path.isfile(a.deffile):
        print("!! 找不到 %s" % a.deffile)
        return 1

    want = parse_def(a.deffile)
    if not want:
        print("!! %s 里没解析到 chs_px_ 跳板条目" % a.deffile)
        return 1

    pe = PE(a.dll)
    size = os.path.getsize(a.dll)
    sha = hashlib.sha256(open(a.dll, "rb").read()).hexdigest()
    print("%s  %d 字节  sha256 %s…" % (a.dll, size, sha[:16]))
    print("  期望导出 %d 项" % len(want))

    # 1. 架构
    if pe.machine != IMAGE_FILE_MACHINE_I386:
        bad("machine=0x%04X，不是 i386 —— 32 位游戏不会加载" % pe.machine)
    if pe.pe32plus:
        bad("是 PE32+（64 位），游戏是 32 位进程")

    # 5. linker 版本（PE32 可选头偏移 +2/+3）
    lv = (pe.data[pe.opt + 2], pe.data[pe.opt + 3])
    lv_s = "%d.%d" % lv
    print("  linker %s  节数 %d" % (lv_s, pe.nsec))
    if not a.allow_any_linker:
        wl = tuple(int(x) for x in a.linker.split("."))
        if lv != wl:
            bad("linker %s ≠ %s —— 工具集可能被回退，产物会依赖新版 CRT"
                % (lv_s, a.linker))

    # 2/3/4. 导出表
    ex = pe.exports()
    got = {name: (idx, va) for name, idx, va in ex}
    print("  实际导出 %d 项" % len(ex))
    if len(ex) != len(want):
        bad("导出数量 %d ≠ .def 的 %d" % (len(ex), len(want)))

    dir_rva, dir_size = pe.dir_entry(DIR_EXPORT)
    base = struct.unpack_from("<I", pe.data, pe.rva2off(dir_rva) + 16)[0] \
        if dir_rva and pe.rva2off(dir_rva) is not None else 1

    for name, ordinal in want:
        if name not in got:
            bad("缺少导出 %s" % name)
            continue
        idx, va = got[name]
        if base + idx != ordinal:
            bad("%s 序号是 %d，.def 要求 %d" % (name, base + idx, ordinal))
        if dir_rva and dir_rva <= va < dir_rva + dir_size:
            bad("%s 是转发导出（RVA 落在导出目录内），会递归回自己" % name)
            continue
        off = pe.rva2off(va)
        if off is None:
            bad("%s 的 RVA 0x%X 不在任何节里" % (name, va))
            continue
        # 导出必须是代码。本工程只把这些跳板放在 .text；若哪天被挪到数据区，
        # 一调用就崩，这里提前提示。
        sec = next((s for s in pe.sections if s[1] <= va < s[1] + s[2]), None)
        if sec is None or sec[0] != ".text":
            print("  [提示] %s 位于节 %s（不在 .text）"
                  % (name, sec[0] if sec else "?"))

    extra = sorted(set(got) - {n for n, _ in want})
    if extra:
        bad("多出未在 .def 声明的导出：%s" % "、".join(extra))

    # 6. 不能静态导入自己
    try:
        for dll, _funcs in pe.imports():
            if dll.lower() == "version.dll":
                bad("静态导入了 version.dll —— 代理会导入自己")
    except Exception as e:  # 解析失败不应让门禁误报通过
        bad("导入表解析失败：%s" % e)

    if errs:
        print("DLL 校验失败（%d 项）" % len(errs))
        return 1
    print("DLL 校验通过：PE32/i386，导出 %d 项与 .def 一致，linker %s"
          % (len(want), lv_s))
    return 0


if __name__ == "__main__":
    sys.exit(main())
