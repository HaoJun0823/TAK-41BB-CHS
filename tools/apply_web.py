#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 05_web.jsonl 的译文写回内嵌网页（atlas/、Boneyards/、GC/）。

抽取时用 `extract_strings.VisibleText` 把网页按块级标签切成段落，
段落序号 `P<i>` 是「切块后的自然序号」（不因是否可译而改变）。本脚本用
同一套切块规则在原文上重放一遍，拿到每个段落在**原始字节里的区间**，
只替换文本、不碰标签结构：

    <TD><B>Foo</B> bar</TD>
    ->  <TD><B>译文</B></TD>      （译文落在字符最多的那个文本片段里）

这样 `<a href=...>`、`<img>`、表格布局、字体标签都原样保留，丢掉的只是
段落内部的行内排版（一段里既有粗体又有正文时，粗体会吃掉整段）。

产物写到 `build_out/web/<相对路径>`；部署时放到游戏目录 `chs_web/`，
由 DLL 在 CreateFileA 上做重定向（原文件一个字节不动）。

用法：
    python tools/apply_web.py --check          # 只统计能写回多少条
    python tools/apply_web.py --write          # 产出 build_out/web
    python tools/apply_web.py --write --encoding utf-8
"""

import argparse
import html
import html.parser
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)

BLOCK = {"p", "div", "td", "tr", "li", "br", "h1", "h2", "h3", "h4",
         "h5", "h6", "center", "table", "ul", "ol", "hr", "blockquote"}
SKIP = {"script", "style", "head"}

CHARSET_RE = re.compile(rb"charset\s*=\s*[\"']?iso-8859-1", re.I)


def clean(s):
    return s.replace("\r", "").replace("\t", " ").strip()


class SpanText(html.parser.HTMLParser):
    """按 VisibleText 的规则切块，同时记录每块在原文里的字符区间。

    原文按 cp1252 解码，单字节一一对应，所以字符下标 == 字节下标。
    """

    def __init__(self, src):
        html.parser.HTMLParser.__init__(self, convert_charrefs=False)
        self.src = src
        self.line_off = [0]
        for line in src.splitlines(keepends=True):
            self.line_off.append(self.line_off[-1] + len(line))
        self.chunks = []      # [{'text': str, 'spans': [(start, end), ...]}]
        self.cur = []
        self.skip = 0
        self.last_end = 0     # 上一段的结束位置，防止 getpos 给出重叠区间

    def _off(self):
        line, col = self.getpos()
        return self.line_off[line - 1] + col

    def _locate(self, data):
        """getpos() 只是提示；遇到不带分号的 &nbsp 之类会偏，回头找一次。

        允许相邻片段重叠：`&nbsp ` 这种漏分号的写法会让解析器把同一个空格
        算进前后两段。重叠一个空格对文本比较无害（空白会被折叠），但**不能**
        用「往前顶到上一段结尾」的办法修正，那会把片段末尾推出真实的文本边界。
        """
        hint = self._off()
        if self.src.startswith(data, hint):
            start = hint
        else:
            start = self.src.find(data, max(0, hint - 6), hint + len(data) + 32)
            if start < 0:
                start = hint
        self.last_end = start + len(data)
        return start, self.last_end

    def _add(self, start, end):
        if not self.skip:
            self.cur.append((start, end))

    def handle_data(self, data):
        self._add(*self._locate(data))

    def handle_entityref(self, name):
        self._add(*self._locate("&" + name + ";"))

    def handle_charref(self, name):
        self._add(*self._locate("&#" + name + ";"))

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        if tag in BLOCK:
            self.flush()

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1
        if tag in BLOCK:
            self.flush()

    def flush(self):
        spans, self.cur = self.cur, []
        if not spans:
            return
        raw = "".join(self.src[a:b] for a, b in spans)
        text = clean(" ".join(html.unescape(raw).split()))
        if text:
            self.chunks.append({"text": text, "spans": spans})

    def result(self):
        self.flush()
        return self.chunks


def load_rows(text_dir):
    path = os.path.join(text_dir, "05_web.jsonl")
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            if line.strip():
                rows.append(json.loads(line))
    return rows


def group_by_file(rows):
    """src -> {P 序号: 译文}；同一条目多个 target 时逐个登记。"""
    out = {}
    for r in rows:
        if r.get("status") != "done" or not r.get("zh"):
            continue
        for t in r.get("targets", []):
            m = re.fullmatch(r"P(\d+)", t.get("key", ""))
            if not m:
                continue
            out.setdefault(t["src"], {})[int(m.group(1))] = r["zh"]
    return out


def rewrite(src, mapping, encoding):
    """返回 (新字节, 统计)。src 为原始字节。"""
    text = src.decode("cp1252", errors="replace")
    p = SpanText(text)
    p.feed(text)
    p.close()
    chunks = p.result()

    edits = []          # (start, end, bytes)
    replaced = 0
    missing = []
    for idx, zh in mapping.items():
        if idx >= len(chunks):
            missing.append(idx)
            continue
        spans = chunks[idx]["spans"]
        # 主片段：非空白字符最多的那个（粗体/链接里的正文最可能在里面）
        best, best_n = None, -1
        for a, b in spans:
            piece = html.unescape(text[a:b])
            n = sum(1 for c in piece if not c.isspace())
            if n > best_n:
                best, best_n = (a, b), n
        if best is None:
            missing.append(idx)
            continue
        zh_bytes = zh.encode(encoding)
        edits.append((best[0], best[1], zh_bytes))
        for a, b in spans:
            if (a, b) == best:
                continue
            # 片段可能与主片段重叠一个空格（见 _locate 的说明）；裁掉重叠部分，
            # 免得把译文的首尾字节当成空白删掉。
            if b <= best[0] or a >= best[1]:
                pass
            elif a < best[0] < b:
                b = best[0]
            elif a < best[1] < b:
                a = best[1]
            else:
                continue
            if b > a and html.unescape(text[a:b]).strip():
                edits.append((a, b, b""))
        replaced += 1

    out = bytearray(src)
    for a, b, data in sorted(edits, key=lambda e: -e[0]):
        out[a:b] = data
    out = CHARSET_RE.sub(b"charset=gb2312", bytes(out))
    return bytes(out), replaced, missing


def _chunk_text(raw, chunk, encoding, fallback="cp1252"):
    """按字节取一个段落的可见文本。

    cp1252 解码是 1 字节 1 字符，所以 SpanText 给出的区间可以直接当字节
    下标用；译文段用目标编码解，其余段用 cp1252 解。
    """
    parts = []
    for a, b in chunk["spans"]:
        buf = raw[a:b]
        try:
            parts.append(buf.decode(encoding))
        except UnicodeDecodeError:
            # 段落里夹着 0xA0 之类的单字节（cp1252 的 nbsp），整段 GBK 解不了；
            # 逐片降级，别让一个字节毁掉整段校验。
            parts.append(buf.decode(fallback, errors="replace"))
    text = "".join(parts)
    return clean(" ".join(html.unescape(text).split()))


def verify(orig, new, mapping, encoding):
    """重跑切块：未翻译段落必须逐字不变，翻译段落必须正好是译文。"""
    problems = []
    ta = orig.decode("cp1252", errors="replace")
    tb = new.decode("cp1252", errors="replace")
    pa, pb = SpanText(ta), SpanText(tb)
    pa.feed(ta); pa.close(); pb.feed(tb); pb.close()
    a, b = pa.result(), pb.result()
    if len(a) != len(b):
        return ["段落数变了：%d -> %d" % (len(a), len(b))]
    for i, x in enumerate(a):
        if i in mapping:
            got = _chunk_text(new, b[i], encoding)
            if got != mapping[i]:
                problems.append("P%d 期望 %r 得到 %r" % (i, mapping[i][:24], got[:24]))
        else:
            got = _chunk_text(new, b[i], "cp1252")
            if got != x["text"]:
                problems.append("P%d 未翻译却被改动：%r -> %r"
                                % (i, x["text"][:20], got[:20]))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--game", default=r"I:\LocalGames\Total Annihilation Kingdoms",
                    help="含 atlas/ Boneyards/ GC/ 的游戏根目录")
    ap.add_argument("--out", default=os.path.join(ROOT, "build_out", "web"))
    ap.add_argument("--encoding", default="gbk", choices=["gbk", "utf-8"])
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--show", type=int, default=0, help="打印前 N 处改动预览")
    args = ap.parse_args()

    rows = load_rows(args.text)
    by_file = group_by_file(rows)
    total = sum(len(v) for v in by_file.values())
    print("05 组已译条目 %d，涉及 %d 个文件，待写回位置 %d" % (
        sum(1 for r in rows if r.get("status") == "done"), len(by_file), total))

    ok_files = miss_files = 0
    done = missing_entries = 0
    bad = []
    shown = 0
    for rel in sorted(by_file):
        src_path = os.path.join(args.game, rel.replace("/", os.sep))
        if not os.path.exists(src_path):
            miss_files += 1
            print("  ! 缺文件 %s" % rel)
            continue
        with open(src_path, "rb") as f:
            raw = f.read()
        new, n, miss = rewrite(raw, by_file[rel], args.encoding)
        done += n
        missing_entries += len(miss)
        if miss:
            print("  ! %s 有 %d 个段落序号越界：%s" % (rel, len(miss), miss[:5]))
        probs = verify(raw, new, by_file[rel], args.encoding)
        if probs:
            bad.append((rel, probs[:3]))
        if args.show and shown < args.show:
            # 预览：打印第一处替换的上下文
            t = raw.decode("cp1252", errors="replace")
            _p = SpanText(t)
            _p.feed(t)
            _p.close()
            chunks = _p.result()
            for idx in sorted(by_file[rel]):
                if idx < len(chunks):
                    print("\n[%s P%d]\n  旧: %s\n  新: %s" % (
                        rel, idx, chunks[idx]["text"][:100], by_file[rel][idx][:100]))
                    shown += 1
                    break
        if args.write:
            dst = os.path.join(args.out, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            with open(dst, "wb") as f:
                f.write(new)
        ok_files += 1

    print("\n处理文件 %d（缺失 %d），写回段落 %d，序号越界 %d" % (
        ok_files, miss_files, done, missing_entries))
    if bad:
        print("校验失败 %d 个文件：" % len(bad))
        for rel, probs in bad[:10]:
            print("  %s" % rel)
            for p in probs:
                print("     ", p)
    else:
        print("校验通过：未翻译段落逐字未变，翻译段落与译文一致，段落总数不变")
    if args.write:
        print("产物目录：%s" % args.out)
    return 0 if not bad else 1


if __name__ == "__main__":
    sys.exit(main())
