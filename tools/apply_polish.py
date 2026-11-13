#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""C 档定稿译文重放器：长文本逐条人工润色的唯一真相源。

为什么需要它
------------
`text/*.jsonl` 是抽取产物：重跑 extract_strings.py 会把人工改的 zh 冲掉。
A/B 档已由 fixes.json / lore_terms.json 覆盖；C 档是「整段重写」，量太大，
塞进 overrides 不好核读，于是单独放 `text/polish.json`（id -> 定稿译文）。

流水线顺序（重跑抽取后按此顺序各执行一次即可恢复全部人工成果）：
    1. python tools/apply_lore_terms.py --text text --write    # 专名与术语归一
    2. python tools/apply_term_fixes.py --text text --write     # A/B 档定稿与替换
    3. python tools/apply_polish.py --text text --write         # C 档整段定稿（最后覆盖）

只改 zh 与 status；en / targets / placeholders 一律不动。
每次套用前会校验：id 必须存在、原句的 <$模板$> 与 %s/%d 必须原样保留、
不得凭空多出占位符。任何一条不过就整体中止，不写半个文件。

用法：
    python tools/apply_polish.py --merge                 # 从 _probe/polish_parts/ 合并出 text/polish.json
    python tools/apply_polish.py --text text            # 预览
    python tools/apply_polish.py --text text --write    # 落盘
    python tools/apply_polish.py --text text --check    # 只校验，不改动
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PARTS = os.path.join(ROOT, "_probe", "polish_parts")
PLACEHOLDER = re.compile(r"<\$[^>]*\$>|%[sd]")
SKIP_GROUPS = {"07_icd_candidates"}

NOTE = ("C 档定稿译文（长文本逐条人工润色）。key 为 text/*.jsonl 的稳定 id，value 为定稿 zh。"
        "只改 zh/status，其余字段不动。重跑抽取后由 tools/apply_polish.py --write 重放。"
        "按「意译优先、音译兜底」定稿，专名写法以 text/lore_terms.json 为准。")


def dump_json(path, data, compact=False):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=None if compact else 2)
        f.write("\n")


def merge():
    parts = sorted(glob.glob(os.path.join(PARTS, "*.json")))
    if not parts:
        print("没找到分片：%s" % PARTS)
        return 1
    entries, seen = {}, {}
    for p in parts:
        for k, v in json.load(open(p, encoding="utf-8")).items():
            if not v:
                continue
            if k in entries:
                print("重复 id（保留后者）：%s (%s / %s)" % (k, seen[k], os.path.basename(p)))
            entries[k] = v
            seen[k] = os.path.basename(p)
    out = os.path.join(ROOT, "text", "polish.json")
    dump_json(out, {"_note": NOTE, "entries": entries})
    print("合并 %d 个分片 -> %s，共 %d 条定稿" % (len(parts), out, len(entries)))
    return 0


def load_entries(text_dir):
    path = os.path.join(text_dir, "polish.json")
    if not os.path.exists(path):
        print("缺 %s，先跑 --merge" % path)
        sys.exit(1)
    return json.load(open(path, encoding="utf-8"))["entries"]


def collect_rows(text_dir):
    rows = {}
    files = []
    for fn in sorted(os.listdir(text_dir)):
        if not fn.endswith(".jsonl") or fn[:-6] in SKIP_GROUPS:
            continue
        p = os.path.join(text_dir, fn)
        files.append((fn, p, [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]))
        for r in files[-1][2]:
            rows.setdefault(r["id"], r)
    return files, rows


def validate(entries, rows):
    bad = []
    for k, zh in entries.items():
        if k not in rows:
            bad.append("%s: id 不存在" % k)
            continue
        en = rows[k].get("en") or ""
        need = set(PLACEHOLDER.findall(en))
        got = set(PLACEHOLDER.findall(zh))
        miss, extra = need - got, got - need
        if miss:
            bad.append("%s: 占位符丢失 %s" % (k, sorted(miss)))
        if extra:
            bad.append("%s: 占位符多出 %s" % (k, sorted(extra)))
        if not zh.strip():
            bad.append("%s: 空译文" % k)
    return bad


def main():
    ap = argparse.ArgumentParser(description="C 档定稿译文重放")
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--merge", action="store_true", help="先合并分片生成 polish.json")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true", help="只校验不改动")
    a = ap.parse_args()
    if a.merge:
        return merge()

    entries = load_entries(a.text)
    files, rows = collect_rows(a.text)
    bad = validate(entries, rows)
    if bad:
        print("校验失败 %d 条：" % len(bad))
        for b in bad[:30]:
            print("  " + b)
        return 1
    print("校验通过：%d 条定稿的 id 与占位符全部对得上" % len(entries))
    if a.check:
        return 0

    changed = 0
    for fn, p, data in files:
        dirty = False
        for r in data:
            zh = entries.get(r["id"])
            if zh is None or zh == r.get("zh"):
                continue
            old = r.get("zh") or ""
            r["zh"] = zh
            if r.get("status") != "done":
                r["status"] = "done"
            changed += 1
            dirty = True
            if changed <= 8:
                print("  [%s] %s\n      %s\n   -> %s" % (fn, r["id"], old[:70], zh[:70]))
        if dirty and a.write:
            with open(p, "w", encoding="utf-8", newline="\n") as f:
                for r in data:
                    f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    print("---- %s %d 条 ----" % ("已写入" if a.write else "将改动", changed))
    if changed and not a.write:
        print("（预览模式，加 --write 落盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
