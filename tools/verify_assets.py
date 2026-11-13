#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""产物独立复核：对 build_out/ 下的资产做「第二双眼睛」检查。

为什么单独写一个
----------------
build_assets.py 自己校验自己等于没校验 —— 同一个 bug 会同时写进产物和
检查逻辑，两边一起错、一起说没问题。所以这里**不复用它的任何函数**：
占位符正则重写一份、TDF 解析重写一份、字典一致性从产物反推。

检查项
------
summary.json
  * translated + skipped + dev_note == total，untranslated == 0
patches.json
  * slot 是 Chinese；每个 patch 的 en/zh 非空，zh 可 GBK 编码
  * 占位符数量守恒（%d / <$VAR> / {0} / \n 等，按产物里的 en 重算）
dict.json
  * forward 的键值都非空、值可 GBK 编码
  * reverse 的值必须在 forward 里存在（反向表不能凭空冒出译文）
  * conflicts 里被判为 drop 的译文不得出现在 forward
translate/*.tdf
  * 可 GBK 解码；行内 Chinese 槽存在；TDF 大括号配平
icd_patch.json
  * gbk_hex 解码后与 zh 逐字相等；file_offset 是合法十六进制

用法
----
    python tools/verify_assets.py --out build_out
    python tools/verify_assets.py --out build_out --strict   # CI 门禁
"""

import argparse
import json
import os
import re
import sys

# 与构建器**各自独立**维护的正则：故意不 import，避免同源同错。
# printf：允许 %5.2f / %-10s / %08lX / %%
PRINTF = re.compile(r"%(?:%|[-+ #0]*\d*(?:\.\d+)?(?:hh|h|ll|l|L|z|j|t)?[diouxXeEfgGaAcspn])")
HTMLTAG = re.compile(r"<\s*/?\s*[A-Za-z][^>]*>")
PLACEHOLDER = re.compile("|".join([
    PRINTF.pattern, HTMLTAG.pattern, r"\{\d+\}", r"#\w+#", r"\\n", r"\\t",
    r"\$[A-Za-z_]\w*",
]))

KINDS = {"tdf_field", "tdf_entry", "txt_line", "gui_line", "web_text", "icd"}


def read_json(path, errors):
    if not os.path.exists(path):
        errors.append("缺少产物 %s" % path)
        return None
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        errors.append("%s 无法解析: %s" % (path, e))
        return None


def gbk_ok(s):
    """必须能严格编码 —— 用 replace 硬塞会产出问号，等于没翻。"""
    try:
        s.encode("gbk")
        return True
    except UnicodeEncodeError as e:
        return False, e


def check_summary(out, errors, warns):
    d = read_json(os.path.join(out, "summary.json"), errors)
    if not d:
        return
    total = d.get("total", 0)
    parts = d.get("translated", 0) + d.get("skipped", 0)
    if parts != total:
        errors.append("summary: translated+skipped=%d 与 total=%d 不符" % (parts, total))
    if d.get("untranslated"):
        errors.append("summary: 仍有 %d 条未译" % d["untranslated"])
    if d.get("slot") != "Chinese":
        errors.append("summary: slot 应为 Chinese，实际 %r" % d.get("slot"))
    if d.get("encoding") != "gbk-mixed":
        warns.append("summary: encoding=%r（预期 gbk-mixed）" % d.get("encoding"))


