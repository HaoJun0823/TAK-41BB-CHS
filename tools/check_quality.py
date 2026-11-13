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
    "directplay", "directdraw", "direct3d", "glide", "voodoo", "opengl",
    "dll", "url", "ini", "exe", "hpi", "mmx", "huffman", "zlib", "libpng",
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


# 常见英文虚词/动词：正常中译里不可能连用两个。用于抓「半译」——
# 整段英文被照抄进译文，而 check_foreign_words 查不出来（因为原文里也有）。
COMMON_EN = set("""the a an this that these those is are was were be been being
you your yours we our ours they their them it its he she his her
to of in on at for with from by as and or but not no yes if then than
will would can could should shall may might must do does did done have has had
please press click select enter file files game player players new old
one two three all any some more most other such only also very""".split())


def check_half_translated(rows):
    """译文里残留 >=2 个常见英文虚词 —— 说明这句只译了一半。"""
    bad = []
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        if not re.search(r"[\u4e00-\u9fff]", r["zh"]):
            continue
        s = re.sub(r"%[sd]|<[^>]+>", " ", r["zh"])
        words = [w.lower() for w in re.findall(r"[A-Za-z][A-Za-z'\-]{1,}", s)]
        hits = [w for w in words if w in COMMON_EN]
        # 专名里的 of/the（Clash of Nations、The Iron Plague）不算半译：
        # 原文里同样位置就是大写开头的连续词。
        if len(hits) >= 2 and not re.search(
                r"\b[A-Z][a-z]+(?: (?:of|the|and|de|von) [A-Z][a-z]+)+", r["en"]):
            bad.append((r, "half:%s" % ",".join(hits[:3])))
    return bad


def check_ui_latin(rows):
    """短标签里出现英文单词：界面直接露英文的高发区（原文短、译文也短）。

    两类不算问题：
      * 全大写缩写（DLL/MSG/CTRL/ARASSH）—— 技术名，本就不该硬译；
      * 原文里同拼写的词（metamap、Boneyards、专名）—— 有意保留。
    """
    bad = []
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        en, zh = r["en"], r["zh"]
        if len(en) > 24 or not re.search(r"[\u4e00-\u9fff]", zh):
            continue
        en_low = en.lower()
        for w in set(re.findall(r"[A-Za-z]{3,}", zh)):
            lw = w.lower()
            if lw in ALLOW or lw in en_low or w.isupper():
                continue
            bad.append((r, "ui_en:%s" % w))
            break
    return bad


def check_punct(rows):
    """中文紧贴半角标点、或仍用 `...` 的，统一算排版问题。"""
    bad = []
    half = re.compile(r"[\u4e00-\u9fff][,;:!?]|[,;:!?][\u4e00-\u9fff]")
    dots = re.compile(r"[\u4e00-\u9fff]\.{3}|\u2026{3,}")
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        if half.search(r["zh"]):
            bad.append((r, "半角标点"))
        elif dots.search(r["zh"]):
            bad.append((r, "...未换成……"))
    return bad


def check_traditional(rows):
    """混进繁体字（简中项目不该出现）。只列无歧义的繁体字形，避免误报。"""
    trad = set("個這來時間戰單敵軍資開關於與為擊龍聖術價錢裡麼樣過還進運員屍體傷護"
               "圍選擇確認設隊營廠礦場頭糧訓練僱騎劍牆門橋樑網絡連線導彈藥獸鳥魚馬車輪砲槍")
    bad = []
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        hit = sorted({c for c in r["zh"] if c in trad})
        if hit:
            bad.append((r, "繁体:" + "".join(hit)))
    return bad


CN_DIGIT = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
            "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
CN_UNIT = {"十": 10, "百": 100, "千": 1000, "万": 10000}


