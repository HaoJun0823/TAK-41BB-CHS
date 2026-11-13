#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按引擎真实实现校验 TDF 语法，部署前拦住"游戏里弹解析错误"这类事故。

为什么不能只靠人眼
------------------
引擎的 TDF 解析器（KINGDOMS.icd sub_5429D0，41BB）不是逐行读的，而是**按
分隔符在整块缓冲区里跳**：

    跳过 空格/TAB/CR/LF
    '[' → strchr(']') 取节名 → 跳过空白 → 必须是 '{' → 递归
    '}' → 返回上一层
    其它 → strchr('=') 取键 → strchr(';') 取值

后果是：少一个 ']'、少一个 '{'、少一个 ';'，解析位置就会一路错位到文件末尾，
报出来的往往是最没信息量的那句

    Parse error in .TDF File! Data field - '=' not found - name = 'root'

（name 永远是 'root'，因为顶层调用的节名就叫 root；真正出事的位置在文件里
 几十行之外。）所以这里把同一套规则原样复刻一遍，直接指出**第一个**出错的
 字节偏移和行号。

两类结论分开报
--------------
* FAIL：会让游戏弹框或吞掉后续内容的硬伤 —— 缺 ']' / '{' / '=' / ';'、
  NUL 字节、'}' 多出来。
* WARN：引擎能吞下去、但内容一定不对的写法 —— 键含非 ASCII（键被翻译了）、
  键里带 ';'（原版法语就有一处，值里的分号把后半句切成了假键）、
  换行符不一致（原版全是 CRLF，混进裸 LF 说明生成器没跟随源文件）。

用法
----
    python tools/check_tdf.py build_out/update          # 硬伤 + 警告
    python tools/check_tdf.py --strict build_out/update # 警告也算失败
    python tools/check_tdf.py --endings build_out/update  # 只查换行符
