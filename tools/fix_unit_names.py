#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单位名归一：按「词缀 + 基础名」重算 02 组里的状态变体。

为什么需要它
------------
`02_units.jsonl` 是抽取产物，同一件东西的变体在原文里是机械拼出来的：

    Troll / Stone Troll / Frozen Troll / Destroyed Troll ...

但送翻时每条独立成句，模型对每个词缀各译各的，于是同一件石像会在
建造菜单里叫「石像鬼」，在战场上叫「石巨魔」，被毁之后又叫「摧毁的巨魔」。
玩家会以为是三种东西。更糟的是同一批名字在 01 组（引擎词条表）里还有
另一套译法，比如 `Drake Carcass` 一边是「幼龙残骸」、一边是「龙虾尸体」。

所以这里不按 id 改，而是**按英文原名**写进 `fixes.json` 的 overrides：
凡是同一串英文，不管出现在 01 还是 02，一律用同一个译名。

用法：
    python tools/fix_unit_names.py --text text            # 只报告
    python tools/fix_unit_names.py --text text --write    # 写 text/fixes.json
"""

from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 词缀 -> 中文前缀。顺序有意义：先长后短，避免 "Stone Dark Mason" 被 "Stone " 抢先。
PREFIX = [
    ("Stone ", "石"),
    ("Frozen ", "冰封"),
    ("Damaged ", "受损"),
    ("Destroyed ", "被毁"),
    ("Ruined ", "残破"),
    ("Sacked ", "被洗劫的"),
    ("Abandoned ", "废弃的"),
]

# 后缀 -> 中文后缀（尸体类）。和前缀一样是机械拼出来的。
SUFFIX = [
    (" Corpse", "尸体"),
    (" Carcass", "残骸"),
]

# 基础名定稿（只列需要改的）。基础名一改，所有变体跟着变。
BASE_FIX = {
    "Bottle Wagon": "运瓶车",
    "Fire Demon": "火魔",
    "Mer Warrior": "人鱼战士",
    "Cabal": "密会",
    "Fire Mage": "火法师",
    "Beast Rider": "兽骑兵",
    "Drake": "幼龙",
    "Joreth": "乔雷斯",
    "Wisp": "鬼火",
    "Buck": "雄鹿",
    "Serf": "农奴",
    "Vagrant": "流浪者",
    # 建造菜单里的 House 是民房，不是 Boneyards 的「家族」；Wall 补成墙壁
    "House": "房屋",
    "Wall": "墙壁",
}

# 没有独立基础名条目的（只以变体形式出现），直接给出基础译名。
EXTRA_BASE = {
    "Builder": "建造者",
    "Tribesman": "部落战士",
    "Handler": "驭兽人",
    "Witch": "女巫",
    "Priest": "祭司",
    "Skeleton": "骷髅",
    "Orm": "奥姆",
    "Heket": "赫凯特",
    "Leim": "莱姆",
    "Solan Rixx": "索兰·里克斯",
    "Buriash": "布里亚什",
    "Emen": "埃门",
    "Dernhest": "德恩赫斯特",
    "Tiger": "虎",
    "Boar": "野猪",
    "Amazon": "亚马逊",
    "Barracuda": "梭鱼",
    "Tribal Wolf": "部落狼",
    "Tomb": "墓穴",
    "Crops": "农田",
    "Generator": "发电机",
    "Giant Carcass": "巨人残骸",
    "Giant": "巨人",
    "Orc": "兽人",
    "Engineer": "工程师",
    "Trooper": "士兵",
    "Dragon": "龙",
    "Seagull": "海鸥",
    "Avatar": "化身",
    "Spawn": "后裔",
    "Merchant": "商人",
    "Spirit": "幽灵",
    "Crow": "乌鸦",
    "Non-human": "非人生物",
    "Ship Eater": "噬船兽",
    "Kandran": "坎德兰人",
    "Nyrian": "尼里安人",
    "Spider": "蜘蛛",
    "Satyr": "萨梯",
    "Zhonian": "佐恩人",
    "Demon": "恶魔",
    "Factory": "工厂",
    "Livestock": "牲畜",
    "Baths": "浴场",
    "Mage": "法师",
    "Helagrin": "赫拉格林",
}

# 机械规则算出来别扭的个例
MANUAL = {
    "Stone Wall": "石墙",
    "Priest of Lihr Corpse": "里尔祭司尸体",
    "Angel of Lihr Corpse": "里尔天使尸体",
}


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def build_base(rows):
    base = dict(EXTRA_BASE)
    for r in rows:
        if r["status"] == "done" and r["zh"]:
            base[r["en"]] = BASE_FIX.get(r["en"], r["zh"])
    return base


def expected(name, base):
    """按词缀表算期望译名；算不出返回 None。"""
    if name in MANUAL:
        return MANUAL[name]
    for suf, zh in SUFFIX:
        if name.endswith(suf):
            stem = name[: -len(suf)]
            b = base.get(stem)
            if not b:
                return None
            if b.endswith(zh):
                return b
            return b + zh
    for pre, zh in PREFIX:
        if name.startswith(pre):
            stem = name[len(pre):]
            b = base.get(stem)
            if not b:
                return None
            # 基础名本身已含该前缀字（石像鬼），不再叠一层
            if b.startswith(zh):
                return b
            return zh + b
    return base.get(name)


def load_all(text_dir):
    """所有分组的 en -> [(组号, id, 现译)]。"""
    seen = {}
    for fn in sorted(os.listdir(text_dir)):
        if not fn.endswith(".jsonl") or fn.startswith("07"):
            continue
        g = fn[:2]
        for r in load(os.path.join(text_dir, fn)):
            if r["status"] != "done" or not r["zh"]:
                continue
            seen.setdefault(r["en"], []).append((g, r["id"], r["zh"]))
    return seen


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    rows = load(os.path.join(a.text, "02_units.jsonl"))
    base = build_base(rows)
    seen = load_all(a.text)

    out, unknown = {}, []
    for en, occ in seen.items():
        exp = expected(en, base)
        if exp is None:
            if any(en.startswith(p) for p, _ in PREFIX) or any(en.endswith(s) for s, _ in SUFFIX):
                unknown.append(en)
            continue
        if any(zh != exp for _, _, zh in occ):
            out[en] = exp
    for en, zh in BASE_FIX.items():
        if en in seen:
            out[en] = zh

    print("英文名 %d 条，需统一 %d 条，无法推导 %d 条" % (len(seen), len(out), len(unknown)))
    for en, zh in out.items():
        cur = seen[en][0][2]
        print("  %-34s %-14s -> %s  (%s)" % (en, cur, zh, ",".join(g for g, _, _ in seen[en])))
    for en in unknown:
        print("  [缺基础名] %s" % en)

    if a.write:
        p = os.path.join(a.text, "fixes.json")
        doc = json.load(open(p, encoding="utf-8"))
        ov = doc.setdefault("overrides", {})
        n0 = len(ov)
        ov.update(out)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            json.dump(doc, f, ensure_ascii=False, indent=2)
            f.write("\n")

        # C 档里若已有同名 id 的旧条目，一并撤掉，避免两处定稿打架
        pp = os.path.join(a.text, "polish.json")
        pd = json.load(open(pp, encoding="utf-8"))
        en_of = {r["id"]: r["en"] for r in rows}
        drop = [k for k in pd["entries"] if en_of.get(k) in out]
        for k in drop:
            pd["entries"].pop(k, None)
        if drop:
            with open(pp, "w", encoding="utf-8", newline="\n") as f:
                json.dump(pd, f, ensure_ascii=False, indent=2)
                f.write("\n")
        print("已写入 %s：overrides %d -> %d；清掉 C 档重复 %d 条"
              % (p, n0, len(ov), len(drop)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
