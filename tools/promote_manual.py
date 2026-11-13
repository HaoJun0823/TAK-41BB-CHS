#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 `text/promote_strings.json` 里的运行时词条并入 00_manual.jsonl。

为什么进 00 组：这些串没有可写的资源位置（有的在主程序里，有的是 .ota 字段），
运行时只能靠 chs.dict 命中。00 组的语义正好是「不进资源补丁、只进字典」，
build_assets 对它是显式放行的。

只做追加，幂等：已在 00 组里的英文键跳过，id 从现有最大号往后排。

用法：
    python tools/promote_manual.py --text text            # 预览
    python tools/promote_manual.py --text text --write    # 落盘
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PH_RE = re.compile(r"%(?:[-+ #0]*\d*(?:\.\d+)?[diouxXeEfgGcs])")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    src = os.path.join(a.text, "promote_strings.json")
    with io.open(src, encoding="utf-8") as f:
        mapping = json.load(f)

    p = os.path.join(a.text, "00_manual.jsonl")
    rows = [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]
    have = {r["en"] for r in rows}
    nxt = max([int(r["id"].split("-")[1]) for r in rows if r["id"].startswith("00-")] or [0]) + 1

    added = []
    for en, zh in mapping.items():
        if en in have:
            continue
        ph = PH_RE.findall(en)
        rows.append({
            "id": "00-%04d" % nxt,
            "en": en,
            "zh": zh,
            "status": "done",
            "ctx": "运行时词条：主程序内置提示 / 地图规格，经 chs.dict 查表替换",
            "kind": "manual",
            "placeholders": ph,
            "flags": ["promoted"],
            "targets": [],
        })
        nxt += 1
        added.append((en, zh))

    print("新增 %d 条，跳过已存在 %d 条，00 组共 %d 条"
          % (len(added), len(mapping) - len(added), len(rows)))
    for en, zh in added[:8]:
        print("  %-52s -> %s" % (en[:52], zh[:40]))
    if added and not a.write:
        print("（预览模式，加 --write 落盘）")
    if a.write and added:
        tmp = p + ".tmp"
        with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        os.replace(tmp, p)
        print("已写入 %s" % p)


if __name__ == "__main__":
    sys.exit(main())
