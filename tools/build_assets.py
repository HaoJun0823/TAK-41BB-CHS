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
    python tools/build_assets.py --text text --out build_out --slot Italian
    python tools/build_assets.py --text text --out build_out --slot Italian --strict
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


def encode_game(s):
    """按「原单字节优先、中文走 GBK」编码：保住弯引号/德语变音，中文入 GBK。

    游戏资源是 8 位单字节文本（cp1252）。中文无法用单字节表示，必须写 GBK
    双字节。逐字符处理可避免整串 encode('gbk') 把原有 8 位字符撑成双字节。
    """
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
            if r["status"] != "done" or not r["zh"]:
                stats["untranslated"] += 1
                continue
            stats["translated"] += 1
            if group == "06_icd":
                for t in r["targets"]:
                    m = re.search(r"0x([0-9a-fA-F]+)", t["key"])
                    if m:
                        icd.append({
                            "file_offset": "0x" + m.group(1),
                            "en": r["en"], "zh": r["zh"],
                            "gbk_hex": r["zh"].encode("gbk", "replace").hex(),
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
                n = len(p["zh"].encode("gbk", "replace"))
                lines[idx] = "%s%d%s%s%s" % (mm.group(1), n, mm.group(3),
                                             p["zh"], mm.group(5))
    return "\n".join(lines)


def write_update(sources, out_dir, slot, game_dir):
    """本地模式：生成改写后的完整资源文本（HTML 除外，见模块 docstring）。"""
    written = skipped = 0
    for src, patches in sorted(sources.items()):
        kinds = {p["kind"] for p in patches}
        if "web_text" in kinds:
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
    ap.add_argument("--slot", default="Italian",
                    help="写入哪个语言槽，默认 Italian（A1 方案；待 IDA 验证）")
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
    with open(os.path.join(a.out, "icd_patch.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump({"schema": 1,
                   "note": "文件偏移，非 RVA；需 IDA 确认引用来源并做联机白名单",
                   "entries": icd}, f, ensure_ascii=False, indent=2)
        f.write("\n")

    result = {"schema": 1, "slot": a.slot,
              "total": stats["total"], "translated": stats["translated"],
              "untranslated": stats["untranslated"],
              "sources": len(sources), "tdf_tables": n_tdf,
              "icd_entries": len(icd), "update_files": 0, "skipped": 0}
    if a.game:
        w, s = write_update(sources, a.out, a.slot, a.game)
        result["update_files"] = w
        result["skipped"] = s
    with open(os.path.join(a.out, "summary.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print("已译 %d / %d 条，未译 %d 条" %
          (stats["translated"], stats["total"], stats["untranslated"]))
    print("产出: patches.json (%d 个来源), translate/*.tdf (%d 份), "
          "icd_patch.json (%d 条), summary.json"
          % (len(sources), n_tdf, len(icd)))
    if a.game:
        print("      build_out/update/ 改写文件 %d 份，跳过 %d 份（HTML 或未找到）"
              % (result["update_files"], result["skipped"]))
    if a.strict and stats["untranslated"]:
        print("STRICT: 仍有 %d 条未译，构建失败" % stats["untranslated"])
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
