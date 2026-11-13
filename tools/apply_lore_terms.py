#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按 text/lore_terms.json 统一专名与术语，只改 zh。

三层保护，避免把已经对的东西改坏：
  1. `<$...$>` 服务器模板片段原样保留（翻它会破坏 Boneyards 模板）；
  2. 定稿中文（canonical 的 value）先占位，避免 `图诺` 把 `图诺尔` 改成 `图诺尔尔`；
  3. 技术/品牌白名单（DirectX、DLL、URL…）不做英文替换。

顺序：短语（Fair Abiad 这类多词）→ 变体（纳尔默→纳默尔）→ 英文原词（Narmer→纳默尔）。

用法：
    python tools/apply_lore_terms.py --text text            # 预览
    python tools/apply_lore_terms.py --text text --write    # 落盘
"""

from __future__ import annotations

import argparse
import collections
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUPS = ["01_ui_tdf", "02_units", "03_missions", "04_gui", "05_web", "06_icd"]

# 不该被英文替换的技术/品牌词：留在中文里反而更准确
KEEP_EN = {
    "Boneyards", "TA:K", "TAK", "DirectX", "Windows", "DirectDraw", "Direct3D",
    "DirectPlay", "Glide", "Voodoo", "OpenGL", "DLL", "URL", "INI", "ESC", "MSG",
    "Mplayer.com", "Cavedog", "Smacker", "JPEG", "PNG", "GDI", "HAPI", "MMX",
    "DCT", "IHDR", "PLTE", "bKGD", "cHRM", "gAMA", "hIST", "oFFs", "pCAL",
    "pHYs", "sBIT", "sRGB", "tIME", "tRNS", "zTXt", "iTXt", "IDAT", "IEND",
    "CD-ROM", "CDKey", "TDF", "XMS", "EMS", "VTune", "DAC", "VTOL", "SOS",
    "Huffman", "Adobe", "Macromedia", "Shockwave", "Flash", "Beta",
}

PLACEHOLDER = re.compile(r"<\$.*?\$>", re.S)


def load_terms(path):
    with io.open(path, encoding="utf-8") as f:
        d = json.load(f)
    return d.get("canonical", {}), d.get("variants", {}), d.get("phrases", {})


def protect(s, store):
    """把模板片段与定稿中文换成占位符，返回 (新串, 还原函数用的列表)。"""
    def stash(text):
        store.append(text)
        return "\x01%d\x02" % (len(store) - 1)

    s = PLACEHOLDER.sub(lambda m: stash(m.group(0)), s)
    return s


def apply_one(zh, canon, variants, phrases, stats):
    store = []
    s = protect(zh, store)
    # 定稿中文也保护起来，防止变体替换误伤
    for zh_form in sorted({v for v in canon.values() if v}, key=len, reverse=True):
        if zh_form in s:
            store.append(zh_form)
            s = s.replace(zh_form, "\x01%d\x02" % (len(store) - 1))

    # 多词短语优先，避免 Fair Abiad 只换了 Abiad
    for en, zh_form in sorted(phrases.items(), key=lambda kv: -len(kv[0])):
        pat = re.compile(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(en))
        def rep(m):
            stats[en] += 1
            return zh_form
        s = pat.sub(rep, s)
    for en, zh_form in sorted(canon.items(), key=lambda kv: -len(kv[0])):
        if en in KEEP_EN:
            continue
        pat = re.compile(r"(?<![A-Za-z])%s(?![A-Za-z])" % re.escape(en))
        def rep(m, en=en):
            stats[en] += 1
            return zh_form
        s = pat.sub(rep, s)

    # 变体中文：单遍替换，长的优先。
    # 不能逐条 str.replace——替换结果会被后一条规则再次命中（「战争房间」→「作战室」，
    # 随后「战室」又命中「作战室」里的子串，产出「作作战室」）。一次扫描只查原文即可。
    if variants:
        alt = "|".join(re.escape(k) for k in sorted(variants, key=len, reverse=True))

        def vrep(m):
            bad = m.group(0)
            stats["变体:" + bad] += 1
            return variants[bad]

        s = re.sub(alt, vrep, s)

    # 中文之间的多余空格收掉（替换英文名后常留下「铁砧岩 是」这种缝）
    s = re.sub(r"(?<=[\u4e00-\u9fff])[ \t]+(?=[\u4e00-\u9fff])", "", s)

    # 「塔卡瓦瓦（塔卡瓦瓦）」这类英文名替换后出现的重复括注
    s = re.sub(r"([\u4e00-\u9fff]{2,10})（\1）", r"\1", s)

    # 还原
    def restore(m):
        return store[int(m.group(1))]
    s = re.sub(r"\x01(\d+)\x02", restore, s)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=12)
    a = ap.parse_args()

    canon, variants, phrases = load_terms(os.path.join(a.text, "lore_terms.json"))
    stats = collections.Counter()
    changed = []
    for grp in GROUPS:
        p = os.path.join(a.text, grp + ".jsonl")
        if not os.path.exists(p):
            continue
        rows = [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]
        touched = 0
        for r in rows:
            if r.get("status") != "done" or not r.get("zh"):
                continue
            new = apply_one(r["zh"], canon, variants, phrases, stats)
            if new != r["zh"]:
                r["zh"] = new
                touched += 1
                if len(changed) < a.show:
                    changed.append((grp, r["en"][:46], new[:70]))
        if touched:
            print("%-14s 改动 %d 条" % (grp, touched))
        if a.write:
            tmp = p + ".tmp"
            with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
            os.replace(tmp, p)

    print("\n替换计数（前 20）：")
    for k, v in stats.most_common(20):
        print("  %-16s %d" % (k, v))
    print("\n示例：")
    for g, en, zh in changed:
        print("  [%s] %s\n      -> %s" % (g, en, zh))
    if not a.write:
        print("\n（预览模式，加 --write 落盘）")


if __name__ == "__main__":
    sys.exit(main())