def cn_numbers(s):
    """把中文数字（一千、一百八十、十五）折成阿拉伯数字集合，供数字校验比对。"""
    out = set()
    for m in re.finditer(r"[零一二三四五六七八九十百千万两]+", s):
        total = section = num = 0
        for ch in m.group(0):
            if ch in CN_DIGIT:
                num = CN_DIGIT[ch]
            else:
                unit = CN_UNIT[ch]
                if unit == 10000:
                    total += (section + num) * unit
                    section = 0
                else:
                    section += (num or 1) * unit
                num = 0
        val = total + section + num
        if val:
            out.add(str(val))
    return out


def check_numbers(rows):
    """原文的整数应在译文中出现；中文数字（一千、一百八十）算命中。"""
    bad = []
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        # 散文里常把数字写成中文（四千），只在短标签/数值串上强校验
        if len(r["zh"]) > 60:
            continue
        zh_digits = r["zh"].replace(",", "")
        zh_cn = cn_numbers(r["zh"])
        nums = re.findall(r"(?<![A-Za-z0-9])(\d[\d,]{0,5})(?![A-Za-z0-9])", r["en"])
        for n in {x.replace(",", "") for x in nums}:
            if n not in zh_digits and n not in zh_cn:
                bad.append((r, "number:%s" % n))
                break
    return bad


def load_lore(text_dir):
    """读专名表的中文变体与定稿中文，供 C 档检查用。"""
    path = os.path.join(text_dir, "lore_terms.json")
    if not os.path.exists(path):
        return {}, []
    d = json.load(open(path, encoding="utf-8"))
    variants = {k: v for k, v in d.get("variants", {}).items()
                if not k.startswith("_")}
    canon_zh = sorted({v for v in d.get("canonical", {}).values() if v},
                      key=len, reverse=True)
    return variants, canon_zh


def check_variants(rows, variants, canon_zh):
    """译文里还留着旧译名（范诺克 / 谢凯勒什 / 邪恶计数器…）就算术语不一致。

    先把定稿中文整段挖掉再查：「战室」是「作战室」的一部分、
    「阿比亚」是「阿比亚德」的一部分，不挖会满屏误报。
    """
    bad = []
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        s = r["zh"]
        for form in canon_zh:
            s = s.replace(form, "")
        for old in sorted(variants, key=len, reverse=True):
            if old in s:
                bad.append((r, "旧译名:%s->%s" % (old, variants[old])))
                break
    return bad


DUP_PAREN = re.compile(r"([\u4e00-\u9fff]{2,10})（\1）")


def check_duplicate_paren(rows):
    """「塔卡瓦瓦（塔卡瓦瓦）」这类重复括注：英文名替换后常见的残留。"""
    bad = []
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        if DUP_PAREN.search(r["zh"]):
            bad.append((r, "重复括注"))
    return bad


def check_long_sentence(rows, limit=68):
    """一个分句长到没处换气，多半是照英文语序硬拖下来的。"""
    bad = []
    for r in rows:
        if r["status"] != "done" or not r["zh"]:
            continue
        for seg in re.split(r"[，。；！？：、\n]", r["zh"]):
            if len(re.findall(r"[\u4e00-\u9fff]", seg)) > limit:
                bad.append((r, "超长句:%d字" % len(seg)))
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

    variants, canon_zh = load_lore(a.text)
    total_bad = 0
    shown = 0
    for fn in sorted(os.listdir(a.text)):
        if not fn.endswith(".jsonl") or fn[:-6] in SKIP_GROUPS:
            continue
        rows = load_jsonl(os.path.join(a.text, fn))
        groups = [("专名", check_terms(rows, terms)),
                  ("英文残留", check_foreign_words(rows)),
                  ("半译", check_half_translated(rows)),
                  ("界面英文", check_ui_latin(rows)),
                  ("数字丢失", check_numbers(rows)),
                  ("排版", check_punct(rows)),
                  ("繁体", check_traditional(rows)),
                  ("旧译名", check_variants(rows, variants, canon_zh)),
                  ("重复括注", check_duplicate_paren(rows)),
                  ("超长句", check_long_sentence(rows))]
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