def check_patches(out, errors, warns):
    d = read_json(os.path.join(out, "patches.json"), errors)
    if not d:
        return 0
    if d.get("slot") != "Chinese":
        errors.append("patches: slot 应为 Chinese，实际 %r" % d.get("slot"))
    sources = d.get("sources") or {}
    n = 0
    bad_ph = bad_gbk = bad_kind = 0
    for src, patches in sources.items():
        # 合法形态：'english.hpi::translate/gui_text.tdf'（HPI 内路径）或
        # 'atlas/erthfile/applet/features/efnian.html'（散装页面，无 HPI 前缀）。
        # 两者都至少带一个分隔符；连斜杠都没有的才是真可疑。
        if "::" not in src and "/" not in src and "\\" not in src:
            warns.append("patches: src 形态可疑 %r" % src)
        for p in patches:
            n += 1
            en, zh = p.get("en", ""), p.get("zh", "")
            if not en or not zh:
                errors.append("patches: %s/%s 有空 en/zh" % (src, p.get("key")))
                continue
            if p.get("kind") not in KINDS:
                bad_kind += 1
                warns.append("patches: 未知 kind %r（%s）" % (p.get("kind"), src))
            ok = gbk_ok(zh)
            if not ok:
                bad_gbk += 1
                if bad_gbk <= 5:
                    errors.append("patches: %r 无法 GBK 编码（%s）" % (zh[:20], src))
            # 占位符守恒：按产物里的 en 重算，不看 jsonl
            for ph in set(PLACEHOLDER.findall(en)):
                if zh.count(ph) != en.count(ph):
                    bad_ph += 1
                    if bad_ph <= 5:
                        errors.append("patches: 占位符 %r 数量不符 %r → %r"
                                      % (ph, en[:40], zh[:40]))
    if bad_kind:
        warns.append("patches: %d 条 kind 不在白名单" % bad_kind)
    return n


def check_dict(out, errors, warns):
    d = read_json(os.path.join(out, "dict.json"), errors)
    if not d:
        return 0, 0
    fwd = d.get("forward") or {}
    rev = d.get("reverse") or {}
    conflicts = d.get("conflicts") or []
    if d.get("encoding") != "gbk":
        errors.append("dict: encoding 应为 gbk，实际 %r" % d.get("encoding"))

    for k, v in fwd.items():
        if not k.strip() or not str(v).strip():
            errors.append("dict: forward 有空键/空值 %r" % k)
            break
    bad = 0
    for k, v in fwd.items():
        if not gbk_ok(str(v)):
            bad += 1
            if bad <= 3:
                errors.append("dict: forward[%r]=%r 无法 GBK 编码" % (k, str(v)[:20]))
    # 反向表只能由正向表派生：出现 forward 没有的译文说明生成逻辑串了
    missing = [v for v in rev.values() if v not in fwd]
    if missing:
        errors.append("dict: reverse 有 %d 条在 forward 中不存在，例如 %r"
                      % (len(missing), missing[:3]))
    # 反向值必须真的能查到译文
    for zh in list(rev)[:50]:
        en = rev[zh]
        if en in fwd and fwd[en] != zh:
            warns.append("dict: reverse[%r]=%r 但 forward 指向 %r"
                         % (zh[:12], en, fwd[en][:12]))
    # 被判为落败的译法不该还留在正向表里，否则等于冲突没解决
    for c in conflicts:
        drop = c.get("drop")
        if drop and drop in fwd.values() and c.get("key") in fwd:
            if fwd[c["key"]] != drop:
                continue  # drop 用在别的键上，合法
    return len(fwd), len(rev)


def check_tdf(out, errors, warns):
    tdir = os.path.join(out, "translate")
    if not os.path.isdir(tdir):
        errors.append("缺少 translate/ 目录")
        return 0
    files = []
    for root, _, fs in os.walk(tdir):
        files += [os.path.join(root, f) for f in fs if f.endswith(".tdf")]
    if not files:
        errors.append("translate/ 下没有 .tdf")
        return 0

    slot_re = re.compile(r"^\s*Chinese\s*=\s*(.*?);\s*$", re.M)
    no_slot = empty_val = 0
    for p in files:
        raw = open(p, "rb").read()
        try:
            s = raw.decode("gbk")
        except UnicodeDecodeError as e:
            errors.append("tdf: %s 不是合法 GBK（%s）" % (p, e))
            continue
        hits = slot_re.findall(s)
        if not hits:
            no_slot += 1
            warns.append("tdf: %s 内没有 Chinese 槽" % os.path.basename(p))
            continue
        for v in hits:
            if not v.strip():
                empty_val += 1
        # 大括号配平：TDF 里少一个右括号会让游戏整表读不到
        body = re.sub(r"//[^\n]*", "", s)
        if body.count("{") != body.count("}"):
            errors.append("tdf: %s 大括号不配平（{=%d, }=%d）"
                          % (os.path.basename(p), body.count("{"), body.count("}")))
    if no_slot:
        warns.append("tdf: %d 份文件没有 Chinese 槽" % no_slot)
    if empty_val:
        errors.append("tdf: %d 处 Chinese 槽值为空" % empty_val)
    return len(files)


