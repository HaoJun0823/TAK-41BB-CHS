#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""跨组译名统一：同一英文在不同组里只能有一个译法。

`translate.py` 在一次运行内用缓存保证一致，但以下情况仍会分叉：
  * 分组分批跑（先跑 01，隔天再跑 02）；
  * 早期人工/小样译文没进缓存；
  * 修改 terms.json 后只重译了部分组。

本脚本按 GROUP_ORDER 的优先级取第一条 `done` 译文，把后面组里的同文条目改成一致，
并输出冲突清单供人工抽查。只改 zh，不动 status/en/targets。

用法：
    python tools/unify_translations.py --text text            # 预览
    python tools/unify_translations.py --text text --write    # 落盘
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDER = ["01_ui_tdf", "02_units", "03_missions", "04_gui", "05_web", "06_icd"]


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def save(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="跨组译名统一")
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=30)
    a = ap.parse_args()

    chosen = {}      # en -> (zh, group)
    files = {}
    for code in ORDER:
        p = os.path.join(a.text, code + ".jsonl")
        if not os.path.exists(p):
            continue
        rows = load(p)
        files[code] = (p, rows)
        for r in rows:
            if r["status"] != "done" or not r["zh"]:
                continue
            chosen.setdefault(r["en"], (r["zh"], code))

    conflicts, changed = [], 0
    for code in ORDER:
        if code not in files:
            continue
        p, rows = files[code]
        dirty = False
        for r in rows:
            if r["status"] != "done" or not r["zh"]:
                continue
            zh, src = chosen[r["en"]]
            if r["zh"] != zh:
                conflicts.append((r["en"], src, zh, code, r["zh"]))
                r["zh"] = zh
                changed += 1
                dirty = True
        if dirty and a.write:
            save(p, rows)

    for en, src, keep, dst, drop in conflicts[:a.show]:
        print("%-34s %s=%s | %s=%s" % (en[:34], src, keep[:18], dst, drop[:18]))
    print("-" * 60)
    print("冲突 %d 条%s" % (len(conflicts), "，已写回" if a.write else "（预览，加 --write 落盘）"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
