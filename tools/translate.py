#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""用 LLM API 批量回填 text/*.jsonl 的 zh 与 status。

设计约束（见 text/README.md 的翻译契约）：
  * 只写 zh / status，其余字段原样保留；
  * 按 id 幂等：默认只处理 pending，done 跳过，review 需显式 --retry-review；
  * placeholders 必须原样保留，缺失即判失败；
  * 07_icd_candidates 与 status=review 的条目默认不送翻；
  * 同一英文跨组共享译文（保证同一句在 UI 与单位表里译法一致）。

API 配置不写进仓库：用 --api-file 指向本地凭据文件，或设置环境变量
TAK_API_FILE / SILICONFLOW_API_KEY。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import threading
import time
import unicodedata
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_TERMS = os.path.join(ROOT, "text", "terms.json")
DEFAULT_CACHE = os.path.join(ROOT, "build_out", "cache", "translations.json")
DEFAULT_REPORT = os.path.join(ROOT, "build_out", "translate_report.json")

GROUP_ORDER = ["01_ui_tdf", "02_units", "03_missions", "04_gui", "05_web", "06_icd"]
SKIP_GROUPS = {"07_icd_candidates"}

# 快捷键 / 单键名：这些是按键显示串，翻了会破坏配置界面
KEY_RE = re.compile(
    r"^(?:"
    r"(?:Ctrl|Shift|Alt|Cmd)(?:[-+](?:Ctrl|Shift|Alt|Cmd|F\d{1,2}|[A-Za-z0-9]+))+"
    r"|F\d{1,2}"
    r"|Tab|Enter|Return|Esc|Escape|Space|Backspace|Delete|Insert|Home|End"
    r"|Page ?Up|Page ?Down|Up|Down|Left|Right"
    r")$",
    re.I,
)

# 服务器模板变量 / 纯占位串，玩家看不到，翻译只会破坏模板
TEMPLATE_RE = re.compile(r"^(?:<\$[^>]*>[\s/|,;:·()\-]*)+$")

# 产品名统一（模型有时保留英文，有时译一半，这里兜底）
PRODUCT_REPLACE = (
    ("Total Annihilation: Kingdoms", "横扫千军：王国风云"),
    ("Total Annihilation:Kingdoms", "横扫千军：王国风云"),
    ("Total Annihilation", "横扫千军"),
)

# 游戏文本最终要写成 GBK 双字节。若译文里混进「GBK 编不了、cp1252 能编」的
# 字符（• ™ © 之类），打包时会被写成单字节，把整串 GBK 序列撑错位：
#   '杜马·戈残骸' 的 '·'(U+00B7) 一旦走 cp1252 就是 0xB7，
#   输出 11 字节（奇数）→ 整张 npcs.tdf 解不开 → 游戏读不到任何词条。
# GBK 本身覆盖了全部常用中文标点（— ‘’“” … 、。，！？《》均可用），
# 所以只需给少数几个缺口配等价物，其余编不了的一律清掉。
GBK_FALLBACK = {
    # • → ●（A1F1，实心圆点）：实测 211 处全是任务简报的行首列表符号，
    # 用实心点最贴近原版观感；· (A1A4) 太窄，当项目符号会看不清。
    "\u2022": "\u25cf",
    "\u00a0": " ",        # 不间断空格 → 普通空格
    "\u2122": "",         # ™ GBK 无
    "\u00a9": "",         # © GBK 无（上下文已写"版权所有"）
    "\u00ae": "",         # ® GBK 无
}


def gbk_safe(s):
    """把 GBK 编不了的字符换成可编码等价物，从源头掐掉打包期的字节错位。"""
    out = []
    for ch in s:
        try:
            ch.encode("gbk")
        except UnicodeEncodeError:
            rep = GBK_FALLBACK.get(ch)
            if rep is None:
                # 拉丁字母去变音后可能还能用；阿拉伯文等直接丢
                rep = unicodedata.normalize("NFKD", ch).encode("ascii", "ignore").decode()
            out.append(rep)
        else:
            out.append(ch)
    return "".join(out)

