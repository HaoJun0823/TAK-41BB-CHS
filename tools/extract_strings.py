#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从干净 41BB 安装中抽取全部待译英文，生成明文待译表 text/*.jsonl。

设计要点
--------
* 唯一真相源是 `text/*.jsonl`（UTF-8、一行一条）。游戏原始资源不入库。
* 按优先级分六组，组内**按英文原文去重**，但用 targets 保留每一个写回位置。
  这样 LLM 只需翻译一次，workflow 却能精确改写所有出现处。
* 每条带稳定 id、占位符清单、上下文与状态；翻译脚本只准回填 zh / status。
* 可复现：只要游戏基线不变，重跑本脚本产出的文件应逐字节一致。

用法
----
    python tools/extract_strings.py --game <41BB目录> [--out text] [--no-web]
    python tools/extract_strings.py --game <41BB目录> --check     # 只统计不写文件
"""

import argparse
import csv
import hashlib
import html.parser
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hpi_v2 import HpiV2  # noqa: E402

GROUPS = [
    ("01_ui_tdf", "引擎自带多语言词条表 translate/*.tdf（P0，最该先译）"),
    ("02_units", "单位/建筑定义里的名称与说明（P1）"),
    ("03_missions", "任务/地图名称与简报（P2）"),
    ("04_gui", "GUI 布局里的按钮与提示文本（P3）"),
    ("05_web", "游戏内嵌网页可见文本（P4）"),
    ("06_icd", "主程序硬编码串：高置信 UI 文案（P5，仍需人工确认）"),
    ("07_icd_candidates", "主程序全部 ASCII 串候选池（status=review，默认不送翻）"),
]

# 这些字段的值才可能是给人看的文案；其余是数值/标识符/路径。
TEXT_FIELDS = {
    "name", "description", "missionname", "missiondescription",
    "title", "text", "label", "caption", "tooltip", "hint",
    "shortdescription", "longdescription", "briefing", "objective",
    "message", "subtitle", "help", "prompt", "buttontext", "string",
    "displayname", "unitdescription", "missiontitle", "tedclass",
}

# C++ runtime / 调试串黑名单词（主程序组用）
RUNTIME_NOISE = (
    "ios::", "bad allocation", "unknown exception", "access violation",
    "bad dynamic_cast", "string too long", "invalid string position",
    "this program cannot", "rtti", "assert", "debug", "exception",
    "vector", "iterator", "null pointer", "memory", "heap", "stack overflow",
    "too small", "library version", "not determined", "allocation",
    "dperr", "hapinet", "failed", "failed to", "cannot be",
)

# 第三方库（libjpeg / libpng / DirectDraw / zlib）的内部报错，不是游戏 UI
LIB_NOISE = (
    "chunk", "jfif", "coefficient", "scanline", "quantization",
    "cooperative level", "pixel format", "rect. is", "viewport",
    "instruction fetches", "uppercase lowercase pairs", "logic error",
    "buffer overflow", "render explode", "attempt to read row data",
    "uppercase uppercase pairs", "lowercase lowercase pairs",
)

# printf 占位符必须整体匹配：%ld / %02x / %3.1f / %% 都算一个，
# 旧版 %[-+ #0-9.]*[a-zA-Z] 会把 %ld 切成 %l，把 "%% complete" 切成 "% c"。
PRINTF_RE = re.compile(
    r"%(?:%|[-+ #0]*\d*(?:\.\d+)?(?:hh|h|ll|l|L|z|j|t)?[diouxXeEfgGaAcspn])"
)
TAG_RE = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
PLACEHOLDER_RE = re.compile(
    PRINTF_RE.pattern + r"|" + TAG_RE.pattern + r"|\{\d+\}|#\w+#|\\n|\\t|\$[A-Za-z_]\w*"
)


def decode(raw):
    """游戏资源是 8 位单字节文本；cp1252 能把 0x92 之类还原成弯引号。"""
    return raw.decode("cp1252", errors="replace")


def clean(s):
    return s.replace("\r", "").replace("\t", " ").strip()


def looks_translatable(s):
    """粗判一段英文是否值得送翻。"""
    if not s or len(s) > 1000:
        return False
    if len(re.findall(r"[A-Za-z]{2,}", s)) < 1:
        return False
    # 纯标识符 / 常量（含 takmission29_dh、nobuild 这类内部名）
    if re.fullmatch(r"[A-Z0-9_ ./+-]+", s):
        return False
    if " " not in s and re.fullmatch(r"[a-z0-9_]+", s):
        return False
    # 路径、文件名、URL、注册表
    if re.search(r"(?i)(\\|\.dll|\.exe|\.hpi|\.tdf|\.gaf|\.wav|://|software\\|^\./|\.\./)", s):
        return False
    # 规格串："10 x 10  8 Player  32MB" / "12 X 12  4 Player  32MB"
    if re.match(r"^\s*\d+\s*x\s*\d+", s, re.I):
        return False
    return True


def placeholders(s):
    return sorted(set(m.group(0) for m in PLACEHOLDER_RE.finditer(s)))


class Table(object):
    """一个分组的待译表：英文原文去重 + 全部写回位置。"""

    def __init__(self, code, desc):
        self.code = code
        self.desc = desc
        self.rows = {}

    def add(self, en, src, key, kind, ctx, flags=None, force=False, status="pending"):
        en = clean(en)
        if not force and not looks_translatable(en):
            return False
        if "\n" in en:
            en = " ".join(en.split())
        rid = "%s-%s" % (self.code.split("_")[0],
                         hashlib.sha1((kind + "|" + en).encode("utf-8")).hexdigest()[:12])
        row = self.rows.get(rid)
        if row is None:
            row = {
                "id": rid,
                "en": en,
                "zh": "",
                "status": status,
                "ctx": ctx,
                "kind": kind,
                "placeholders": placeholders(en),
                "flags": sorted(set(flags or [])),
                "targets": [],
            }
            self.rows[rid] = row
        t = {"src": src, "key": key}
        if t not in row["targets"]:
            row["targets"].append(t)
        return True

    def write(self, outdir):
        path = os.path.join(outdir, self.code + ".jsonl")
        rows = sorted(self.rows.values(), key=lambda r: r["id"])
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
        return path, len(rows)


# ---------------- 各组提取器 ----------------

TDF_ENTRY_RE = re.compile(r"^\s*\[([^\]\r\n]+)\]\s*$")
TDF_ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(.*?);\s*$")
ASSIGN_RE = re.compile(r"^\s*([A-Za-z_]\w*)\s*=\s*(.*?);\s*$", re.M)
GUI_TEXT_RE = re.compile(rb"^\s*2\s+0\s+(\d+)\s+(.+?)\s*$", re.M)


def extract_ui_tdf(table, hpi_name, path, text):
    """translate/*.tdf：每个 [词条] 一条。有 English 槽取槽值，否则取词条名。"""
    cur = None
    english = None
    for line in text.splitlines():
        m = TDF_ENTRY_RE.match(line)
        if m:
            if cur is not None:
                en = english if english is not None else cur
                table.add(en, "%s::%s" % (hpi_name, path), cur, "tdf_entry",
                          "UI 词条表；写回时填中文语言槽",
                          [] if english is not None else ["english_from_key"])
            cur = m.group(1).strip()
            english = None
            continue
        a = TDF_ASSIGN_RE.match(line)
        if a and cur is not None and a.group(1).lower() == "english":
            english = a.group(2).strip()
    if cur is not None:
        en = english if english is not None else cur
        table.add(en, "%s::%s" % (hpi_name, path), cur, "tdf_entry",
                  "UI 词条表；写回时填中文语言槽",
                  [] if english is not None else ["english_from_key"])


def extract_fields(table, hpi_name, path, text, group):
    """fbi / 非 translate 的 tdf / ota：只取白名单字段。"""
    for m in ASSIGN_RE.finditer(text):
        k, v = m.group(1).strip(), clean(m.group(2))
        if k.lower() not in TEXT_FIELDS:
            continue
        if not v:
            continue
        src = "%s::%s" % (hpi_name, path)
        # 用行号做 key，避免同名字段互相覆盖
        line_no = text.count("\n", 0, m.start()) + 1
        table.add(v, src, "%s@L%d" % (k, line_no), "tdf_field",
                  "数据定义文件里的显示字段 %s" % k,
                  ["field:" + k.lower()])


def extract_gui(table, hpi_name, path, raw):
    for m in GUI_TEXT_RE.finditer(raw):
        n = int(m.group(1))
        t = decode(m.group(2)).strip()
        if len(t) != n or t.startswith("#") or not re.search(r"[A-Za-z]{2}", t):
            continue
        line_no = raw.count(b"\n", 0, m.start()) + 1
        table.add(t, "%s::%s" % (hpi_name, path), "L%d" % line_no, "gui_line",
                  "GUI 控件上的显示文本",
                  ["gui_fixed_length:%d" % n])


class VisibleText(html.parser.HTMLParser):
    """抽取网页可见文本，按块级标签分段，跳过 script/style。"""

    BLOCK = {"p", "div", "td", "tr", "li", "br", "h1", "h2", "h3", "h4",
             "h5", "h6", "center", "table", "ul", "ol", "hr", "blockquote"}
    SKIP = {"script", "style", "head"}

    def __init__(self):
        html.parser.HTMLParser.__init__(self, convert_charrefs=True)
        self.chunks = []
        self.buf = []
        self.skip = 0

    def handle_starttag(self, tag, attrs):
        if tag in self.SKIP:
            self.skip += 1
        if tag in self.BLOCK:
            self.flush()

    def handle_endtag(self, tag):
        if tag in self.SKIP and self.skip:
            self.skip -= 1
        if tag in self.BLOCK:
            self.flush()

    def handle_data(self, data):
        if not self.skip:
            self.buf.append(data)

    def flush(self):
        s = clean(" ".join("".join(self.buf).split()))
        if s:
            self.chunks.append(s)
        self.buf = []

    def result(self):
        self.flush()
        return self.chunks


def extract_web(table, rel_path, raw):
    p = VisibleText()
    try:
        p.feed(decode(raw))
        p.close()
    except Exception:
        return
    for i, chunk in enumerate(p.result()):
        if not looks_translatable(chunk):
            continue
        table.add(chunk, rel_path, "P%d" % i, "web_text",
                  "游戏内嵌网页正文段落", ["html"])


def extract_txt_lines(table, hpi_name, path, text):
    """任务目标/简报 .txt：每行一条（• 开头或完整句子），排除 AI profile 数值行。"""
    for i, line in enumerate(text.splitlines(), 1):
        s = clean(line)
        if not s or s.startswith("//") or s.startswith(";"):
            continue
        words = re.findall(r"[A-Za-z]{2,}", s)
        if len(words) < 4 and not s.startswith(("\u2022", "*", "-")):
            continue
        # AI profile / 数值配置行，如 "weight araarch 5"
        if re.match(r"(?i)^(weight|limit|build|max|min|num|unit|side|ai|profile|map|type)\b", s) \
                and not re.search(r"[.!?]\s*$", s):
            continue
        table.add(s, "%s::%s" % (hpi_name, path), "L%d" % i, "txt_line",
                  "任务目标 / 简报正文（逐行）", ["plain_line"])


def strict_ui(s):
    """从主程序 ASCII 串里挑高置信的「玩家可见短语」，排除 C++ 符号与调试串。"""
    if not looks_translatable(s) or " " not in s or len(s.split()) < 2:
        return False
    if not re.fullmatch(r"[A-Za-z0-9 ,.!?:;'\"()\-&'/+#%]+", s):
        return False
    if re.search(r"[_@\\]|::|\?AV|\.dll|\.exe|\.htm|\.frd|\.hpi|\.gaf|\.gui|\.txt|\.wav|\.bik",
                 s, re.I):
        return False
    low = s.lower()
    if any(n in low for n in RUNTIME_NOISE) or any(n in low for n in LIB_NOISE):
        return False
    # libjpeg / DirectDraw 的错误码尾巴，如 "(NOEMULATION)" "(HEIGHTALIGN)"
    if re.search(r"\([A-Z]{5,}\)", s):
        return False
    if re.match(r"^(DPERR|HAPINET|ERR_|ERROR|WARN|Max |Min |RPE:|Get|Set|Create|Load|Save|Init|Destroy)",
                s, re.I):
        return False
    if re.search(r"(?i)\b(exception|assert|too small|library version|unexpected|not supported)\b", s):
        return False
    if re.search(r":\s*%|%\d*[sdf]\b.*:", s):
        return False
    return True


def extract_icd(table_ui, table_cand, raw):
    """主程序 ASCII 串：严格表 + 全量候选池（候选池默认 status=review，不送翻）。"""
    seen = set()
    for m in re.finditer(rb"[ -~]{5,}", raw):
        b = m.group()
        off = m.start()
        s = decode(b).strip()
        if not looks_translatable(s):
            continue
        low = s.lower()
        if any(n in low for n in RUNTIME_NOISE):
            continue
        letters = len(re.findall(r"[A-Za-z0-9 ,.!?:;'\"()\-]", s))
        if letters < len(s) * 0.85:
            continue
        key = "str@0x%x" % off
        table_cand.add(s, "KINGDOMS.icd::ascii", key, "icd_ascii",
                       "主程序 ASCII 串；需用 IDA 交叉引用确认是否为 UI，确认后再提升",
                       ["candidate", "needs_review"], force=True, status="review")
        if s in seen:
            continue
        seen.add(s)
        if strict_ui(s):
            table_ui.add(s, "KINGDOMS.icd::hardcoded", key, "icd_string",
                         "主程序硬编码 UI 文案，需人工确认", ["needs_review"])


# ---------------- 主流程 ----------------

def build(game_dir, out_dir, include_web=True, check=False):
    tables = {code: Table(code, desc) for code, desc in GROUPS}
    hpis = sorted(__import__("glob").glob(os.path.join(game_dir, "*.hpi")))
    stats = {"hpi": len(hpis), "files": 0, "text_files": 0}
    gaf_rows = []
    for path in hpis:
        hpi = HpiV2(path)
        hpi_name = os.path.basename(path)
        for rec in hpi.records:
            stats["files"] += 1
            if rec.name.lower().endswith(".gaf"):
                gaf_rows.append(["%s::%s" % (hpi_name, rec.path),
                                 rec.decompressed_size,
                                 "GAF 位图/字体；若含烧字或需扩字库则须修图"])
            if not rec.is_text():
                continue
            stats["text_files"] += 1
            raw = hpi.read_file(rec)
            low = rec.path.lower()
            ext = os.path.splitext(rec.name)[1].lower()
            if "/translate/" in low or low.startswith("translate/"):
                extract_ui_tdf(tables["01_ui_tdf"], hpi_name, rec.path, decode(raw))
            elif ext == ".gui":
                extract_gui(tables["04_gui"], hpi_name, rec.path, raw)
            elif ext == ".ota":
                extract_fields(tables["03_missions"], hpi_name, rec.path, decode(raw), "03")
            elif ext == ".txt":
                extract_txt_lines(tables["03_missions"], hpi_name, rec.path, decode(raw))
                extract_fields(tables["02_units"], hpi_name, rec.path, decode(raw), "02")
            elif ext in (".fbi", ".tdf", ".def", ".msg"):
                extract_fields(tables["02_units"], hpi_name, rec.path, decode(raw), "02")

    # 根目录 Startup.tdf 也是多语言词条表（缺 CD / DirectX 版本等启动前提示）
    startup = os.path.join(game_dir, "Startup.tdf")
    if os.path.exists(startup):
        extract_ui_tdf(tables["01_ui_tdf"], "<root>", "Startup.tdf",
                       decode(open(startup, "rb").read()))

    if include_web:
        web_count = 0
        for sub in ("Boneyards", "atlas", "GC"):
            base = os.path.join(game_dir, sub)
            if not os.path.isdir(base):
                continue
            for dp, _dns, fns in os.walk(base):
                for fn in fns:
                    if not fn.lower().endswith((".htm", ".html")):
                        continue
                    p = os.path.join(dp, fn)
                    rel = os.path.relpath(p, game_dir).replace("\\", "/")
                    extract_web(tables["05_web"], rel, open(p, "rb").read())
                    web_count += 1
        stats["web_pages"] = web_count

    icd_path = os.path.join(game_dir, "KINGDOMS.icd")
    if os.path.exists(icd_path):
        extract_icd(tables["06_icd"], tables["07_icd_candidates"],
                    open(icd_path, "rb").read())

    print("%-14s %8s %8s  %s" % ("分组", "唯一英文", "写回位置", "说明"))
    total_rows = total_targets = 0
    for code, desc in GROUPS:
        t = tables[code]
        n = len(t.rows)
        tg = sum(len(r["targets"]) for r in t.rows.values())
        total_rows += n
        total_targets += tg
        print("%-14s %8d %8d  %s" % (code, n, tg, desc))
    print("-" * 78)
    print("合计 %d 条唯一英文串，覆盖 %d 个写回位置" % (total_rows, total_targets))
    print("资源: %d 个 HPI / %d 条索引记录 / %d 个文本类文件" %
          (stats["hpi"], stats["files"], stats["text_files"]))

    if check:
        return 0

    os.makedirs(out_dir, exist_ok=True)
    manifest = {
        "schema": 1,
        "source_sha256": (hashlib.sha256(open(icd_path, "rb").read()).hexdigest()[:16]
                          if os.path.exists(icd_path) else ""),
        "groups": [],
    }
    for code, desc in GROUPS:
        _, n = tables[code].write(out_dir)
        manifest["groups"].append({"file": code + ".jsonl", "entries": n, "desc": desc})
    with open(os.path.join(out_dir, "manifest.json"), "w", encoding="utf-8",
              newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=2)
        f.write("\n")

    # 不可直译资产清单（游戏外文档一律排除）
    ntr = os.path.join(os.path.dirname(out_dir), "inventory", "non_translatable.csv")
    os.makedirs(os.path.dirname(ntr), exist_ok=True)
    rows = []
    outside = {"docs", "manual", "licenses", "common settings"}
    for p in __import__("glob").glob(os.path.join(game_dir, "**", "*"), recursive=True):
        if not os.path.isfile(p):
            continue
        rel = os.path.relpath(p, game_dir).replace("\\", "/")
        top = rel.split("/")[0].lower()
        if top in outside or rel.lower().startswith(("readme", "eula", "v4readme",
                                                      "v41readme", "rendererhelp",
                                                      "unins", "manual")):
            continue
        ext = os.path.splitext(p)[1].lower()
        reason = None
        if ext == ".bik":
            reason = "过场视频，字幕烧录在帧内，需重制（建议不纳入）"
        elif ext == ".wav":
            reason = "语音/音效，需配音或重录（建议不纳入）"
        elif ext in (".gif", ".png", ".jpg") and ("/" not in rel or top in
                                                   {"boneyards", "atlas", "gc"}):
            reason = "位图，若含烧字需修图；纯配图可跳过"
        if reason:
            rows.append([rel, os.path.getsize(p), reason])
    rows.extend(gaf_rows)
    with open(ntr, "w", newline="", encoding="utf-8-sig") as f:
        w = csv.writer(f)
        w.writerow(["path", "bytes", "reason"])
        w.writerows(rows)
    print("已写出 %s（%d 条不可直译/需人工制作资产）" % (ntr, len(rows)))
    print("已写出 %s/manifest.json 与 %d 个 jsonl" % (out_dir, len(GROUPS)))
    return 0


def main():
    ap = argparse.ArgumentParser(description="抽取 41BB 待译英文，生成明文待译表")
    ap.add_argument("--game", required=True, help="干净 41BB 游戏目录")
    ap.add_argument("--out", default="text", help="输出目录，默认 text/")
    ap.add_argument("--no-web", action="store_true", help="排除内嵌网页")
    ap.add_argument("--check", action="store_true", help="只统计，不写文件")
    a = ap.parse_args()
    return build(a.game, a.out, include_web=not a.no_web, check=a.check)


if __name__ == "__main__":
    sys.exit(main())
