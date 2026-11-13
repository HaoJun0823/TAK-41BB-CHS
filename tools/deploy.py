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

import check_tdf

MARKER = ".tak_chs_installed"    # 装过的痕迹，撤回时据此判断哪些是我们放的
MANIFEST = ".tak_chs_files.txt"  # 我们写进去的文件清单（相对路径），撤回时按单删

# 代理宿主：输出文件名 → 说明
PROXIES = {
    "version": "version.dll",
    "binkw32": "binkw32.dll",
}
DEFAULT_PROXY = "version"

# 上一版铺到游戏根目录、本轮要清理的松散文件（相对路径）
PENDING_CLEANUP = []

# 同一 VFS 路径在多个 HPI 里各有副本时，按游戏加载顺序取最后生效的那份。
# 引擎用 *.hpi 通配枚举、按文件名升序装载，后装覆盖先装；这里按同一顺序
# 给权重，数字大的优先。只用于「各版本改写后正文一致」的情况，
# 正文不同的直接跳过（见 same_bodies）。
LOOSE_PRIORITY = {
    "boneyards": 10, "boneyards2": 11,
    "data": 20, "english": 21,
    "ipdata": 30, "ipenglish": 31, "ipmissions": 32, "ipsections": 33,
    "jersey": 34, "maps": 35, "meta": 36, "missions": 37, "sections": 38,
    "terrain": 39, "v2rocket": 50, "v3rocket": 60,
}


def _body(b):
    """比较版本用：统一行尾、去掉首尾空白。"""
    return b.replace(b"\r\n", b"\n").replace(b"\r", b"\n").strip()


def same_bodies(items):
    """多版本产物的正文是否完全一致（只差行尾/大小写路径就不算差异）。"""
    blobs = set()
    for _, full, _ in items:
        with open(full, "rb") as f:
            blobs.add(_body(f.read()))
    return len(blobs) == 1



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


def iter_res_files(assets):
    """挑出该进 chs_res/ 的资源文件，返回 [(rel, full, hpdir, ambiguous)]。

    rel 是游戏 VFS 里的相对路径，也是 chs_res/ 下的落盘位置。

    版本歧义：同一 VFS 路径在 data / V2Rocket / V3Rocket / IPData 等包里各
    有一份，4.1BB 补丁改过 .fbi 的 frozen/airship/healtime 这些平衡字段。
    引擎按包加载顺序取后者（V3Rocket 最后加载），所以这里同样按
    LOOSE_PRIORITY 取最高的那一份；ambiguous 标记「多版本正文不一致、
    已按优先级择一」，供调用方提示。

    只取 update/：build_out/translate/ 是另一套「精简语言槽」产物，与
    update/ 里的改写后原文件同路径，混进来会互相覆盖。

    部署（deploy.py）与打包（pack_payload.py）都走这一个函数，
    免得两边各写一套挑选规则、最后发布的包和实测通过的包不是同一份。
    """
    base = os.path.join(assets, "update")
    if not os.path.isdir(base):
        return []

    cand = {}
    for hpdir in sorted(os.listdir(base)):
        hp = os.path.join(base, hpdir)
        if not os.path.isdir(hp) or hpdir.startswith("_"):
            continue      # _root 里的 Startup.tdf 会顶掉根目录原版文件
        for full, rel in walk_files(hp):
            key = rel.replace("\\", "/").lower()
            cand.setdefault(key, []).append((hpdir, full, rel))

    out = []
    for key, items in sorted(cand.items()):
        items.sort(key=lambda x: -LOOSE_PRIORITY.get(x[0].lower(), 0))
        hpdir, full, rel = items[0]
        amb = len(items) > 1 and not same_bodies(items)
        out.append((rel.replace("\\", "/"), full, hpdir, amb))
    return out


