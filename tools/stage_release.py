#!/usr/bin/env python3
"""把 DLL、字典、资源层、说明文件组装成发布目录 stage/。

为什么单独一个脚本
    CI 与本地必须产出同一份东西。把组装逻辑写进 workflow 的 heredoc，
    缩进一层不对就报 SyntaxError，而且本地没法复跑 —— 只能推上去等 CI 失败。
    这里本地跑一遍，dist/ 里的 zip 和 release 上的就是同一份字节。

组成（全部放在 zip 根，解压到游戏目录即用）
    version.dll    编译产物
    chs.dict       运行时词典
    chs_res/       资源层（来自 payload/chs_res.zip）
    INSTALL.txt    安装说明（@VERSION@ 已替换，UTF-8 BOM + CRLF）
    LICENSE        HJNCSA-1.0（本项目的许可全文）
    LICENSES/      基础许可 Apache-2.0 与第三方许可全文
    NOTICE         版权、来源与改动说明（源文件 packaging/NOTICE.txt）

用法
    python tools/stage_release.py --version v0.1.0 --dll build_dll/version.dll
"""

import argparse
import json
import os
import shutil
import sys
import zipfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def guard(path):
    """拒绝把仓库根或盘符根当成临时目录删掉。"""
    p = os.path.abspath(path)
    if p in (os.path.abspath(ROOT), os.path.dirname(os.path.abspath(ROOT)),
             os.path.splitdrive(p)[0] + os.sep):
        sys.exit("拒绝清空 %s —— 这不是构建目录" % p)
    return p


def render_install(src, dst, version):
    with open(src, encoding="utf-8") as f:
        text = f.read()
    if "@VERSION@" not in text:
        sys.exit("%s 里没有 @VERSION@ 占位" % src)
    text = text.replace("@VERSION@", version)
    # 记事本在旧系统上会把无 BOM 的 UTF-8 当 GBK 解，中文全乱；
    # 行尾统一 CRLF，否则整篇会挤成一行。
    text = text.replace("\r\n", "\n").replace("\n", "\r\n")
    with open(dst, "w", encoding="utf-8-sig", newline="") as f:
        f.write(text)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--version", required=True)
    ap.add_argument("--build-out", default=os.path.join(ROOT, "build_out"))
    ap.add_argument("--payload", default=os.path.join(ROOT, "payload"))
    ap.add_argument("--packaging", default=os.path.join(ROOT, "packaging"))
    ap.add_argument("--dll", default=None,
                    help="不传则从 build_dll/version.dll 取（本地构建）")
    ap.add_argument("--out", default=os.path.join(ROOT, "stage"))
    a = ap.parse_args()

    out = guard(a.out)

    dll = a.dll or os.path.join(ROOT, "build_dll", "version.dll")
    if not os.path.isfile(dll):
        sys.exit("找不到 %s —— 先编译，或用 --dll 指定" % dll)

    dic = os.path.join(a.build_out, "chs.dict")
    if not os.path.isfile(dic):
        sys.exit("找不到 %s —— 先跑 build_assets.py" % dic)

    pzip = os.path.join(a.payload, "chs_res.zip")
    pmeta = os.path.join(a.payload, "chs_res.json")
    for p in (pzip, pmeta):
        if not os.path.isfile(p):
            sys.exit("找不到 %s —— 先跑 pack_payload.py" % p)

    if os.path.isdir(out):
        shutil.rmtree(out)
    os.makedirs(out)

    shutil.copy2(dll, os.path.join(out, "version.dll"))
    shutil.copy2(dic, os.path.join(out, "chs.dict"))

    with open(pmeta, encoding="utf-8") as f:
        meta = json.load(f)
    with zipfile.ZipFile(pzip) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]
        if len(names) != meta["files"]:
            sys.exit("payload 文件数不符：%d vs %d" % (len(names), meta["files"]))
        z.extractall(out)

    render_install(os.path.join(a.packaging, "INSTALL.txt"),
                   os.path.join(out, "INSTALL.txt"), a.version)

    # 包内文件名用不带扩展名的 NOTICE：Apache 2.0 第 4(d) 节与本许可第
    # 3.7(a) 节都按「名为 NOTICE 的文件」表述；仓库里的编辑源仍是
    # packaging/NOTICE.txt（带扩展名方便编辑器识别）。
    shutil.copy2(os.path.join(a.packaging, "NOTICE.txt"),
                 os.path.join(out, "NOTICE"))

    # HJNCSA-1.0 以 Apache 2.0 为基础，要求随分发物提供本文件与基础许可
    # 全文（第 0 节、第 3.8 节）。LICENSES/ 整目录带上，NOTICE 里引用的
    # TAUtil-MIT.txt 才会在包内真实存在。
    lic = os.path.join(ROOT, "LICENSE")
    if not os.path.isfile(lic):
        sys.exit("找不到仓库根 LICENSE")
    shutil.copy2(lic, os.path.join(out, "LICENSE"))

    licdir = os.path.join(ROOT, "LICENSES")
    if not os.path.isdir(licdir):
        sys.exit("找不到 LICENSES/ —— 许可要求随包提供基础许可全文")
    os.makedirs(os.path.join(out, "LICENSES"), exist_ok=True)
    nlic = 0
    for name in sorted(os.listdir(licdir)):
        if name.lower().endswith(".txt"):
            shutil.copy2(os.path.join(licdir, name),
                         os.path.join(out, "LICENSES", name))
            nlic += 1
    if not os.path.isfile(os.path.join(out, "LICENSES", "Apache-2.0.txt")):
        sys.exit("LICENSES/ 里缺少 Apache-2.0.txt")

    total = 0
    nfile = 0
    for dirpath, _, files in os.walk(out):
        for f in files:
            nfile += 1
            total += os.path.getsize(os.path.join(dirpath, f))
    print("stage 组装完成：%d 个文件，%.1f MB → %s"
          % (nfile, total / 1048576.0, out))
    print("  version.dll %d 字节" % os.path.getsize(os.path.join(out, "version.dll")))
    print("  chs.dict    %d 字节" % os.path.getsize(os.path.join(out, "chs.dict")))
    print("  chs_res/    %d 个文件" % len(names))
    print("  LICENSES/   %d 份许可全文" % nlic)
    return 0


if __name__ == "__main__":
    sys.exit(main())
