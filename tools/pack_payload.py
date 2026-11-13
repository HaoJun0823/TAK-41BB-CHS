#!/usr/bin/env python3
"""把资源层打成随仓库走的 payload/chs_res.zip。

为什么要有这个包
    CI runner 上不可能有原版游戏（版权 + 体积），build_assets 因此只能产出
    chs.dict / patches.json 这类纯文本资产，产不出 build_out/update/。
    而 release 要交给玩家一个解压即用的完整补丁，资源层必须随身携带 ——
    所以 update/ 里改写后的文件先在本机打成 zip 入库，CI 只负责解压。

为什么不在 release 时现场生成
    生成 update/ 要读 12 个原版 HPI；把 HPI 传上 GitHub 既过不了 100MB
    单文件限制，也涉及再分发原版资源。

用法
    python tools/pack_payload.py --assets build_out --out payload/chs_res.zip
    python tools/pack_payload.py --check     # 只对比现有包是否已过期

可复现
    zip 内每条记录的时间戳固定为 1980-01-01，压缩参数固定，同样的 update/
    目录产出逐字节相同的 zip。否则每次重建都会在 git 里留一个无法审查的
    二进制 diff，看不出究竟改了哪几个文件。

挑选规则不在这里
    哪些文件该进 chs_res 由 deploy.iter_res_files 决定，部署与打包共用同一
    个函数 —— 否则「本机实测通过的包」和「发布出去的包」可能不是同一份。
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile
import zlib

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import deploy  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIXED_TIME = (1980, 1, 1, 0, 0, 0)
PREFIX = "chs_res/"


def collect(assets):
    """返回 [(rel, full, hpdir, ambiguous)]，按 rel 排序。"""
    return deploy.iter_res_files(assets)


def entries_digest(rows):
    """所有 (相对路径, 内容) 的聚合摘要，与 zip 的压缩实现无关。

    校验方（tools/verify_payload.py）解压后按同样顺序重算这个值，就能判断
    「包里的东西」和「打包时记录的」是不是同一批字节 —— 比直接比 zip 文件的
    sha256 稳，不受压缩库版本影响。
    """
    h = hashlib.sha256()
    for rel, full, _, _ in rows:
        h.update(rel.lower().encode("utf-8"))
        h.update(b"\0")
        with open(full, "rb") as f:
            h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()


def text_digest(text_dir):
    """明文译文层的聚合摘要，用来判断 payload 是否已落后于 text/。

    chs_res 里的每个文件都是由 text/*.jsonl 改写原版资源得到的。若 text 变了
    而 payload 没重打，发布出去的包就是「资源层旧、字典新」的混合体 ——
    这类不一致在游戏里表现为同一个词在两处翻译不同，极难排查。
    """
    h = hashlib.sha256()
    for name in sorted(os.listdir(text_dir)):
        if not name.endswith((".jsonl", ".json")):
            continue
        p = os.path.join(text_dir, name)
        if not os.path.isfile(p):
            continue
        h.update(name.encode("utf-8"))
        h.update(b"\0")
        with open(p, "rb") as f:
            h.update(hashlib.sha256(f.read()).digest())
    return h.hexdigest()


def write_zip(rows, out):
    os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
    tmp = out + ".tmp"
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
        for rel, full, _, _ in rows:
            zi = zipfile.ZipInfo(PREFIX + rel, date_time=FIXED_TIME)
            zi.compress_type = zipfile.ZIP_DEFLATED
            zi.external_attr = 0o644 << 16
            zi.create_system = 0
            with open(full, "rb") as f:
                z.writestr(zi, f.read())
    os.replace(tmp, out)


def read_zip(path):
    """返回 {相对路径: crc32}，用于 --check 对比。"""
    with zipfile.ZipFile(path) as z:
        return {x.filename: x.CRC for x in z.infolist() if not x.is_dir()}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--assets", default=os.path.join(ROOT, "build_out"))
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--out", default=os.path.join(ROOT, "payload", "chs_res.zip"))
    ap.add_argument("--check", action="store_true",
                    help="不写文件，只报告现有包与 update/ 的差异")
    a = ap.parse_args()

    if not os.path.isdir(os.path.join(a.assets, "update")):
        sys.exit("找不到 %s/update —— 先带 --game 跑一次 build_assets.py"
                 % a.assets)

    rows = collect(a.assets)
    if not rows:
        sys.exit("%s/update 里没有可打包的资源" % a.assets)

    raw = sum(os.path.getsize(f) for _, f, _, _ in rows)
    amb = sum(1 for _, _, _, x in rows if x)

    if a.check:
        if not os.path.isfile(a.out):
            sys.exit("还没有 %s，先跑一次不带 --check 的" % a.out)
        old = read_zip(a.out)
        new = {PREFIX + rel: zlib.crc32(open(full, "rb").read()) & 0xFFFFFFFF
               for rel, full, _, _ in rows}
        add = sorted(set(new) - set(old))
        rm = sorted(set(old) - set(new))
        mod = sorted(k for k in set(old) & set(new) if old[k] != new[k])
        if not (add or rm or mod):
            print("payload 是最新的：%d 个文件逐条一致" % len(new))
            return 0
        for x in add[:20]:
            print("  + " + x)
        for x in rm[:20]:
            print("  - " + x)
        for x in mod[:20]:
            print("  ~ " + x)
        if max(len(add), len(rm), len(mod)) > 20:
            print("  …")
        print("payload 已过期：+%d / -%d / ~%d，请重新生成"
              % (len(add), len(rm), len(mod)))
        return 1

    write_zip(rows, a.out)

    meta = {
        "note": "由 tools/pack_payload.py 生成；不要手改。",
        "prefix": PREFIX.rstrip("/"),
        "files": len(rows),
        "raw_bytes": raw,
        "zip_bytes": os.path.getsize(a.out),
        "entries_sha256": entries_digest(rows),
        "text_sha256": text_digest(a.text),
        "ambiguous": amb,
    }
    mp = os.path.splitext(a.out)[0] + ".json"
    with open(mp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("已写 %s" % a.out)
    print("  %d 个文件，原始 %.1f MB，压缩后 %.1f MB（多版本按优先级择一 %d 个）"
          % (len(rows), raw / 1048576.0,
             os.path.getsize(a.out) / 1048576.0, amb))
    print("  entries_sha256 %s" % meta["entries_sha256"][:16])
    print("  记录 %s" % mp)
    return 0


if __name__ == "__main__":
    sys.exit(main())