def plan_install(game, assets, dll, proxy, res=False):
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

    # 4) 内嵌网页译文：chs_web/<相对路径>，由 DLL 在 CreateFileA 上重定向。
    #    原有 atlas/ Boneyards/ GC/ 一个字节不动。
    web = os.path.join(assets, "web")
    if os.path.isdir(web):
        n = 0
        for full, rel in walk_files(web):
            acts.append(("网页译文 %s" % rel.replace("\\", "/"), full,
                         os.path.join(game, "chs_web", rel)))
            n += 1
        if n:
            print("   （网页译文 %d 个文件）" % n)
    else:
        warns.append("没找到 %s，跳过网页译文层" % web)

    # 5) 资源层：把改写后的 .txt/.ota/.gui/.fbi/.tdf 按 VFS 路径放进 chs_res/。
    #
    #    为什么不是铺到游戏根目录：引擎的 sub_53AFF0 只在
    #    「Override File Search Path 为空」时才尝试 fopen 松散文件；实测该
    #    路径在初始化时被设置，松散分支永远走不到（铺 843 个文件到根目录
    #    实测零效果）。DLL 现在直接接管 sub_53AFF0，命中 chs_res/ 就返回
    #    我们构造的引擎文件对象，因此译文必须放在 chs_res/<VFS 路径>。
    #
    #    版本歧义：同一 VFS 路径在 data / V2Rocket / V3Rocket / IPData 等包里
    #    各有一份，4.1BB 补丁改过 .fbi 的 frozen/airship/healtime 这些平衡字段。
    #    选错版本 = 回退数值 = 联机不同步，所以按包优先级择一并计数；
    #    挑选规则统一放在 iter_res_files，打包（pack_payload）与部署共用一份。
    if res:
        taken = multi = stale = 0
        multi_src = {}
        for rel, full, hpdir, amb in iter_res_files(assets):
            if amb:
                multi += 1
                multi_src[hpdir] = multi_src.get(hpdir, 0) + 1
            # 上一版铺在游戏根目录的松散文件：内容一致才删，避免误删原版
            old = os.path.join(game, rel)
            if os.path.isfile(old):
                try:
                    if _body(open(old, "rb").read()) == _body(open(full, "rb").read()):
                        PENDING_CLEANUP.append(rel)
                        stale += 1
                except OSError:
                    pass
            acts.append(("资源 %s ← %s" % (rel.lower(), hpdir), full,
                         os.path.join(game, "chs_res", rel)))
            taken += 1
        print("   （chs_res 资源 %d 个；多版本按优先级取 %d；根目录待清理 %d）"
              % (taken, multi, stale))
        if multi:
            detail = "、".join("%s %d" % (k, v) for k, v in
                               sorted(multi_src.items(), key=lambda x: -x[1]))
            warns.append("多版本资源按包优先级取用（%s）——与引擎自己的取用顺序一致" % detail)
    elif res is False:
        pass

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


def cleanup_stale_loose(game):
    """删掉上一版铺在根目录的松散资源（只删内容与产物一致的那些）。"""
    removed = 0
    dirs = set()
    for rel in PENDING_CLEANUP:
        p = os.path.join(game, rel)
        if not os.path.isfile(p):
            continue
        try:
            os.remove(p)
            removed += 1
            dirs.add(os.path.dirname(p))
        except OSError as e:
            print("  [警告] 删不掉 %s: %s" % (rel, e))
    for d in sorted(dirs, key=lambda x: -x.count(os.sep)):
        cur = d
        while os.path.isdir(cur) and not os.listdir(cur):
            try:
                os.rmdir(cur)
            except OSError:
                break
            cur = os.path.dirname(cur)
    return removed


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


def tdf_gate(update_dir):
    """部署前把 update/ 下所有 .tdf 过一遍语法，返回硬伤文件数。

    只拦硬伤（缺 ']' / '{' / '=' / ';'、多余 '}'）；原版自带的分号怪写法
    （法语那句 "Vous êtes éliminé; désirez-vous..."）只报警告，不拦。
    """
    if not os.path.isdir(update_dir):
        return 0
    bad = 0
    for path in check_tdf.walk(update_dir):
        fails, warns = check_tdf.check_file(path)
        for w in warns:
            print("   [TDF 警告] %s：%s" % (os.path.basename(path), w))
        if fails:
            bad += 1
            print("   [TDF 硬伤] %s" % path)
            for msg in fails:
                print("       %s" % msg)
    if not bad:
        print("   TDF 预检通过")
    return bad


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
    ap.add_argument("--res", action="store_true",
                    help="把改写后的 .txt/.ota/.gui/.fbi/.tdf 按 VFS 路径"
                         "放进 chs_res/（由 DLL 接管引擎的文件读取）")
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
        # 部署前过一遍 TDF 语法：引擎解析失败时只会弹一句
        #   "Data field - '=' not found - name = 'root'"
        # 不带行号，事后要从 500 多份文件里倒查，代价比在这里挡一下高得多。
        if a.res:
            bad = tdf_gate(os.path.join(os.path.abspath(a.assets), "update"))
            if bad:
                print("[中止] %d 份 TDF 有语法硬伤，先修资产再部署" % bad)
                return 1
        acts, warns = plan_install(game, os.path.abspath(a.assets), dll, a.proxy,
                                   res=a.res)
        if acts is None:
            for w in warns:
                print("[中止] " + w)
            return 1
        print("安装计划（%d 步）：" % len(acts))
        total = do_install(game, acts, a.apply)
        print("\n合计写入约 %s" % human(total))
        if a.apply and a.res and PENDING_CLEANUP:
            n = cleanup_stale_loose(game)
            print("已清理上一版铺在根目录的松散资源 %d 个" % n)

    for w in warns:
        print("[注意] " + w)

    if not a.apply:
        print("\n这是预演，硬盘没有改动。确认无误后加 --apply。")
    else:
        print("\n完成。启动游戏后看游戏目录的 tak_chs.log。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
