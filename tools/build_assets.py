#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 text/*.jsonl 编译成 workflow 可消费的资产补丁（明文 → 资产）。

两种模式
--------
* **CI 模式（默认，不需要游戏本体）**：只凭待译表就能产出
  - build_out/patches.json       全部已译条，按 src 聚合的写回指令
  - build_out/translate/*.tdf    01 组自足生成的多语言词条表（含中文槽）
  - build_out/icd_patch.json     06 组：文件偏移 → GBK 译文（供 DLL 内存补丁）
  - build_out/summary.json       统计
  这就是 GitHub Action 里应当跑的模式：原版游戏有版权，不进仓库、不进 runner。

* **本地模式（--game <41BB目录>）**：额外产出
  - build_out/update/**          01/02/03/04 组改写后的完整原始资源文本
  用于装上游戏后做端到端验证。

尚未实现（依赖 IDA 定位与 DLL 方案定稿，见 docs/01-待译表与打包约定.md）：
  - HPI v2 重打包（把散文件压回 .hpi）
  - HTML 保留标签结构的段落替换（05_web 目前只出指令）
  - 运行时资源重定向、字库扩展、内存补丁本身

用法
----
    python tools/build_assets.py --text text --out build_out --slot Chinese
    python tools/build_assets.py --text text --out build_out --slot Chinese --strict
    python tools/build_assets.py --text text --out build_out --game <41BB目录>
"""

import argparse
import collections
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

SKIP_GROUPS = {"07_icd_candidates"}

# HPI 里混着一些开发者自述（readme/note/todo 之类），玩家永远看不到。
# 用户约定「只汉化游戏内内容」，这些即使被抽取器扫到、甚至已译，也不该落进资产。
# 注意 readme 常出现在文件名中间（cursors_tex_readme.txt），所以前后都放开。
DEV_NOTE_RE = re.compile(
    r"[^/]*(?:readme|read_me|note|todo|changelog|credit)[^/]*\.(?:txt|md|nfo)$", re.I)


def is_dev_note(src):
    """src 形如 'data.hpi::anims/cursors_tex_readme.txt'。"""
    return bool(DEV_NOTE_RE.search(src.split("::")[-1]))


CJK_RE = re.compile(r"[\u3000-\u303f\u4e00-\u9fff\uff01-\uff60]")


def encode_game(s):
    """含中文的串整体走 GBK，纯西文串保持 cp1252 单字节。

    ★ 为什么不能逐字符「先试 cp1252」
    cp1252 能表示不少 Unicode 字符，一旦逐字符判定，这些字符会被写成**单字节**，
    嵌进 GBK 双字节流里就会把整串错位。真实踩坑：
        '杜马·戈残骸' 里的 '·' 是 U+00B7，cp1252 对应 0xB7，
        结果输出 b6c5 c2ed | b7 | b8ea b2d0 baba —— 11 字节（奇数），
        GBK 解码在 0xb7 处截断，整张 npcs.tdf 打不开，游戏读不到任何词条。
    GBK 是 ASCII 兼容的，所以中文串整体 encode('gbk') 不会伤到其中的英文部分。

    纯西文串仍走 cp1252，保住原文件里的弯引号（0x92）、变音字母等既有字节。
    """
    if CJK_RE.search(s):
        out = bytearray()
        for ch in s:
            try:
                out += ch.encode("gbk")
            except UnicodeEncodeError:
                # GBK 无此字符（•™© 之类）：退回单字节会破坏双字节流，
                # 只能替换成 '?' 并留下痕迹 —— 正常数据已在 clean_text 阶段清掉了。
                out += b"?"
        return bytes(out)
    out = bytearray()
    for ch in s:
        try:
            out += ch.encode("cp1252")
        except UnicodeEncodeError:
            out += ch.encode("gbk", errors="replace")
    return bytes(out)


def load_rows(text_dir):
    by_group = {}
    for fn in sorted(f for f in os.listdir(text_dir) if f.endswith(".jsonl")):
        group = fn[:-6]
        with open(os.path.join(text_dir, fn), encoding="utf-8") as f:
            by_group[group] = [json.loads(l) for l in f if l.strip()]
    return by_group


def collect(by_group):
    """返回 (sources, icd, stats)。sources: src -> list of patch dict。"""
    sources = collections.defaultdict(list)
    icd = []
    stats = collections.Counter()
    for group, rows in sorted(by_group.items()):
        if group in SKIP_GROUPS:
            continue
        for r in rows:
            stats["total"] += 1
            if r["status"] == "skip":
                stats["skipped"] += 1
                continue
            if r["status"] != "done" or not r["zh"]:
                stats["untranslated"] += 1
                continue
            stats["translated"] += 1
            # 00 组是人工补的运行时词条（GUI 内置标签），没有资源目标，
            # 不参与资源补丁，但会进 chs.dict。不能算成 dev_note。
            if group == "00_manual":
                stats["manual"] += 1
                continue
            if all(is_dev_note(t["src"]) for t in r["targets"]):
                stats["dev_note"] += 1
                continue
            if group == "06_icd":
                for t in r["targets"]:
                    m = re.search(r"0x([0-9a-fA-F]+)", t["key"])
                    if m:
                        icd.append({
                            "file_offset": "0x" + m.group(1),
                            "en": r["en"], "zh": r["zh"],
                            "gbk_hex": encode_game(r["zh"]).hex(),
                        })
                continue
            for t in r["targets"]:
                sources[t["src"]].append({
                    "key": t["key"], "en": r["en"], "zh": r["zh"],
                    "kind": r["kind"], "group": group, "id": r["id"],
                })
    return sources, icd, stats


def safe_rel(src):
    """把 'english.hpi::translate/gui_text.tdf' 转成文件系统相对路径。"""
    if "::" in src:
        hpi, path = src.split("::", 1)
        hpi = hpi.replace("<root>", "_root").replace(".hpi", "")
        return os.path.join(hpi, *path.split("/"))
    return src.replace("\\", "/")


def write_runtime_dict(by_group, out_dir):
    """生成 DLL 运行时字典：sub_544020 正向、sub_544110 反向共用一份。

    键规则：
      * 英文原文永远是键（sub_544020 收到的是英文显示串）；
      * 01 组额外收录 TDF 短语 ID，但同一 ID 对应多种译文时视为歧义，不收录。
    歧义键记账输出，交给人工/实机确认，绝不随便挑一条。
    """
    en_map = {}
    for group, rows in sorted(by_group.items()):
        if group in SKIP_GROUPS:
            continue
        for r in rows:
            if r["status"] == "done" and r["zh"]:
                en_map.setdefault(r["en"], r["zh"])

    id_votes = collections.defaultdict(set)
    for group, rows in sorted(by_group.items()):
        if group != "01_ui_tdf":
            continue
        for r in rows:
            if r["status"] != "done" or not r["zh"]:
                continue
            for t in r["targets"]:
                k = (t.get("key") or "").strip()
                if k:
                    id_votes[k].add(r["zh"])

    fwd, conflicts = dict(en_map), []
    for k, vals in sorted(id_votes.items()):
        if len(vals) == 1:
            zh = next(iter(vals))
            if k in fwd and fwd[k] != zh:
                conflicts.append({"key": k, "keep": fwd[k], "drop": zh,
                                  "reason": "id_vs_english"})
                continue
            fwd[k] = zh
        else:
            conflicts.append({"key": k, "variants": sorted(vals)[:5],
                              "reason": "id_ambiguous"})

    rev = {}
    for k, zh in fwd.items():
        rev.setdefault(zh, k)
    data = {
        "schema": 1,
        "encoding": "gbk",
        "case_insensitive": True,
        "note": "键为 GBK 字节串；DLL 需同时按原样与小写查表。reverse 供 sub_544110。",
        "forward": fwd,
        "reverse": rev,
        "conflicts": conflicts,
    }
    path = os.path.join(out_dir, "dict.json")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
        f.write("\n")
    write_chs_dict(fwd, out_dir)
    return len(fwd), len(rev), len(conflicts)


def write_chs_dict(fwd, out_dir):
    """DLL 直接消费的字典：GBK 字节流，`键\\t译文\\n`，一行一条。

    为什么不用 JSON：DLL 是 v141_xp 的 32 位 C++，引 JSON 库要连带处理
    异常/STL/体积；而这个文件只需要「逐行切开、按 TAB 分两段」，200KB
    整个读进内存零拷贝就能建表。

    键用 encode_game 编：英文键走 cp1252 单字节，与引擎传进来的字节完全
    一致（引擎资源也是单字节文本）。译文含中文，自然走 GBK 双字节。
    """
    path = os.path.join(out_dir, "chs.dict")
    n = 0
    skipped = 0
    with open(path, "wb") as f:
        f.write(b"# TAK CHS dict v1\n")
        for k, zh in sorted(fwd.items()):
            if not k or not zh:
                continue
            kb = encode_game(k)
            vb = encode_game(zh)
            # 结构字符不能出现在字段里，否则行切分会错位
            if b"\t" in kb or b"\n" in kb or b"\r" in kb:
                skipped += 1
                continue
            if b"\n" in vb or b"\r" in vb:
                vb = vb.replace(b"\r", b" ").replace(b"\n", b" ")
            f.write(kb + b"\t" + vb + b"\n")
            n += 1
    if skipped:
        print("       chs.dict 跳过 %d 条（键含 TAB/换行）" % skipped)
    return n



def write_translate_tables(sources, out_dir, slot):
    """CI 模式：01 组自足生成 TDF，不需要原游戏文件。

    只写中文槽，不复制原有 5 种语言 —— 引擎按当前 Language 取不到其它槽时
    会回退到英文/词条名，这正是我们做「覆盖层」想要的行为。
    """
    n = 0
    for src, patches in sorted(sources.items()):
        if not src.lower().endswith(".tdf"):
            continue
        tdf = [p for p in patches if p["kind"] == "tdf_entry"]
        if not tdf:
            continue
        rel = safe_rel(src)
        dest = os.path.join(out_dir, "translate", rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        lines = ["// 自动生成，请勿手工编辑；源：text/01_ui_tdf.jsonl", ""]
        for p in sorted(tdf, key=lambda x: x["key"]):
            lines.append("[%s]" % p["key"])
            lines.append("{")
            lines.append("\t%s = %s;" % (slot, p["zh"]))
            lines.append("}")
            lines.append("")
        with open(dest, "wb") as f:
            f.write(encode_game("\n".join(lines)))
        n += 1
    return n


def load_source(game_dir, src):
    """本地模式：读出原始资源文本（cp1252 解码）。"""
    if "::" in src:
        hpi_name, path = src.split("::", 1)
        if hpi_name == "<root>":
            raw = open(os.path.join(game_dir, path), "rb").read()
        else:
            from hpi_v2 import HpiV2
            hpi = HpiV2(os.path.join(game_dir, hpi_name))
            hit = next((r for r in hpi.records if r.path == path), None)
            if hit is None:
                return None
            raw = hpi.read_file(hit)
    else:
        p = os.path.join(game_dir, src.replace("/", os.sep))
        if not os.path.isfile(p):
            return None
        raw = open(p, "rb").read()
    return raw.decode("cp1252", "replace")


def apply_patches(text, patches, slot):
    """本地模式：把已译条改写进原始资源文本，返回新文本。"""
    lines = text.split("\n")
    kinds = {p["kind"] for p in patches}

    if "tdf_entry" in kinds:
        # 按 [词条名] 定位块，在块内替换/插入语言槽
        by_key = {p["key"]: p["zh"] for p in patches if p["kind"] == "tdf_entry"}
        i = 0
        while i < len(lines):
            m = re.match(r"^\s*\[([^\]\r\n]+)\]\s*$", lines[i])
            if not m or m.group(1).strip() not in by_key:
                i += 1
                continue
            zh = by_key[m.group(1).strip()]
            j = i + 1
            while j < len(lines) and "{" not in lines[j]:
                j += 1
            if j >= len(lines):
                break
            k = j + 1
            replaced = False
            while k < len(lines) and "}" not in lines[k]:
                sm = re.match(r"^(\s*)%s\s*=.*;\s*$" % re.escape(slot), lines[k])
                if sm:
                    lines[k] = "%s%s = %s;" % (sm.group(1), slot, zh)
                    replaced = True
                    break
                k += 1
            if not replaced:
                lines.insert(j + 1, "\t%s = %s;" % (slot, zh))
            i = k + 1
        return "\n".join(lines)

    # 其余按行号定位
    for p in patches:
        m = re.search(r"L(\d+)", p["key"])
        if not m:
            continue
        idx = int(m.group(1)) - 1
        if idx < 0 or idx >= len(lines):
            continue
        old = lines[idx]
        if p["kind"] == "tdf_field":
            mm = re.match(r"^(\s*[A-Za-z_]\w*\s*=\s*)(.*?)(;\s*)$", old)
            if mm:
                lines[idx] = mm.group(1) + p["zh"] + mm.group(3)
        elif p["kind"] == "txt_line":
            lead = re.match(r"^\s*", old).group(0)
            lines[idx] = lead + p["zh"]
        elif p["kind"] == "gui_line":
            mm = re.match(r"^(\s*2\s+0\s+)(\d+)(\s+)(.+?)(\s*)$", old)
            if mm:
                n = len(encode_game(p["zh"]))
                lines[idx] = "%s%d%s%s%s" % (mm.group(1), n, mm.group(3),
                                             p["zh"], mm.group(5))
    return "\n".join(lines)


def write_update(sources, out_dir, slot, game_dir):
    """本地模式：生成改写后的完整资源文本（HTML 除外，见模块 docstring）。"""
    written = skipped = 0
    for src, patches in sorted(sources.items()):
        kinds = {p["kind"] for p in patches}
        if is_dev_note(src) or "web_text" in kinds:
            skipped += 1
            continue
        text = load_source(game_dir, src)
        if text is None:
            skipped += 1
            continue
        new = apply_patches(text, patches, slot)
        dest = os.path.join(out_dir, "update", safe_rel(src))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(encode_game(new))
        written += 1
    return written, skipped


def main():
    ap = argparse.ArgumentParser(description="明文待译表 → 资产补丁")
    ap.add_argument("--text", default="text")
    ap.add_argument("--out", default="build_out")
    ap.add_argument("--slot", default="Chinese",
                    help="写入哪个语言槽，默认 Chinese（A2 方案，IDA 已验证无语言白名单；German 不可占用）")
    ap.add_argument("--game", help="干净 41BB 目录；给了才生成 build_out/update/")
    ap.add_argument("--strict", action="store_true",
                    help="01-06 组仍有未译条时失败（CI 用）")
    a = ap.parse_args()

    by_group = load_rows(a.text)
    sources, icd, stats = collect(by_group)
    os.makedirs(a.out, exist_ok=True)

    with open(os.path.join(a.out, "patches.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump({"schema": 1, "slot": a.slot, "encoding": "gbk-mixed",
                   "sources": dict(sources)}, f, ensure_ascii=False, indent=2)
        f.write("\n")

    n_tdf = write_translate_tables(sources, a.out, a.slot)
    n_dict, n_rev, n_conf = write_runtime_dict(by_group, a.out)
    with open(os.path.join(a.out, "icd_patch.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump({"schema": 1,
                   "note": "文件偏移，非 RVA；需 IDA 确认引用来源并做联机白名单",
                   "entries": icd}, f, ensure_ascii=False, indent=2)
        f.write("\n")

    result = {"schema": 1, "slot": a.slot, "encoding": "gbk-mixed",
              "total": stats["total"], "translated": stats["translated"],
              "untranslated": stats["untranslated"],
              "dev_note": stats["dev_note"],
              "sources": len(sources), "tdf_tables": n_tdf,
              "dict_entries": n_dict, "dict_reverse": n_rev,
              "dict_conflicts": n_conf,
              "icd_entries": len(icd),
              # skipped = 文本层按规则跳过的条数；update_skipped 才是改写阶段
              # 没落地的文件数。两者语义不同，不能共用一个字段（曾互相覆盖，
              # 导致 CI 模式下 skipped 恒为 0，本地模式又被 117 顶掉）。
              "skipped": stats["skipped"],
              "update_files": 0, "update_skipped": 0}
    if a.game:
        w, s = write_update(sources, a.out, a.slot, a.game)
        result["update_files"] = w
        result["update_skipped"] = s
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("已译 %d / %d 条，未译 %d 条，按规则跳过 %d 条" %
          (stats["translated"], stats["total"], stats["untranslated"],
           stats["skipped"]))
    if stats["dev_note"]:
        print("      其中 %d 条属开发者自述（readme/note），已计入已译但不进资产"
              % stats["dev_note"])
    print("产出: patches.json (%d 个来源), translate/*.tdf (%d 份), "
          "dict.json (%d 正 / %d 反 / %d 冲突), icd_patch.json (%d 条), summary.json"
          % (len(sources), n_tdf, n_dict, n_rev, n_conf, len(icd)))
    if a.game:
        print("      build_out/update/ 改写文件 %d 份，跳过 %d 份（HTML 或未找到）"
              % (result["update_files"], result["update_skipped"]))
    if a.strict and stats["untranslated"]:
        print("STRICT: 仍有 %d 条未译，构建失败" % stats["untranslated"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