SYSTEM = """你是《横扫千军：王国风云》(Total Annihilation: Kingdoms) 的简体中文本地化译者。
用户会给出一个 JSON 数组，每项含 en（英文原文）、ctx（使用场景）。
请逐条翻译成简体中文，并输出一个 JSON 对象：键必须原样照抄该项的 en 原文，值是译文。

硬性要求：
1. 只输出 JSON 对象本身，不要解释、不要 markdown 代码块、不要多个候选。
1b. 每个 en 都必须出现且只出现一次；不得合并、跳过或改写键名。
2. 忠实且简洁，不增删信息。界面按钮/标签用短词，不要写成句子。
3. 不要擅自加书名号《》、引号、括号注释；原文有才保留。
4. %s %d %ld %u %02x %% 这类 printf 占位符和 <...> 标签必须原样保留，数量与顺序不能变。
5. 原文行首的符号（如 •）必须保留。
6. 下列专名一律用固定译法，不得按上下文意译，也不得另造译名：
{terms}
7. 快捷键（如 Ctrl-Shift-6）、纯符号、版本号、文件名，原样返回，不要翻译。
8. 同一个英文词在本批内译法保持一致；拿不准的宁可直译，不要发挥。
"""


# ---------------------------------------------------------------- API 配置

def load_api(path):
    """从本地凭据文件读 base_url / api_key。文件格式：地址：... / 口令：..."""
    base = key = None
    if path:
        with open(path, encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip()
                if line.startswith("地址："):
                    base = line[len("地址："):].strip()
                elif line.startswith("口令："):
                    key = line[len("口令："):].strip()
    base = base or os.environ.get("SILICONFLOW_BASE_URL")
    key = key or os.environ.get("SILICONFLOW_API_KEY")
    if not key:
        sys.exit("缺少 API 凭据：用 --api-file 指定文件，或设置 SILICONFLOW_API_KEY")
    return (base or "https://api.siliconflow.cn/v1").rstrip("/"), key


# ---------------------------------------------------------------- 词条处理

def load_terms(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return {k: v for k, v in data.items() if not k.startswith("_")}


def terms_prompt(terms):
    if not terms:
        return "（无）"
    return "，".join("%s=%s" % (k, v) for k, v in terms.items())


def skip_reason(en):
    s = en.strip()
    if not s:
        return "empty"
    if KEY_RE.match(s):
        return "keybinding"
    if TEMPLATE_RE.match(s):
        return "template_var"
    # 资源文件名 / 开发备注，玩家看不到
    if re.search(r"\.(pcx|bmp|tga|png|jpe?g|gif|wav|bik|gaf)\b", s, re.I):
        return "asset_ref"
    # takmission03_MT / unitname_DH 这类内部标识
    if re.fullmatch(r"[a-z][a-z0-9]*(?:_[A-Za-z0-9]+)+", s) and re.search(r"[A-Z]", s):
        return "internal_id"
    # SepheryneSepheryne 这类重复拼接的占位名
    if re.fullmatch(r"(.{4,})\1", s):
        return "duplicated_id"
    # 日期/数字格式串，翻了会破坏格式化
    if re.search(r"\b(dddd|MMMM|yyyy|hh:mm|mm:ss)\b", s):
        return "date_format"
    # 产品名/系统名，按原样保留
    if re.fullmatch(r"(Windows( NT| 95| 98| 2000| XP)?|DirectX|Internet Explorer|"
                    r"Netscape|Boneyards|TA:? Kingdoms)", s, re.I):
        return "product_name"
    # 扫描出来的乱码（无元音、长短不齐的片段）
    if re.search(r"[A-Za-z]{2,}", s) and not re.search(r"[aeiouAEIOU]", s):
        return "no_vowel"
    if not re.search(r"[A-Za-z]", s):
        return "no_latin"
    if re.fullmatch(r"[vV]?\d+(?:\.\d+)+[a-zA-Z]?", s):
        return "version"
    # 只有占位符/符号，没有可译单词
    if not re.search(r"[A-Za-z]{2,}", s):
        return "no_word"
    return None


def make_ctx(row):
    bits = [row.get("ctx", "")]
    for fl in row.get("flags", []):
        if fl.startswith("field:name"):
            bits.append("这是单位或建筑的名称，译成 2-8 字的专名")
        elif fl.startswith("field:description"):
            bits.append("这是单位或建筑的简短说明")
        elif fl.startswith("field:missionname"):
            bits.append("这是关卡名称")
        elif fl.startswith("gui_fixed_length:"):
            n = fl.split(":", 1)[1]
            try:
                bits.append("界面控件只有约 %d 个英文字符宽，译文请控制在 %d 个汉字内"
                            % (int(n), max(2, int(n) // 2)))
            except ValueError:
                pass
        elif fl == "html":
            bits.append("游戏内嵌网页正文，保留原有标点风格和 HTML 标签")
        elif fl == "english_from_key":
            bits.append("这是英文词条名，同时被当作显示文本")
        elif fl == "plain_line":
            bits.append("任务简报的一行，保持行首符号")
    phs = row.get("placeholders") or []
    if phs:
        bits.append("必须原样保留这些片段：" + " ".join(phs))
    return "；".join(b for b in bits if b)


def flatten(value):
    """模型偶尔会返回 {"type":"paragraph","text":"..."} 这种嵌套结构，取出正文。"""
    if isinstance(value, dict):
        for k in ("zh", "text", "translation", "content", "value", "message"):
            if k in value:
                return flatten(value[k])
        parts = [flatten(v) for v in value.values() if isinstance(v, (str, dict, list))]
        return "".join(p for p in parts if p)
    if isinstance(value, list):
        return "".join(flatten(v) for v in value if isinstance(v, (str, dict, list)))
    s = str(value).strip()
    if s[:1] in "{[":
        try:
            return flatten(json.loads(s))
        except Exception:
            pass
    return s


def normalize(zh, en):
    """去掉模型爱加的外壳，保持与原文同构。"""
    if zh is None:
        return ""
    s = flatten(zh).strip()
    s = re.sub(r"^(?:译文|翻译|translation)\s*[:：]\s*", "", s, flags=re.I)
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = s.strip()
    # 整体包裹的引号/书名号：原文没有就去掉
    for a, b in (('"', '"'), ("“", "”"), ("'", "'"), ("《", "》")):
        if len(s) >= 2 and s.startswith(a) and s.endswith(b):
            if a not in en and b not in en:
                s = s[1:-1].strip()
    # 原文没有换行就把译文压成一行，避免撑破 TDF
    if "\n" not in en:
        s = re.sub(r"\s*\n\s*", " ", s)
    s = re.sub(r"[ \t]+", " ", s).strip()
    # 原文没有句末标点，就不要替玩家加感叹号/句号（UI 标签最忌这个）
    if s and en.rstrip() and en.rstrip()[-1] not in ".!?;:：。！？":
        if s[-1] in "。！？；":
            s = s[:-1].rstrip()
    for a, b in PRODUCT_REPLACE:
        s = s.replace(a, b)
    return gbk_safe(s)


def problems(en, zh, phs):
    p = []
    if not zh.strip():
        p.append("empty")
        return p
    if not re.search(r"[\u4e00-\u9fff]", zh) and re.search(r"[A-Za-z]{2,}", en):
        p.append("no_cjk")
    for ph in phs:
        if zh.count(ph) != en.count(ph):
            p.append("placeholder:%s" % ph)
    # 中文通常比英文短；译文明显长于原文，多半是错位或把说明塞进了标签
    if len(zh) > max(24, len(en) * 1.8):
        p.append("too_long")
    if zh.strip() == en.strip() and len(en.split()) > 1 and not KEY_RE.match(en.strip()):
        p.append("identical")
    # 项目符号：GBK 没有 •(U+2022)，归一化后会变成 ●(U+25CF)，两者都算保留
    if en.startswith("•") and zh[:1] not in ("•", "●"):
        p.append("bullet_lost")
    return p


# ---------------------------------------------------------------- 模型调用

class Client(object):
    def __init__(self, base, key, model, fallback_model=None, timeout=240):
        self.url = base + "/chat/completions"
        self.key = key
        self.model = model
        self.fallback_model = fallback_model
        self.timeout = timeout
        self.json_mode = True
        self.system = ""
        self.lock = threading.Lock()

    def _post(self, payload):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.url, data=data,
            headers={"Authorization": "Bearer " + self.key,
                     "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=self.timeout) as r:
            return json.loads(r.read().decode("utf-8"))

    def chat(self, model, messages, max_tokens, json_mode):
        payload = {
            "model": model,
            "messages": messages,
            "temperature": 0.1,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        last = None
        for attempt in range(4):
            try:
                with self.lock:
                    use_json = self.json_mode
                if use_json:
                    payload["response_format"] = {"type": "json_object"}
                else:
                    payload.pop("response_format", None)
                d = self._post(payload)
                return d["choices"][0]["message"]["content"]
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:300]
                except Exception:
                    pass
                last = "HTTP %s %s" % (e.code, body)
                if e.code == 400 and self.json_mode:
                    # 个别模型不支持 response_format，降级重试
                    with self.lock:
                        self.json_mode = False
                    continue
                if e.code in (429, 500, 502, 503, 504):
                    time.sleep(min(30, 3 * (attempt + 1) ** 2))
                    continue
                break
            except Exception as e:  # 超时 / 连接中断
                last = "%s: %s" % (type(e).__name__, e)
                time.sleep(2 * (attempt + 1))
        raise RuntimeError(last or "unknown error")

    def translate_batch(self, items):
        body = json.dumps(items, ensure_ascii=False)
        # Qwen3 默认会先思考；预算给少了会被截断成半截 JSON，
        # 进而触发大量单条重试（越跑越慢）。所以下限必须够高。
        chars = sum(len(x["en"]) for x in items)
        max_tokens = min(8192, max(4096, 1500 + int(chars * 1.5)))
        out = self.chat(self.model,
                        [{"role": "system", "content": self._system},
                         {"role": "user", "content": body}],
                        max_tokens, self.json_mode)
        return parse_object(out)

    def translate_single_fallback(self, en):
        """批量失败后的兜底：单条、朴素提示、可换小模型。"""
        if not self.fallback_model:
            return None
        prompt = ("Translate the following segment into Chinese, "
                  "without additional explanation.\n\n" + en)
        out = self.chat(self.fallback_model,
                        [{"role": "user", "content": prompt}], 1024, False)
        return out.strip()

    @property
    def _system(self):
        return self.system


def mask_placeholders(en, phs):
    """把占位符换成 ASCII 哨兵再送翻，避免模型把 <$PLAYER2|$> 一起译掉。"""
    masked, mapping = en, []
    for i, ph in enumerate(sorted(set(phs), key=len, reverse=True)):
        tok = "@@%d@@" % i
        if ph in masked:
            masked = masked.replace(ph, tok)
            mapping.append((tok, ph))
    return masked, mapping


def unmask_placeholders(zh, mapping):
    for tok, ph in mapping:
        zh = zh.replace(tok, ph)
    return zh


def lookup(obj, en):
    """优先精确匹配；模型偶尔会改动大小写或首尾空白，再做一次归一化匹配。"""
    if en in obj:
        return obj[en]
    t = en.strip().lower()
    for k, v in obj.items():
        if k.strip().lower() == t:
            return v
    return None


def parse_object(text):
    if not text:
        return None
    s = text.strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    i, j = s.find("{"), s.rfind("}")
    if i < 0 or j <= i:
        return None
    try:
        obj = json.loads(s[i:j + 1])
    except Exception:
        return None
    if isinstance(obj, list):
        # 有些模型会返回 [{"en":...,"zh":...}, ...]，等价转换
        out = {}
        for it in obj:
            if isinstance(it, dict) and "en" in it:
                out[str(it["en"])] = it.get("zh") or it.get("translation") or ""
        return out or None
    if not isinstance(obj, dict):
        return None
    return {str(k): v for k, v in obj.items()}


# ---------------------------------------------------------------- 批处理

def batch_size_for(rows):
    avg = sum(len(r["en"]) for r in rows) / float(len(rows))
    if avg <= 60:
        return 24
    if avg <= 200:
        return 8
    return 4


def chunk(rows):
    if not rows:
        return []
    out, cur, size = [], [], batch_size_for(rows)
    for r in rows:
        cur.append(r)
        if len(cur) >= size:
            out.append(cur)
            cur = []
    if cur:
        out.append(cur)
    return out


class Runner(object):
    def __init__(self, client, cache, cache_path, terms_hash, workers):
        self.client = client
        self.cache = cache
        self.cache_path = cache_path
        self.terms_hash = terms_hash
        self.workers = workers
        self.lock = threading.Lock()
        self.dirty = 0
        self.stats = {"done": 0, "skip": 0, "review": 0, "cached": 0}
        self.failures = []
        self.retry_review = False
        self.limit = 0
        self.seen = 0

    def get(self, en):
        with self.lock:
            return self.cache.get(en)

    def put(self, en, zh, how):
        with self.lock:
            self.cache[en] = zh
            self.stats["done" if how != "review" else "review"] += 1
            self.dirty += 1
            if self.dirty % 40 == 0:
                self.save_cache()

    def save_cache(self):
        if not self.cache_path:
            return
        os.makedirs(os.path.dirname(self.cache_path), exist_ok=True)
        tmp = self.cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8", newline="\n") as f:
            json.dump({"schema": 1, "terms_hash": self.terms_hash,
                       "map": self.cache}, f, ensure_ascii=False, indent=0)
            f.write("\n")
        os.replace(tmp, self.cache_path)

    def run_group(self, rows, group):
        by_en = {}       # en -> 该组内所有同文行（必须一起回写）
        todo = {}        # en -> 代表行
        for r in rows:
            if r.get("status") in ("done", "skip"):
                continue
            if r.get("status") == "review" and not self.retry_review:
                continue
            reason = skip_reason(r["en"])
            if reason:
                r["status"] = "skip"
                with self.lock:
                    self.stats["skip"] += 1
                continue
            by_en.setdefault(r["en"], []).append(r)
            todo.setdefault(r["en"], r)

        # 先吃缓存：命中就整组同文行一起写
        for en in list(todo):
            cached = self.get(en)
            if cached is None:
                continue
            zh = normalize(cached, en)
            if problems(en, zh, todo[en]["placeholders"]):
                continue
            for r in by_en[en]:
                r["zh"], r["status"] = zh, "done"
            with self.lock:
                self.stats["cached"] += 1
            del todo[en]

        items = list(todo.values())
        if self.limit:
            remain = self.limit - self.seen
            if remain <= 0:
                return
            items = items[:remain]
        self.seen += len(items)

        batches = chunk(items)
        print("[%s] 待译 %d 行 / %d 条唯一英文，分 %d 批，已跳过 %d"
              % (group, sum(len(v) for v in by_en.values()), len(items),
                 len(batches), sum(1 for r in rows if r.get("status") == "skip")),
              flush=True)
        if not items:
            return

        with ThreadPoolExecutor(max_workers=self.workers) as ex:
            futures = {ex.submit(self.work_batch, b): b for b in batches}
            done = 0
            for fut in as_completed(futures):
                batch = futures[fut]
                try:
                    results = fut.result()
                except Exception as e:
                    results = {r["en"]: (None, "batch_error:%s" % e) for r in batch}
                for rep in batch:
                    zh, err = results.get(rep["en"], (None, "missing"))
                    self.apply(by_en[rep["en"]], zh, err)
                done += len(batch)
                print("  [%s] %d/%d 条，累计 done=%d skip=%d review=%d"
                      % (group, done, len(items), self.stats["done"],
                         self.stats["skip"], self.stats["review"]), flush=True)

    def work_batch(self, batch):
        # 键直接用英文原文（批内已去重），模型少返回一条也不会整体错位
        items = [{"en": r["en"], "ctx": make_ctx(r)} for r in batch]
        out = self.client.translate_batch(items)
        # 输出被截断/解析失败时先二分重试，比逐条重试便宜得多
        if (not out or len(out) < len(batch) * 0.6) and len(batch) > 6:
            mid = len(batch) // 2
            results = {}
            results.update(self.work_batch(batch[:mid]))
            results.update(self.work_batch(batch[mid:]))
            return results
        results, retry = {}, []
        if out:
            for r in batch:
                raw = lookup(out, r["en"])
                if raw is None:
                    retry.append(r)
                    continue
                zh = normalize(raw, r["en"])
                probs = problems(r["en"], zh, r["placeholders"])
                if probs:
                    retry.append(r)
                else:
                    results[r["en"]] = (zh, None)
        else:
            retry = list(batch)

        # 批里漏掉/不合格的，单条重试一次，再走兜底模型
        for r in retry:
            zh, err = self.work_single(r)
            results[r["en"]] = (zh, err)
        return results

    def work_single(self, row):
        try:
            phs = row["placeholders"]
            if phs:
                masked, mapping = mask_placeholders(row["en"], phs)
                out = self.client.translate_batch(
                    [{"en": masked, "ctx": make_ctx(row) + "；@@0@@ 这类标记必须原样保留"}])
                raw = lookup(out or {}, masked)
                if raw is None and out and len(out) == 1:
                    raw = list(out.values())[0]
                zh = unmask_placeholders(normalize(raw, masked), mapping)
            else:
                out = self.client.translate_batch(
                    [{"en": row["en"], "ctx": make_ctx(row)}])
                raw = lookup(out or {}, row["en"])
                # 单条请求只剩一个键时，键名被改动（如半角冒号变全角）也认
                if raw is None and out and len(out) == 1:
                    raw = list(out.values())[0]
                zh = normalize(raw, row["en"])
            probs = problems(row["en"], zh, row["placeholders"])
            if not probs:
                return zh, None
            first = ",".join(probs)
        except Exception as e:
            first = str(e)[:120]
        try:
            zh = normalize(self.client.translate_single_fallback(row["en"]), row["en"])
            probs = problems(row["en"], zh, row["placeholders"])
            if not probs:
                return zh, None
            return None, "fallback_bad:%s" % ",".join(probs)
        except Exception as e:
            return None, "%s | %s" % (first, str(e)[:80])

    def apply(self, rows, zh, err):
        if zh is not None:
            for row in rows:
                row["zh"] = zh
                row["status"] = "done"
            self.put(rows[0]["en"], zh, "done")
        else:
            for row in rows:
                row["status"] = "review"
            with self.lock:
                self.stats["review"] += 1
                if len(self.failures) < 300:
                    self.failures.append({"id": rows[0]["id"], "en": rows[0]["en"][:160],
                                          "reason": err})


# ---------------------------------------------------------------- 文件读写

def load_rows(path):
    rows = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def write_rows(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            # 与 extract_strings.py 的紧凑格式保持一致，否则整文件都会进 diff
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser(description="用 LLM API 回填待译表的 zh/status")
    ap.add_argument("--text", default=os.path.join(ROOT, "text"), help="待译表目录")
    ap.add_argument("--groups", default="01,02,03,04,05,06",
                    help="要处理的组号前缀，逗号分隔")
    ap.add_argument("--api-file", default=os.environ.get("TAK_API_FILE"),
                    help="本地 API 凭据文件（地址：/口令：）")
    ap.add_argument("--model", default="Qwen/Qwen3-8B")
    ap.add_argument("--fallback-model", default="tencent/Hunyuan-MT-7B",
                    help="批量失败后的单条兜底模型，留空禁用")
    ap.add_argument("--terms", default=DEFAULT_TERMS, help="专名表 terms.json")
    ap.add_argument("--workers", type=int, default=6)
    ap.add_argument("--limit", type=int, default=0, help="本次最多处理多少条唯一英文")
    ap.add_argument("--retry-review", action="store_true", help="重试 status=review 的条目")
    ap.add_argument("--no-cache", action="store_true", help="不使用磁盘译文缓存")
    ap.add_argument("--dry-run", action="store_true", help="只统计与分批，不调用 API")
    ap.add_argument("--report", default=DEFAULT_REPORT)
    a = ap.parse_args()

    terms = load_terms(a.terms)
    terms_hash = hashlib.sha1(
        json.dumps(terms, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]

    cache = {}
    cache_path = None if a.no_cache else DEFAULT_CACHE
    if cache_path and os.path.exists(cache_path):
        try:
            data = json.load(open(cache_path, encoding="utf-8"))
            if data.get("terms_hash") == terms_hash:
                cache = data.get("map", {})
            else:
                print("专名表已变更，忽略旧缓存", flush=True)
        except Exception as e:
            print("缓存读取失败，忽略：%s" % e, flush=True)

    groups = [g.strip() for g in a.groups.split(",") if g.strip()]
    files = []
    for code in GROUP_ORDER:
        if code.split("_")[0] in groups:
            p = os.path.join(a.text, code + ".jsonl")
            if os.path.exists(p):
                files.append((code, p))

    if a.dry_run:
        total = 0
        for code, p in files:
            rows = load_rows(p)
            todo = [r for r in rows if r.get("status") in ("pending", "review")]
            sk = [r for r in todo if skip_reason(r["en"])]
            print("%-12s 总 %4d / 待处理 %4d / 可跳过 %3d / 预计批次 %3d"
                  % (code, len(rows), len(todo), len(sk),
                     len(chunk([r for r in todo if not skip_reason(r["en"])]))))
            total += len(todo) - len(sk)
        print("合计需翻译 %d 条唯一英文" % total)
        return 0

    base, key = load_api(a.api_file)
    client = Client(base, key, a.model, a.fallback_model)
    client.system = SYSTEM.format(terms=terms_prompt(terms))

    runner = Runner(client, cache, cache_path, terms_hash, a.workers)
    runner.retry_review = a.retry_review
    runner.limit = a.limit
    runner.seen = 0

    t0 = time.time()
    for code, path in files:
        rows = load_rows(path)
        runner.run_group(rows, code)
        write_rows(path, rows)
        runner.save_cache()
        print("[%s] 已写回 %s" % (code, path), flush=True)

    # 最后一次全局校验由 verify_strings.py 负责，这里只落报告
    report = {
        "model": a.model,
        "fallback_model": a.fallback_model,
        "seconds": round(time.time() - t0, 1),
        "stats": runner.stats,
        "failures": runner.failures,
    }
    os.makedirs(os.path.dirname(a.report), exist_ok=True)
    with open(a.report, "w", encoding="utf-8", newline="\n") as f:
        json.dump(report, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("完成：%s，耗时 %.1f 分钟" % (runner.stats, (time.time() - t0) / 60), flush=True)
    if runner.failures:
        print("失败 %d 条，详见 %s" % (len(runner.failures), a.report), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
