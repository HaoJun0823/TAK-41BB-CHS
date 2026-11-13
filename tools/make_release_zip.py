#!/usr/bin/env python3
"""把 stage/ 目录打成发布 zip：路径干净、内容可复现、禁止源码混入。

为什么不用 zip 命令
    不同发行版的 zip 对「`.` 开头」处理不一致：有的写 "./version.dll"，
    有的写 "version.dll"。解压到游戏目录时前者会在部分解压工具里多出一层
    当前目录，玩家看到的是「解压出一堆文件，但没有 version.dll」。
    这里显式控制每一条路径，顺带固定时间戳让同一份 stage 产出同样的字节。

为什么在这里拦源码
    release 的 zip 是给玩家的成品，不该出现 src/、text/、tools/。
    一旦某天 stage 组装脚本改错、把工作区整个复制进去，这个检查会先红，
    而不是等玩家下载后发现包里带了几 MB 的 Python。

用法
    python tools/make_release_zip.py --stage stage --out dist/xxx.zip
"""

import argparse
import hashlib
import os
import sys
import zipfile

FIXED_TIME = (1980, 1, 1, 0, 0, 0)

# 这些东西一旦出现在包顶层的同名目录，就说明组装步骤串了工作区
FORBIDDEN = {".git", ".github", "src", "tools", "text", "docs", "inventory",
             "payload", "packaging", "build_out", "build_dll", "_probe",
             "TAUtil", "test"}

DEFAULT_REQUIRE = "version.dll,chs.dict,INSTALL.txt,LICENSE,NOTICE.txt"


def collect(stage):
    out = []
    for dirpath, _, files in os.walk(stage):
        for f in files:
            full = os.path.join(dirpath, f)
            rel = os.path.relpath(full, stage).replace("\\", "/")
            out.append((rel, full))
    out.sort()
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage", default="stage")
    ap.add_argument("--out", required=True)
    ap.add_argument("--require", default=DEFAULT_REQUIRE,
                    help="必须存在的顶层文件，逗号分隔")
    a = ap.parse_args()

    if not os.path.isdir(a.stage):
        sys.exit("没有 %s 目录" % a.stage)

    rows = collect(a.stage)
    if not rows:
        sys.exit("%s 是空的" % a.stage)

    leak = sorted({rel.split("/")[0] for rel, _ in rows} & FORBIDDEN)
    if leak:
        sys.exit("发布包里混进了源码/中间产物：%s" % "、".join(leak))

    have = {rel for rel, _ in rows}
    missing = [n for n in a.require.split(",") if n.strip() and n.strip() not in have]
    if missing:
        sys.exit("发布包缺文件：%s" % "、".join(missing))

    os.makedirs(os.path.dirname(os.path.abspath(a.out)) or ".", exist_ok=True)
    tmp = a.out + ".tmp"
    raw = 0
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel, full in rows:
            zi = zipfile.ZipInfo(rel, date_time=FIXED_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zi.create_system = 0
            with open(full, "rb") as f:
                data = f.read()
            raw += len(data)
            z.writestr(zi, data)
    os.replace(tmp, a.out)

    size = os.path.getsize(a.out)
    h = hashlib.sha256(open(a.out, "rb").read()).hexdigest()
    print("已写 %s" % a.out)
    print("  %d 个文件，解压后 %.1f MB，压缩后 %.1f MB"
          % (len(rows), raw / 1048576.0, size / 1048576.0))
    print("  sha256 %s" % h)
    return 0


if __name__ == "__main__":
    sys.exit(main())
