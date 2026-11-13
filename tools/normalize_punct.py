#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""中文标点规范化：把紧贴汉字的半角标点换成全角，`...` 换成 `……`。

先保护再替换，避免改坏不该改的东西：
  * `<$...$>` 服务器模板；
  * URL / 文件名 / 版本号（`www.cavedog.com`、`chs.dict`、`2.0`）；
  * 格式串里的 `%d:%d`（冒号两侧是格式符，不是中文）。

用法：
    python tools/normalize_punct.py --text text            # 预览
    python tools/normalize_punct.py --text text --write    # 落盘
"""

from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GROUPS = ["00_manual", "01_ui_tdf", "02_units", "03_missions", "04_gui", "05_web", "06_icd"]

CJK = r"\u4e00-\u9fff"
MASK = re.compile(
    r"<\$.*?\$>"                                   # 模板
    r"|(?:https?://|www\.)[^\s，。；]+"              # URL
    r"|\b[\w-]+\.(?:dll|exe|hpi|tdf|fbi|txt|ini|com|net|org|html?|gaf|bik)\b"
    r"|%[-+ #0]*\d*(?:\.\d+)?[diouxXeEfgGcs]"      # 格式符
    r"|\b\d+\.\d+\b",                              # 版本号/小数
    re.I,
)

PAIRS = {",": "，", ";": "；", ":": "：", "!": "！", "?": "？"}


def fix(s):
    store = []

    def stash(m):
        store.append(m.group(0))
        return "\x01%d\x02" % (len(store) - 1)

    s = MASK.sub(stash, s)
    # 列表符号统一：原版在 • / ● / · 之间混用，中文里统一成居中点
    s = re.sub(r"^\s*[●•·]\s*", "· ", s)
    # 直引号成对换成「」：界面串里常留着 "搜索"、'%s' 这种半角引号
    for _ in range(3):
        t = re.sub(r'"([^"\n]{1,40})"', "「\\1」", s)
        t = re.sub(r"'([^'\n]{1,40})'", "「\\1」", t)
        if t == s:
            break
        s = t
    # 弯引号统一成「」：全文只留一种引号，界面元素与叙述引语都不混用
    s = s.replace("“", "「").replace("”", "」")
    s = re.sub(r"\.{3,}", "……", s)
    # 汉字后面的半角标点
    s = re.sub(r"(?<=[%s])\s*([,;:!?])" % CJK, lambda m: PAIRS[m.group(1)], s)
    # 汉字前面的半角标点
    s = re.sub(r"([,;:!?])(?=[%s])" % CJK, lambda m: PAIRS[m.group(1)], s)
    # 句号：只在贴着汉字、且不是小数/扩展名时换
    s = re.sub(r"(?<=[%s])\s*\.(?![0-9A-Za-z])" % CJK, "。", s)
    s = re.sub(r"(?<![0-9A-Za-z])\.(?=[%s])" % CJK, "。", s)
    # 全角标点前后的空格
    s = re.sub(r"\s+(?=[，。；：！？])", "", s)
    s = re.sub(r"(?<=[，。；：！？])[ \t]+", "", s)
    # 引号与汉字之间的空格（英文词两侧保留）
    s = re.sub(r"(?<=[%s])[ \t]+(?=「)" % CJK, "", s)
    s = re.sub(r"(?<=」)[ \t]+(?=[%s])" % CJK, "", s)

    s = re.sub(r"\x01(\d+)\x02", lambda m: store[int(m.group(1))], s)
    return s


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=10)
    a = ap.parse_args()

    total = 0
    for grp in GROUPS:
        p = os.path.join(a.text, grp + ".jsonl")
        if not os.path.exists(p):
            continue
        rows = [json.loads(l) for l in io.open(p, encoding="utf-8") if l.strip()]
        n = 0
        samples = []
        for r in rows:
            if r.get("status") != "done" or not r.get("zh"):
                continue
            new = fix(r["zh"])
            if new != r["zh"]:
                if len(samples) < a.show:
                    samples.append((r["zh"][:70], new[:70]))
                r["zh"] = new
                n += 1
        if n:
            print("%-14s 改动 %d 条" % (grp, n))
            for old, new in samples[:3]:
                print("   旧: %s\n   新: %s" % (old, new))
            total += n
        if a.write and n:
            tmp = p + ".tmp"
            with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
            os.replace(tmp, p)
    print("合计 %d 条" % total)
    if total and not a.write:
        print("（预览模式，加 --write 落盘）")


if __name__ == "__main__":
    sys.exit(main())
