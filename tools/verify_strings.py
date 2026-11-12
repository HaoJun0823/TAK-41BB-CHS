#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""独立校验 text/*.jsonl —— 不参与生成，专用于在 CI 上拦住坏数据。

检查项
------
1. 文件是 UTF-8 无 BOM、无零宽字符、无 front-matter AIGC 指纹块。
2. 每行是合法 JSON，必需字段齐备，id 全局唯一。
3. status 取值合法；status=done 时 zh 非空且所有占位符在译文里仍存在。
4. targets 非空且每条有 src/key。
5. manifest.json 的条数与实际文件一致。
6. 可选 --against-game：重新从干净 41BB 抽取一遍，比对英文原文集合，
   防止翻译脚本误改 en 或游戏基线被换过。

用法
----
    python tools/verify_strings.py --text text
    python tools/verify_strings.py --text text --against-game <41BB目录>
"""

import argparse
import collections
import json
import os
import sys
import tempfile

REQUIRED = ("id", "en", "zh", "status", "ctx", "kind", "placeholders", "flags", "targets")
VALID_STATUS = {"pending", "done", "skip", "review"}


def check_bytes(raw, label, errors):
    if raw[:3] == b"\xef\xbb\xbf":
        errors.append("%s: 检测到 UTF-8 BOM" % label)
    zwsp = raw.count(b"\xe2\x80\x8b")
    zwj = raw.count(b"\xe2\x80\x8d")
    if zwsp or zwj:
        errors.append("%s: 检测到零宽字符 ZWSP=%d ZWJ=%d（疑似 AIGC 指纹）"
                      % (label, zwsp, zwj))
    head = raw[:400].decode("utf-8", "ignore")
    if head.lstrip().startswith("---") and "AIGC" in head:
        errors.append("%s: 检测到 front-matter AIGC 块" % label)
    try:
        raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        errors.append("%s: 不是合法 UTF-8: %s" % (label, exc))


def load_rows(path, errors):
    raw = open(path, "rb").read()
    check_bytes(raw, os.path.basename(path), errors)
    rows = []
    for i, line in enumerate(raw.decode("utf-8", "replace").splitlines(), 1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except Exception as exc:
            errors.append("%s:%d JSON 解析失败: %s" % (path, i, exc))
    return rows


def main():
    ap = argparse.ArgumentParser(description="校验明文待译表")
    ap.add_argument("--text", default="text")
    ap.add_argument("--against-game", help="干净 41BB 目录，用于比对英文原文")
    a = ap.parse_args()

    errors = []
    files = sorted(f for f in os.listdir(a.text) if f.endswith(".jsonl"))
    seen_ids = {}
    all_by_file = {}
    status_count = collections.Counter()
    print("%-26s %8s %8s %8s %8s" % ("文件", "总条数", "pending", "done", "review"))
    for fn in files:
        p = os.path.join(a.text, fn)
        rows = load_rows(p, errors)
        all_by_file[fn] = rows
        local = collections.Counter()
        for i, r in enumerate(rows, 1):
            for k in REQUIRED:
                if k not in r:
                    errors.append("%s:%d 缺字段 %s" % (fn, i, k))
            if not isinstance(r.get("en"), str) or not r["en"].strip():
                errors.append("%s:%d en 为空" % (fn, i))
            if not isinstance(r.get("zh"), str):
                errors.append("%s:%d zh 不是字符串" % (fn, i))
            if r.get("status") not in VALID_STATUS:
                errors.append("%s:%d 非法 status %r" % (fn, i, r.get("status")))
            rid = r.get("id")
            if rid in seen_ids:
                errors.append("%s:%d id 重复: %s（已在 %s）"
                              % (fn, i, rid, seen_ids[rid]))
            else:
                seen_ids[rid] = fn
            if not isinstance(r.get("targets"), list) or not r["targets"]:
                errors.append("%s:%d targets 为空" % (fn, i))
            else:
                for t in r["targets"]:
                    if "src" not in t or "key" not in t:
                        errors.append("%s:%d target 缺 src/key" % (fn, i))
                        break
            st = r.get("status")
            status_count[st] += 1
            local[st] += 1
            if st == "done":
                if not r.get("zh"):
                    errors.append("%s:%d status=done 但 zh 为空" % (fn, i))
                for ph in r.get("placeholders", []):
                    if ph not in r.get("zh", ""):
                        errors.append("%s:%d 占位符丢失: %r" % (fn, i, ph))
        print("%-26s %8d %8d %8d %8d"
              % (fn, len(rows), local["pending"], local["done"], local["review"]))

    # manifest 一致性
    mp = os.path.join(a.text, "manifest.json")
    if os.path.exists(mp):
        m = json.load(open(mp, encoding="utf-8"))
        for g in m.get("groups", []):
            fn = g["file"]
            actual = len(all_by_file.get(fn, []))
            if g["entries"] != actual:
                errors.append("manifest %s 记 %d 条，实际 %d 条"
                              % (fn, g["entries"], actual))
    else:
        errors.append("缺少 manifest.json")

    # 与游戏基线比对
    if a.against_game:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import extract_strings
        with tempfile.TemporaryDirectory() as td:
            tmp_text = os.path.join(td, "text")
            extract_strings.build(a.against_game, tmp_text, include_web=True,
                                  check=False)
            for fn, rows in all_by_file.items():
                fresh = os.path.join(tmp_text, fn)
                if not os.path.exists(fresh):
                    errors.append("%s 在基线重抽中缺失" % fn)
                    continue
                want = {r["en"] for r in load_rows(fresh, [])}
                have = {r["en"] for r in rows}
                missing = want - have
                extra = have - want
                if missing or extra:
                    errors.append("%s 英文原文与基线不符：少 %d 条 / 多 %d 条（例：%s）"
                                  % (fn, len(missing), len(extra),
                                     ", ".join(repr(x[:40]) for x in list(extra)[:3])))
        print("已与游戏基线重新抽取结果比对。")

    print("-" * 60)
    print("状态汇总: " + ", ".join("%s=%d" % kv for kv in sorted(status_count.items())))
    if errors:
        print("校验失败 %d 项：" % len(errors))
        for e in errors[:30]:
            print("   -", e)
        if len(errors) > 30:
            print("   ...(还有 %d 项)" % (len(errors) - 30))
        return 1
    print("校验通过：%d 个文件、%d 条唯一 id，无格式/编码/占位符问题。"
          % (len(files), len(seen_ids)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
