#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 DLL 与资产装进游戏目录，或原样撤回。

安全约定
--------
* **默认只打印计划，不动硬盘**；确认无误后加 --apply 才执行。
* 两种代理宿主，二选一，**绝不能同时装**（否则同一进程里会有两份我们
  的代码，hook 装两遍必炸）：
    version.dll  推荐。系统 DLL，游戏静态导入它；我们放一个同名代理在
                 游戏目录，应用目录优先，于是先加载到我们。游戏自带文件
                 **一个都不用改名**，删掉 version.dll 即还原。
    binkw32.dll  旧方案。要先把原版改名成 binkw32_orig.dll 再顶替，
                 会动游戏自带文件的名字，因此不再是默认。
* 绝不删除游戏自带文件：bink 方案里的改名是**保留**而非覆盖。
* 只写：代理 DLL、chs.dict、update/ 覆盖层、两个记账文件。

用法
----
    python tools/deploy.py --game "<41BB目录>"                     # 预演（version 代理）
    python tools/deploy.py --game "<41BB目录>" --apply             # 安装
    python tools/deploy.py --game "<41BB目录>" --apply --proxy binkw32
    python tools/deploy.py --game "<41BB目录>" --apply --uninstall # 撤回
"""

import argparse
import filecmp
import os
import shutil
import sys

MARKER = ".tak_chs_installed"    # 装过的痕迹，撤回时据此判断哪些是我们放的
MANIFEST = ".tak_chs_files.txt"  # 我们写进去的文件清单（相对路径），撤回时按单删

# 代理宿主：输出文件名 → 说明
PROXIES = {
    "version": "version.dll",
    "binkw32": "binkw32.dll",
}
DEFAULT_PROXY = "version"


def check_game(game):
    """确认这是一个 41BB 游戏目录，返回 (ok, 说明)。"""
    if not os.path.isdir(game):
        return False, "目录不存在"
    exe = os.path.join(game, "KINGDOMS.icd")
    if not os.path.isfile(exe):
        return False, "找不到 KINGDOMS.icd（不是游戏根目录？）"
    return True, exe


def human(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "%.1f %s" % (n, unit) if unit != "B" else "%d B" % n
        n /= 1024.0


def walk_files(root):
    out = []
    for dirpath, _, files in os.walk(root):
        for f in files:
            full = os.path.join(dirpath, f)
            out.append((full, os.path.relpath(full, root)))
    return out


def plan_install(game, assets, dll, proxy):
    """返回 (actions, warnings)。

    action = (说明, 源, 目标 [, 模式])
        模式 copy（默认）：复制；restore：复制并校验后删源；write：写记账文件。
    """
    acts, warns = [], []

    exe_ok, exe = check_game(game)
    if not exe_ok:
        return None, [exe]

    proxy_file = PROXIES[proxy]
    bink = os.path.join(game, "binkw32.dll")
    bink_bak = os.path.join(game, "binkw32_orig.dll")

    # 0) 上一版装的是 bink 代理的话，先把原版换回去。
    #    不做这一步就装 version 代理，游戏目录里会留着「我们的 binkw32.dll」，
    #    两个代理同时生效 —— hook 装两遍，必崩。
    if proxy != "binkw32" and os.path.isfile(bink_bak):
        acts.append(("还原原版 binkw32.dll（内容取自 binkw32_orig.dll，校验后删备份）",
                     bink_bak, bink, "restore"))

    # 1) 代理 DLL
    if not (dll and os.path.isfile(dll)):
        return None, ["没找到代理 DLL（%s）" % (dll or "未指定")]
    if proxy == "binkw32":
        if os.path.isfile(bink_bak):
            acts.append(("覆盖代理 DLL（原版已在 binkw32_orig.dll）", dll, bink))
        elif os.path.isfile(bink):
            acts.append(("原版 binkw32.dll → binkw32_orig.dll（改名，不覆盖）",
                         bink, bink_bak))
            acts.append(("放入代理 DLL", dll, bink))
        else:
            warns.append("游戏目录没有 binkw32.dll —— 该副本可能不用 Bink，"
                         "请改用 --proxy version")
    else:
        acts.append(("放入代理 %s（游戏自带文件不动）" % proxy_file, dll,
                     os.path.join(game, proxy_file)))

    # 2) 字典
    dict_src = os.path.join(assets, "chs.dict")
    if os.path.isfile(dict_src):
        acts.append(("放入运行时字典", dict_src, os.path.join(game, "chs.dict")))
    else:
        warns.append("没找到 %s，DLL 会因缺字典而放弃 hook" % dict_src)

    # 3) TDF 覆盖层
    # build_out/translate/<HPI>/translate/x.tdf 里的 <HPI> 只是产物内部的区分目录，
    # 不是游戏里的资源路径 —— 游戏 VFS 里它们统一是 translate/x.tdf。
    # 实测 english.hpi 与 IPEnglish.hpi 的同名 TDF 内容逐字节相同（都是同一份
    # 副本），所以合并到 update/ 下不会丢信息，重复的只写一次。
    tr = os.path.join(assets, "translate")
    if os.path.isdir(tr):
        seen = {}
        for hpdir in sorted(os.listdir(tr)):
            hp = os.path.join(tr, hpdir)
            if not os.path.isdir(hp):
                continue
            for full, rel in walk_files(hp):
                # 键必须大小写不敏感：V3Rocket 包里路径是 Translate/，其余包是
                # translate/，Windows 下落盘是同一个目录。用原样大小写当键会让
                # 同一个文件被记两遍（清单 56 行 vs 实际 37 个文件）。
                key = rel.replace("\\", "/").lower()
                if key in seen:
                    continue          # 同内容副本，跳过
                seen[key] = full
                acts.append(("覆盖层 %s" % key, full,
                             os.path.join(game, "update", rel)))
    else:
        warns.append("没找到 %s，跳过 TDF 覆盖层" % tr)

    acts.append(("写入安装标记（供撤回识别）", None,
                 os.path.join(game, MARKER), "write"))
    acts.append(("写入文件清单（撤回据此精确删除）", None,
                 os.path.join(game, MANIFEST), "write"))
    return acts, warns


def do_install(game, acts, apply_):
    total = 0
    written = []
    for act in acts:
        desc, src, dst = act[0], act[1], act[2]
        mode = act[3] if len(act) > 3 else "copy"
        size = os.path.getsize(src) if src else 0
        total += size
        print("  %-56s %s" % (desc[:56], human(size) if size else "-"))
        if not apply_:
            continue
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if mode == "write":
            with open(dst, "w", encoding="utf-8", newline="\n") as f:
                f.write("installed by TAK_41BB_CHS\n")
        elif os.path.abspath(src) != os.path.abspath(dst):
            shutil.copy2(src, dst)
        if mode == "restore":
            # 校验一致才删备份：宁可留个多余文件，也不能把原版弄丢
            if filecmp.cmp(src, dst, shallow=False):
                os.remove(src)
            else:
                raise RuntimeError("还原 binkw32.dll 后校验不一致，已保留备份 %s" % src)
        rel = os.path.relpath(dst, game)
        if rel not in (MARKER, MANIFEST):
            written.append(rel.replace("\\", "/"))

    # 清单最后写：中途失败时不会有半份清单误导撤回
    if apply_ and written:
        with open(os.path.join(game, MANIFEST), "w",
                  encoding="utf-8", newline="\n") as f:
            f.write("# 本补丁写入的文件，撤回时按此删除\n")
            for r in written:
                f.write(r + "\n")
    return total


def plan_uninstall(game):
    acts, warns = [], []
    marker = os.path.join(game, MARKER)
    manifest = os.path.join(game, MANIFEST)
    if not os.path.isfile(marker):
        warns.append("没找到安装标记 %s —— 可能不是本脚本装的，"
                     "强行撤回有风险，已中止" % MARKER)
        return None, warns

    # 按清单精确删除：update/ 里可能还混着别的补丁，不能整个目录端掉。
    # 代理 DLL 也在清单里，所以会一起被删掉。
    if os.path.isfile(manifest):
        with open(manifest, encoding="utf-8") as f:
            for line in f:
                rel = line.strip()
                if not rel or rel.startswith("#"):
                    continue
                p = os.path.join(game, rel)
                if os.path.isfile(p):
                    acts.append(("删 %s" % rel.replace("\\", "/"), p, None))
    else:
        warns.append("没有文件清单，只撤回代理 DLL 与 chs.dict，update/ 保持原样")
        for name in (PROXIES[DEFAULT_PROXY], "chs.dict"):
            p = os.path.join(game, name)
            if os.path.isfile(p):
                acts.append(("删 %s" % name, p, None))

    # bink 方案留下的原版备份：改回原名。必须在删代理之后执行。
    bink = os.path.join(game, "binkw32.dll")
    bink_bak = os.path.join(game, "binkw32_orig.dll")
    if os.path.isfile(bink_bak):
        if os.path.isfile(bink):
            acts.append(("删掉代理 DLL（binkw32.dll）", bink, None))
        acts.append(("binkw32_orig.dll → binkw32.dll（改回原名）", bink_bak, bink))

    acts.append(("删掉安装标记", marker, None))
    acts.append(("删掉文件清单", manifest, None))
    return acts, warns


def do_uninstall(acts, apply_):
    for desc, src, dst in acts:
        print("  %s" % desc)
        if not apply_:
            continue
        if dst:                       # 改名/还原
            shutil.move(src, dst)
        elif os.path.isdir(src):
            shutil.rmtree(src)
        elif os.path.isfile(src):
            os.remove(src)


def main():
    ap = argparse.ArgumentParser(description="部署/撤回 TAK 汉化")
    ap.add_argument("--game", required=True, help="游戏根目录（含 KINGDOMS.icd）")
    ap.add_argument("--assets", default="build_out", help="资产目录（默认 build_out）")
    ap.add_argument("--proxy", default=DEFAULT_PROXY, choices=sorted(PROXIES),
                    help="代理宿主 DLL（默认 %s）" % DEFAULT_PROXY)
    ap.add_argument("--dll", default=None,
                    help="代理 DLL 路径（默认 build_dll/<proxy>）")
    ap.add_argument("--apply", action="store_true", help="真正执行（默认只预演）")
    ap.add_argument("--uninstall", action="store_true", help="撤回")
    a = ap.parse_args()

    dll = a.dll or os.path.join("build_dll", PROXIES[a.proxy])
    game = os.path.abspath(a.game)
    print("游戏目录: %s" % game)
    print("资产目录: %s" % os.path.abspath(a.assets))
    print("代理宿主: %s" % PROXIES[a.proxy])
    print("DLL     : %s" % os.path.abspath(dll))
    print()

    if a.uninstall:
        acts, warns = plan_uninstall(game)
        if acts is None:
            for w in warns:
                print("[中止] " + w)
            return 1
        print("撤回计划：")
        do_uninstall(acts, a.apply)
    else:
        acts, warns = plan_install(game, os.path.abspath(a.assets), dll, a.proxy)
        if acts is None:
            for w in warns:
                print("[中止] " + w)
            return 1
        print("安装计划（%d 步）：" % len(acts))
        total = do_install(game, acts, a.apply)
        print("\n合计写入约 %s" % human(total))

    for w in warns:
        print("[注意] " + w)

    if not a.apply:
        print("\n这是预演，硬盘没有改动。确认无误后加 --apply。")
    else:
        print("\n完成。启动游戏后看游戏目录的 tak_chs.log。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
