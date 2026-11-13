#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把人工定稿的译法套到 text/*.jsonl 上（来源：text/fixes.json）。

为什么单独做成脚本：jsonl 是抽取产物，重跑抽取会覆盖人工改动。定稿译法
集中放在 fixes.json，任何时候重放一遍就能恢复到定稿状态，且改动可 review。

只改 zh，不动 en/status/targets；`status=skip` 的行不动。

用法：
    python tools/apply_term_fixes.py --text text            # 预览
    python tools/apply_term_fixes.py --text text --write    # 落盘
"""

from __future__ import annotations

import argparse
import json
import os

SKIP_GROUPS = {"07_icd_candidates"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default="text")
    ap.add_argument("--write", action="store_true")
    args = ap.parse_args()

    fixes_path = os.path.join(args.text, "fixes.json")
    with open(fixes_path, encoding="utf-8") as f:
        data = json.load(f)
    overrides = data.get("overrides", {})
    id_overrides = data.get("id_overrides", {})

    changed = 0
    hit = set()
    for fn in sorted(os.listdir(args.text)):
        if not fn.endswith(".jsonl"):
            continue
        group = fn[:-6]
        if group in SKIP_GROUPS:
            continue
        path = os.path.join(args.text, fn)
        with open(path, encoding="utf-8") as f:
            rows = [json.loads(l) for l in f if l.strip()]

        dirty = False
        for r in rows:
            en = (r.get("en") or "").strip()
            new = None
            if r.get("id") in id_overrides:
                new = id_overrides[r["id"]]
            elif en in overrides and r.get("status") != "skip":
                new = overrides[en]
            if new is None:
                continue
            old = r.get("zh") or ""
            if old != new:
                print("%-22s %-26r %r -> %r" % (group, en, old, new))
                r["zh"] = new
                r["status"] = "done"
                changed += 1
                dirty = True
                hit.add(en)

        if dirty and args.write:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
                    f.write("\n")

    miss = sorted(set(overrides) - hit)
    print("---- 改动 %d 处，涉及 %d 个英文键 ----" % (changed, len(hit)))
    if miss:
        print("未在清单中出现的键（可能拼写不同或未抽取）：")
        for m in miss:
            print("   %r" % m)
    if changed and not args.write:
        print("（预览模式，未落盘；加 --write 生效）")


if __name__ == "__main__":
    main()
