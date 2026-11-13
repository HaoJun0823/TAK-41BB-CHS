#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""译文质量体检：专名一致性、残留英文、数字丢失。

不是替代人工校对，而是把「机器能判定的错」挡在打包之前：
  * 专名：原文出现 Aramon，译文必须出现「阿拉蒙」；
  * 残留英文：译文里出现原文没有的英文单词，多半是模型自由发挥；
  * 数字：原文的整数（版本号、伤害值、年份）应在译文中保留。

用法：
    python tools/check_quality.py --text text
    python tools/check_quality.py --text text --strict   # 有违规就退出 1
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKIP_GROUPS = {"07_icd_candidates"}

# 允许保留的英文（产品名/技术缩写），比较时统一小写
ALLOW = {
    "boneyards", "directx", "cdkey", "ta", "tak", "ok", "esc", "id", "ai",
    "tcp", "udp", "ip", "http", "https", "html", "gaf", "hpi", "tdf", "fbi",
    "windows", "microsoft", "cavedog", "bink", "mp3", "ogg", "wav", "bik",
    "dx", "d3d", "ddraw", "cpu", "ram", "mb", "kb", "gb", "fps", "ui",
}


def load_jsonl(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def word_in(term, text):
    return re.search(r"(?<![A-Za-z])" + re.escape(term) + r"(?![A-Za-z])", text,
                     re.I) is not None


def check_terms(rows, terms):
    """原文命中专名，译文必须用锁定译法。长词优先，避免 Darien 抢先匹配。"""
    ordered = sorted(terms.items(), key=lambda kv: -len(kv[0]))
    bad = []
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        en, zh = r["en"], r["zh"]
        for src, dst in ordered:
            if src == dst:
                continue
            if word_in(src, en) and not word_in(dst, zh) and dst not in zh:
                bad.append((r, "term:%s->%s" % (src, dst)))
                break
    return bad


def check_foreign_words(rows):
    """译文里出现原文没有的英文单词（长度>=4），视为可疑自由发挥。"""
    bad = []
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        en_low = r["en"].lower()
        for w in set(re.findall(r"[A-Za-z]{4,}", r["zh"])):
            lw = w.lower()
            if lw in ALLOW or lw in en_low:
                continue
            bad.append((r, "foreign:%s" % w))
            break
    return bad


def check_numbers(rows):
    """原文的整数应在译文中出现（允许年份/版本被译成中文数字的情况先不查）。"""
    bad = []
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        # 散文里常把数字写成中文（四千），只在短标签/数值串上强校验
        if len(r["zh"]) > 60:
            continue
        nums = re.findall(r"(?<![A-Za-z0-9])(\d[\d,]{0,5})(?![A-Za-z0-9])", r["en"])
        for n in {x.replace(",", "") for x in nums}:
            if n not in r["zh"].replace(",", ""):
                bad.append((r, "number:%s" % n))
                break
    return bad


def main():
    ap = argparse.ArgumentParser(description="译文质量体检")
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--terms", default=os.path.join(ROOT, "text", "terms.json"))
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--show", type=int, default=40)
    a = ap.parse_args()

    terms = {}
    if os.path.exists(a.terms):
        terms = {k: v for k, v in json.load(open(a.terms, encoding="utf-8")).items()
                 if not k.startswith("_")}

    total_bad = 0
    shown = 0
    for fn in sorted(os.listdir(a.text)):
        if not fn.endswith(".jsonl") or fn[:-6] in SKIP_GROUPS:
            continue
        rows = load_jsonl(os.path.join(a.text, fn))
        groups = [("专名", check_terms(rows, terms)),
                  ("英文残留", check_foreign_words(rows)),
                  ("数字丢失", check_numbers(rows))]
        n = sum(len(x[1]) for x in groups)
        total_bad += n
        print("%-22s done=%-5d 问题=%d  %s" % (
            fn, sum(1 for r in rows if r["status"] == "done"), n,
            "  ".join("%s:%d" % (name, len(items)) for name, items in groups)))
        for name, items in groups:
            for r, why in items:
                if shown >= a.show:
                    continue
                print("   [%s] %-30s | %s | %s" % (
                    name, r["en"][:30], r["zh"][:30], why))
                shown += 1
    print("-" * 60)
    print("合计问题 %d 条" % total_bad)
    if a.strict and total_bad:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