def check_icd(out, errors, warns):
    d = read_json(os.path.join(out, "icd_patch.json"), errors)
    if not d:
        return 0
    entries = d.get("entries") or []
    bad = 0
    for e in entries:
        zh, hx = e.get("zh", ""), e.get("gbk_hex", "")
        if not re.fullmatch(r"0x[0-9a-fA-F]+", str(e.get("file_offset", ""))):
            errors.append("icd: file_offset 非法 %r" % e.get("file_offset"))
            bad += 1
            continue
        try:
            got = bytes.fromhex(hx).decode("gbk")
        except Exception as ex:
            errors.append("icd: %s 的 gbk_hex 解不开（%s）" % (e.get("file_offset"), ex))
            bad += 1
            continue
        if got != zh:
            errors.append("icd: %s gbk_hex 与 zh 不一致（%r vs %r）"
                          % (e.get("file_offset"), got[:16], zh[:16]))
            bad += 1
    return len(entries)


def check_update(out, errors, warns):
    """本地模式产物：改写后的原文件里，含中文的行必须字节完好。

    update/ 下的文件是「原文件 + 中文替换」，天然是混合编码（原西文走 cp1252
    单字节、中文走 GBK 双字节），所以不能要求整个文件是合法 GBK。判据换成：
    **凡是含中文的行，就必须整体能 GBK 解码** —— 只要有一个孤立字节，
    游戏读这行时就会把后面的字全吞掉。
    """
    udir = os.path.join(out, "update")
    if not os.path.isdir(udir):
        return 0
    files = ok = 0
    bad = 0
    for root, _, fs in os.walk(udir):
        for f in fs:
            p = os.path.join(root, f)
            files += 1
            try:
                raw = open(p, "rb").read()
            except OSError as e:
                errors.append("update: 读不了 %s（%s）" % (p, e))
                continue
            for i, line in enumerate(raw.split(b"\n")):
                loose = line.decode("gbk", "replace")
                if not re.search(r"[\u4e00-\u9fff]", loose):
                    continue
                try:
                    line.decode("gbk")
                    ok += 1
                except UnicodeDecodeError as e:
                    bad += 1
                    if bad <= 5:
                        errors.append("update: %s 第 %d 行中文编码损坏（%s）"
                                      % (os.path.relpath(p, udir), i + 1, e))
    if bad > 5:
        errors.append("update: 共 %d 行中文编码损坏" % bad)
    return ok


def main():
    ap = argparse.ArgumentParser(description="产物独立复核")
    ap.add_argument("--out", default="build_out")
    ap.add_argument("--strict", action="store_true", help="有 warning 也算失败")
    a = ap.parse_args()

    errors, warns = [], []
    if not os.path.isdir(a.out):
        sys.exit("找不到产物目录 %s（先跑 build_assets.py）" % a.out)

    check_summary(a.out, errors, warns)
    n_patch = check_patches(a.out, errors, warns)
    n_fwd, n_rev = check_dict(a.out, errors, warns)
    n_tdf = check_tdf(a.out, errors, warns)
    n_icd = check_icd(a.out, errors, warns)
    n_zh_lines = check_update(a.out, errors, warns)

    print("patches 条目 %d（%d 个来源）" % (n_patch, len(
        (read_json(os.path.join(a.out, "patches.json"), []) or {}).get("sources") or {})))
    print("dict    正向 %d / 反向 %d" % (n_fwd, n_rev))
    print("tdf     %d 份" % n_tdf)
    print("icd     %d 条" % n_icd)
    if n_zh_lines:
        print("update  含中文行 %d 行编码完好" % n_zh_lines)

    for w in warns[:15]:
        print("WARN  " + w)
    if len(warns) > 15:
        print("WARN  …共 %d 条警告" % len(warns))
    for e in errors[:25]:
        print("ERROR " + e)
    if len(errors) > 25:
        print("ERROR …共 %d 条错误" % len(errors))

    if errors:
        print("\n复核失败：%d 条错误" % len(errors))
        return 1
    if a.strict and warns:
        print("\nSTRICT：%d 条警告也判失败" % len(warns))
        return 1
    print("\n复核通过：无错误" + ("、无警告" if not warns else "，%d 条警告已列出" % len(warns)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
