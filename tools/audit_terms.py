#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单位名漂移审计：原文提到某单位，译文里却用了别的叫法。

为什么需要它
------------
`02_units.jsonl` 里每个单位只有一种译名，但任务简报、网页、帮助文本是
另外送的翻译批次。同一句里出现 `Trebuchet`，02 组叫「投石车」，03 组可能
写成「投石机」；`Harpy` 在单位表里是「鹰女妖」，到了地图简介里变成「夜枭」。
玩家会以为有两种东西。

这里以 02 组为基础名册，扫 01/03/04/05/06 各组，把「原文有该名、译文里
没有对应译名」的条目列出来，供人工判断——不自动改，因为同一个词在英文里
也可能只是普通名词。

用法：
    python tools/audit_terms.py --text text
    python tools/audit_terms.py --text text --group 03
"""

from __future__ import annotations

import argparse
import glob
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 这些英文本身是普通词，出现在句子里不代表在说单位，扫出来全是噪音
STOP = {
    "wall", "house", "temple", "tower", "gate", "tree", "rock", "well", "road",
    "bridge", "fire", "water", "ship", "boat", "keep", "camp", "fort", "farm",
    "mine", "mill", "shop", "lamp", "bed", "rug", "fence", "crop", "crops",
    "bones", "animal", "monster", "debris", "wreckage", "groundcover", "ivy",
    "lettuce", "pumpkins", "bird", "die", "waves", "arms", "bolt", "sword",
    "dagger", "fireball", "lightbeam", "taser", "flail", "skiff", "spire",
    "hovel", "villa", "palace", "embassy", "barracks", "granary", "smithy",
    "refinery", "shed", "manor", "stable", "senate", "tomb", "plant", "cow",
    "tiger", "boar", "wolf", "prisoner", "hunter", "huntress", "beggar",
    "farmer", "peasant", "warrior", "archer", "priest", "priestess", "witch",
    "shaman", "mechanic", "builder", "engineer", "trooper", "soldier",
    "sage", "mission", "gibberish", "spooky", "wreckage", "lighthouse",
}


def load(path):
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def build_roster(text_dir):
    roster = {}
    for r in load(os.path.join(text_dir, "02_units.jsonl")):
        if r["status"] != "done" or not r["zh"]:
            continue
        en = r["en"].strip()
        if en.lower() in STOP:
            continue
        # 只收长度 >= 4 的名字，避免 "Ark"、"Cow" 这类短词误伤
        if len(en) < 4:
            continue
        roster[en] = r["zh"]
    return roster


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--group", default="")
    ap.add_argument("--limit", type=int, default=60)
    a = ap.parse_args()

    roster = build_roster(a.text)
    # 长名优先，避免 "Trebuchet" 抢在 "Trebuchet Ship" 前面
    names = sorted(roster, key=len, reverse=True)
    pats = []
    for n in names:
        rx = re.compile(r"(?<![A-Za-z])" + re.escape(n) + r"(?![A-Za-z])")
        pats.append((n, rx, roster[n]))

    files = sorted(glob.glob(os.path.join(a.text, "0[1-6]*.jsonl")))
    hits = []
    for p in files:
        g = os.path.basename(p)[:2]
        if a.group and g != a.group:
            continue
        for r in load(p):
            if r["status"] != "done" or not r["zh"]:
                continue
            en, zh = r["en"], r["zh"]
            used = []
            for name, pat, want in pats:
                if not pat.search(en):
                    continue
                # 已被更长的名字覆盖
                if any(name in u[0] and name != u[0] for u in used):
                    continue
                used.append((name, want))
                if want not in zh:
                    hits.append((g, r["id"], name, want, en, zh))
                    break

    print("名册 %d 条，可疑漂移 %d 条" % (len(roster), len(hits)))
    for g, rid, name, want, en, zh in hits[:a.limit]:
        print("\n[%s] %s  期望「%s」" % (g, rid, want))
        print("  E: %s" % en[:110])
        print("  Z: %s" % zh[:110])
    return 0


if __name__ == "__main__":
    sys.exit(main())
