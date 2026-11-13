#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""清洗译文里 GBK 编不了的字符。

为什么必须清
------------
游戏资源是 8 位单字节文本，中文只能靠 GBK 双字节表示。如果译文里混进
「GBK 编不了、但 cp1252 能编」的字符，打包器会给它写**单字节**，插在
GBK 双字节流里就把后续字节全部错位：

    '杜马·戈残骸' 正常应是  b6c5 c2ed a1a4 b8ea b2d0 baba   (12 字节)
    若 '·' 走 cp1252 变成  b6c5 c2ed b7 b8ea b2d0 baba      (11 字节，奇数)
                            ↑ 单字节 0xB7 吞掉了下一字节的配对

后果是整份 npcs.tdf 解不开 —— 不是这一个词条坏，是**整张表**读不到。

处理策略
--------
* • (U+2022) → · (U+00B7)：GBK A1A4，中文标准项目符号
* ™ © ® → 删除：GBK 无对应，且原意已由上下文表达
* 其他编不了的（阿拉伯文等模型幻觉）→ 去变音/丢弃

用法
----
    python tools/clean_text.py --text text            # 只报告
    python tools/clean_text.py --text text --write    # 落盘
"""

import argparse
import collections
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import translate as T          # noqa: E402


def load(path):
    with open(path, encoding="utf-8") as f:
        return [json.loads(l) for l in f if l.strip()]


def main():
    ap = argparse.ArgumentParser(description="清洗 GBK 不安全的字符")
    ap.add_argument("--text", default="text")
    ap.add_argument("--write", action="store_true", help="真正写回（默认只报告）")
    a = ap.parse_args()

    touched = collections.Counter()
    files = sorted(f for f in os.listdir(a.text) if f.endswith(".jsonl"))
    total_rows = total_fields = 0

    for fn in files:
        path = os.path.join(a.text, fn)
        rows = load(path)
        dirty = 0
        for r in rows:
            total_rows += 1
            old = r.get("zh") or ""
            new = T.gbk_safe(old)
            if new != old:
                for ch in old:
                    if ch not in new:
                        touched[ch] += 1
                r["zh"] = new
                dirty += 1
                total_fields += 1
        # 残留扫描独立于 dirty：即使本轮没改，也要报出仍然非法的字符
        still = 0
        for r in rows:
            for ch in (r.get("zh") or ""):
                try:
                    ch.encode("gbk")
                except UnicodeEncodeError:
                    still += 1
        # 只写回真正改过的文件，避免无改动文件被重排产生 diff 噪声
        if dirty and a.write:
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                for r in rows:
                    f.write(json.dumps(r, ensure_ascii=False,
                                       separators=(",", ":")) + "\n")
        if still:
            print("%-24s 仍有 %d 个 GBK 非法字符" % (fn, still))

    if touched:
        print("清洗掉的字符：")
        for ch, n in touched.most_common():
            print("  %r U+%04X  %d 次 → %r"
                  % (ch, ord(ch), n, T.GBK_FALLBACK.get(ch, "(删除)")))
    else:
        print("没有需要清洗的字符。")

    print("扫描 %d 个文件、%d 条记录，改动 %d 处"
          % (len(files), total_rows, total_fields))
    if not a.write and total_fields:
        print("（这是预演，加 --write 才会落盘）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
