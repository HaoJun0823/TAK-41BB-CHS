#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""单条重译可疑条目：结构化 JSON 返回值、专名错、英文残留。

批量请求偶尔会让模型把长段落返回成 {"type":"paragraph","text":...}，
或者把 Lihr 译成“利尔/艾尔辛”。这里逐条重发（单条不会触发结构化输出），
校验通过才覆盖，失败保留原译文并标 review。

用法：
    python tools/retranslate_suspects.py --text text --api-file <凭据> --dry-run
    python tools/retranslate_suspects.py --text text --api-file <凭据> --write
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from concurrent.futures import ThreadPoolExecutor

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import translate as T          # noqa: E402
import check_quality as Q      # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ORDER = ["01_ui_tdf", "02_units", "03_missions", "04_gui", "05_web", "06_icd"]


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def save(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def struct_bad(zh):
    """JSON 外壳且没有可取的正文字段。"""
    if not zh or zh[0] not in "{[":
        return False
    try:
        obj = json.loads(zh)
    except Exception:
        return True
    if isinstance(obj, dict):
        if any(k in obj for k in ("zh", "text", "translation", "content", "value", "message")):
            return False
        if len(obj) == 1:
            k = list(obj)[0]
            return not (k and ord(k[0]) > 127)
        return True
    return not isinstance(obj, list)


def main():
    ap = argparse.ArgumentParser(description="单条重译可疑条目")
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--terms", default=os.path.join(ROOT, "text", "terms.json"))
    ap.add_argument("--api-file", default=os.environ.get("TAK_API_FILE"))
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--show", type=int, default=40)
    a = ap.parse_args()

    terms = T.load_terms(a.terms)
    base, key = T.load_api(a.api_file)
    client = T.Client(base, key, a.model, None)
    client.system = T.SYSTEM.format(terms=T.terms_prompt(terms))

    jobs = []
    for code in ORDER:
        p = os.path.join(a.text, code + ".jsonl")
        if not os.path.exists(p):
            continue
        rows = load(p)
        for r in rows:
            if r["status"] != "done" or not r["zh"]:
                continue
            why = []
            if struct_bad(r["zh"]):
                why.append("json_struct")
            if Q.check_terms([r], terms):
                why.append("term")
            if Q.check_foreign_words([r]):
                why.append("foreign")
            if why:
                jobs.append((p, rows, r, ",".join(why)))

    print("可疑 %d 条" % len(jobs))
    for _, _, r, why in jobs[:a.show]:
        print("  [%s] %-34s | %s" % (why, r["en"][:34], r["zh"][:34]))
    if not a.write or not jobs:
        return 0

    def work(job):
        _, _, r, _ = job
        phs = r["placeholders"]
        masked, mapping = T.mask_placeholders(r["en"], phs) if phs else (r["en"], [])
        ctx = T.make_ctx(r) + ("；@@0@@ 这类标记必须原样保留" if mapping else "")
        try:
            out = client.translate_batch([{"en": masked, "ctx": ctx}])
            raw = T.lookup(out or {}, masked)
            if raw is None and out and len(out) == 1:
                raw = list(out.values())[0]
            zh = T.unmask_placeholders(T.normalize(raw, masked), mapping)
            probs = T.problems(r["en"], zh, phs)
            if probs:
                return job, None, ",".join(probs)
            return job, zh, None
        except Exception as e:
            return job, None, str(e)[:120]

    by_file = {}
    with ThreadPoolExecutor(a.workers) as ex:
        for job, zh, err in ex.map(work, jobs):
            p, rows, r, _ = job
            by_file.setdefault(p, (rows, []))
            if zh:
                r["zh"], r["status"] = zh, "done"
                by_file[p][1].append((r["en"], zh))
            else:
                r["status"] = "review"
                by_file[p][1].append((r["en"], "FAILED:%s" % err))

    for p, (rows, _) in by_file.items():
        save(p, rows)
        print("已写回", p)
    return 0


if __name__ == "__main__":
    sys.exit(main())