"""

import argparse
import os
import sys

WS = b" \t\r\n"
STRUCT = (0x5B, 0x5D, 0x7B, 0x7D)  # [ ] { }


class TdfError(Exception):
    def __init__(self, msg, data, off):
        self.msg = msg
        self.off = off
        self.line = data.count(b"\n", 0, off) + 1 if off <= len(data) else 0
        super(TdfError, self).__init__(
            "%s（第 %d 行，偏移 0x%X）" % (msg, self.line, off))


def strip_comments(data):
    """把 // 与 /* */ 注释替换成空格，规则与 sub_5428D0 完全一致。

    引擎是**原地**改字节的：注释内容变空格，长度不变，所以偏移量不变，
    报错行号才对得上。这里返回同样长度的 bytes。
    """
    out = bytearray(data)
    i = 0
    n = len(out)
    while i < n:
        if out[i] != 0x2F:  # '/'
            i += 1
            continue
        if i + 1 < n and out[i + 1] == 0x2F:      # //
            while i < n and out[i] != 0x0A:
                out[i] = 0x20
                i += 1
            continue
        if i + 1 < n and out[i + 1] == 0x2A:      # /*
            out[i] = out[i + 1] = 0x20
            i += 2
            while i < n and not (out[i] == 0x2A and i + 1 < n and out[i + 1] == 0x2F):
                out[i] = 0x20
                i += 1
            if i < n:
                out[i] = 0x20
                if i + 1 < n:
                    out[i + 1] = 0x20
                i += 2
            continue
        i += 1
    return bytes(out)


def _skip_ws(data, i):
    n = len(data)
    while i < n and data[i] in WS:
        i += 1
    return i


def _trim(data, start, end):
    while start < end and data[start] in WS:
        start += 1
    while end > start and data[end - 1] in WS:
        end -= 1
    return start, end


def parse(data):
    """返回 [(节名, 键, 值, 键偏移)]；结构硬伤抛 TdfError。

    语义与 sub_5429D0 一一对应：'[' 找 ']'，之后必须是 '{'；字段找 '=' 再找 ';'。
    """
    fields = []
    data = strip_comments(data)
    n = len(data)
    i = 0
    stack = []
    depth = 0  # 已进入几层 '{'
    while True:
        i = _skip_ws(data, i)
        if i >= n:
            if depth:
                raise TdfError("文件结束时仍有 %d 层 '}' 未闭合" % depth, data, n - 1)
            return fields
        c = data[i]
        if c == 0x5B:  # '['
            end = data.find(b"]", i + 1)
            if end < 0:
                raise TdfError("节名缺少 ']'（引擎会一路吞到文件尾）", data, i)
            if b"\n" in data[i:end] or b"\r" in data[i:end]:
                raise TdfError("节名里出现换行", data, i)
            name = data[i + 1:end].decode("latin-1").strip()
            if not name:
                raise TdfError("节名为空", data, i)
            j = _skip_ws(data, end + 1)
            if j >= n:
                raise TdfError("节 [%s] 缺少 '{'" % name, data, end)
            if data[j] != 0x7B:  # '{'
                raise TdfError("节 [%s] 之后不是 '{'，而是 %r" % (name, chr(data[j])),
                               data, j)
            stack.append(name)
            depth += 1
            i = j + 1
            continue
        if c == 0x7D:  # '}'
            if not stack:
                raise TdfError("多余的 '}'（没有对应的节）", data, i)
            stack.pop()
            depth -= 1
            i += 1
            continue
        # 数据字段
        eq = data.find(b"=", i)
        if eq < 0:
            raise TdfError("数据字段缺少 '='", data, i)
        semi = data.find(b";", eq + 1)
        if semi < 0:
            raise TdfError("数据字段缺少 ';'", data, eq)
        ks, ke = _trim(data, i, eq)
        vs, ve = _trim(data, eq + 1, semi)
        key = data[ks:ke]
        val = data[vs:ve]
        if not key:
            raise TdfError("字段名为空", data, i)
        if not stack:
            raise TdfError("字段 %r 出现在任何 [节] 之外" % key.decode("latin-1"), data, i)
        fields.append((stack[-1], key.decode("latin-1"),
                       val.decode("gbk", "replace") if any(b > 0x7F for b in val)
                       else val.decode("latin-1"), ks))
        i = semi + 1


def audit(fields):
    """引擎不会因此报错、但内容一定不对的写法。"""
    warns = []
    for section, key, val, off in fields:
        if any(ord(ch) > 0x7F for ch in key):
            warns.append("键含非 ASCII：%r（节 [%s]）—— 键被翻译了？" % (key, section))
        elif ";" in key:
            warns.append("键里带 ';'：%r（节 [%s]）—— 值里的分号把后半句切成了假键"
                         % (key, section))
        if "[" in key or "]" in key or "{" in key or "}" in key:
            warns.append("键里带结构字符：%r（节 [%s]）" % (key, section))
        if "\n" in val or "\r" in val:
            warns.append("值里有换行：%r（节 [%s]）" % (key, section))
    return warns


def check_file(path):
    """返回 (fails, warns)。"""
    with open(path, "rb") as f:
        data = f.read()
    fails = []
    warns = []
    if b"\x00" in data:
        fails.append("含 NUL 字节")
    lf, crlf = data.count(b"\n"), data.count(b"\r\n")
    if lf != crlf:
        warns.append("换行符不一致（%d 个 LF / %d 个 CRLF）" % (lf, crlf))
    try:
        fields = parse(data)
    except TdfError as exc:
        fails.append(str(exc))
        fields = []
    warns.extend(audit(fields))
    try:
        data.decode("gbk")
    except UnicodeDecodeError as exc:
        fails.append("不是合法 GBK：%s" % exc)
    return fails, warns


def walk(root):
    for dirpath, _, names in os.walk(root):
        for name in sorted(names):
            if name.lower().endswith(".tdf"):
                yield os.path.join(dirpath, name)


def main():
    ap = argparse.ArgumentParser(description="按引擎实现校验 TDF 语法")
    ap.add_argument("path", help="TDF 文件或目录")
    ap.add_argument("--strict", action="store_true", help="警告也算失败")
    ap.add_argument("--endings", action="store_true", help="只检查换行符一致性")
    ap.add_argument("--quiet", action="store_true", help="只报问题，不报汇总")
    a = ap.parse_args()

    files = list(walk(a.path)) if os.path.isdir(a.path) else [a.path]
    if not files:
        print("没有找到 .tdf：%s" % a.path)
        return 1

    n_fail = n_warn = 0
    for path in files:
        if a.endings:
            with open(path, "rb") as f:
                data = f.read()
            lf, crlf = data.count(b"\n"), data.count(b"\r\n")
            warns = (["换行符不一致（%d 个 LF / %d 个 CRLF）" % (lf, crlf)]
                     if lf != crlf else [])
            fails = []
        else:
            fails, warns = check_file(path)
        if warns and not a.endings:
            for w in warns:
                n_warn += 1
                print("WARN %s\n     %s" % (path, w))
        elif warns:
            n_warn += 1
            print("WARN %s\n     %s" % (path, warns[0]))
        if fails:
            n_fail += 1
            print("FAIL %s" % path)
            for msg in fails:
                print("     %s" % msg)
    if not a.quiet:
        print("TDF 校验：%d 份，硬伤 %d 份，警告 %d 条" % (len(files), n_fail, n_warn))
    if n_fail or (a.strict and n_warn):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
