#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""补抽「裸字节」存储的文本条目。

背景：极少数文件在 HPI 里不走 SQSH 压缩块，而是把明文直接铺在数据区，
索引记录里的 block_size 记为 0。旧版 hpi_v2 只认 SQSH 头，这类条目会被
整条跳过 —— missions.hpi 的 8 份任务简报、IPMissions.hpi 的 18 份，以及
maps.hpi / V2Rocket.hpi 的地图信息文件，因此从未进入待译表。

本脚本只做「补」，不重建整张表：
  * 找出所有尚未入库的裸存 .txt（限定 missions/ 与 maps/）；
  * 按 extract_strings 同一套 id 规则（sha1(kind|en)[:12]）追加行；
  * 若 EN->ZH 表里已有译文，直接置 done；
  * 同时把译文并入 polish.json，保证重新抽取后可重放。
"""

import argparse
import glob
import hashlib
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hpi_v2 import HpiV2  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 英文 -> 中文定稿。键为去掉行首列表符号后的整行原文。
ZH = {
    # ---- missions.hpi（8 份） ----
    "• Find and enter the garrison to the North.": "· 找到北面的守备营，进到里面去。",
    "• Keep Emen safe.": "· 保埃门周全。",
    "• To the west is a Verunan port.": "· 西面是维鲁纳的港口。",
    "• Destroy the ships, town and soldiers.": "· 摧毁舰船、城镇与守军。",
    "• With the Dragon of Anu, destroy all Taros invaders.": "· 带上安努之龙，消灭所有塔罗斯入侵者。",
    "• The ground troops are added protection for the Dragons safety.": "· 地面部队是额外护卫，确保巨龙无虞。",
    "• If used correctly the waterways may prove to be useful.": "· 水道用对了，能派上大用场。",
    "• Kill all Tarosians.": "· 杀光所有塔罗斯人。",
    "• Keep Elsin and Kirenna alive.": "· 保住艾尔辛和基蕾娜。",
    "• Enter the city of Shekelesh.": "· 进入舍克勒什城。",
    "• Maneuver Lord Dernhest and his party to the center of town.": "· 把德恩赫斯特领主一行带到城中心。",
    "• Dernhest must be kept alive.": "· 德恩赫斯特必须活着。",
    "• Descend upon the city and wipe out the forces of Aramon.": "· 扑向该城，全歼阿拉蒙的军队。",
    "• Reinforcements will arrive to help you periodically.": "· 援军会陆续赶来支援。",
    "• Have your Beast Handler build more troops.": "· 让你的驭兽人多造些兵。",
    "• March south into the Verunan camp.": "· 向南推进，杀进维鲁纳营地。",
    "• Destroy all of their guard towers.": "· 拆掉他们所有的守卫塔。",
    "• Take the Hunter and find the canyon where the Stone Giants live.": "· 带上猎人，找到石巨人居住的峡谷。",
    "• Use them to wipe out the Verunan base to the Northwest.": "· 用它们荡平西北的维鲁纳基地。",
    # ---- IPMissions.hpi（18 份） ----
    "• Unload your troops and Priestesses on the nearby landmass.": "· 把部队和女祭司送上附近的陆地。",
    "• To achieve victory destroy all of Creon's Navy Yards.": "· 摧毁克里昂所有船坞即可取胜。",
    "• Escape into the desert and establish a perimeter.": "· 撤进沙漠，建立防线。",
    "• Defend your position for 45 minutes.": "· 守住阵地 45 分钟。",
    "• Amazon Knights will arrive before the end.": "· 最后关头会有亚马逊骑士赶到。",
    "• Find and secure the wreck of the Sea Shadow.": "· 找到「海影」残骸并控制住。",
    "• Eliminate any enemy presence around the wreckage.": "· 清除残骸周边的所有敌人。",
    "• Keep Heligrin alive.": "· 保住赫利格林。",
    "• Veruna is showing increasing resistance.": "· 维鲁纳的抵抗越来越强。",
    "• Demonstrate Creon's true power.": "· 让克里昂见识真正的力量。",
    "• Wipe them out.": "· 把他们彻底消灭。",
    "• Storm the Aramon city and destroy it.": "· 强攻阿拉蒙的城池，将其摧毁。",
    "• Dig in and survive the Creon attack waves.": "· 固守阵地，顶住克里昂一波波进攻。",
    "• There are only four sacred sites.": "· 圣地只有四处。",
    "• Investigate the ruined Tarosian outpost.": "· 探查塔罗斯废弃的前哨。",
    "• Eliminate any and all aggressors.": "· 消灭所有来犯之敌。",
    "• Find passage through the cavern's maze.": "· 在洞穴迷宫里找出一条通路。",
    "• Monsters are said to inhabit these caverns.": "· 据说这些洞穴里有怪物出没。",
    "• Protect your Mage Builder.": "· 保护好你的法师建造者。",
    "• Seek out and destroy all of the enemy Keeps.": "· 找到并摧毁敌方所有主堡。",
    "• The Acolytes remain loyal to Elsin and refuse to assist.": "· 信徒仍忠于艾尔辛，拒绝相助。",
    "• Seek out Thirsha in the Zhonian jungle.": "· 到佐恩丛林里寻找瑟莎。",
    "• Cross the underground river.": "· 渡过地下河。",
    "• Find the cavern's exit.": "· 找到洞穴出口。",
    "• Unload the troops and establish a landing.": "· 卸下部队，建立登陆场。",
    "• Destroy all Verunan forces.": "· 消灭维鲁纳所有部队。",
    "• Escort Heligrin through Ullin's Fingers to the lowlands.": "· 护送赫利格林穿过乌林之指，抵达低地。",
    "• Protect Heligrin and Thirsha.": "· 保护赫利格林和瑟莎。",
    "• Escort Heligrin to the center of Creon's research facility.": "· 护送赫利格林到克里昂研究所的中心。",
    "• Make your way back through the facility.": "· 沿研究所原路杀回去。",
    "• Creon is now closing in.": "· 克里昂的部队正在合围。",
    "• Thirsha, Lokken and Heligrin must all be kept alive.": "· 瑟莎、洛肯和赫利格林都必须活着。",
    "• Build defenses for the city.": "· 为城市修筑防御。",
    "• Hold off the Aramon invaders for 45 minutes.": "· 顶住阿拉蒙入侵者 45 分钟。",
    "• Protect the Sage.": "· 保护贤者。",
    "• The Sage of Creon is surrounded and must be destroyed.": "· 克里昂的贤者已被包围，必须将其消灭。",
    "• Keep the monarchs of Darien alive.": "· 保住达里恩的几位君主。",
    # ---- 地图说明（maps.hpi / data.hpi） ----
    "Two players start as Aramon in well-defended hilltop fortresses.  It's close quarters combat, so no Trebuchets.":
        "两名玩家都以阿拉蒙开局，据守防御坚固的山顶要塞。战场逼仄，打的是近身仗，所以没有投石机。",
    "Fight with established bases and build restrictions.":
        "双方开局即有基地，并受建造限制。",
}

# 地图信息行：只换 Player/Players，尺寸与内存原样保留
MAP_LINE = re.compile(r"(?i)\bplayers?\b")


def map_line_zh(line):
    if MAP_LINE.search(line):
        return MAP_LINE.sub("玩家", line)
    return None


def rid_of(en, kind="txt_line"):
    return "03-" + hashlib.sha1((kind + "|" + en).encode("utf-8")).hexdigest()[:12]


def load_rows(path):
    rows = []
    if not os.path.exists(path):
        return rows
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--game", default="I:/LocalGames/Total Annihilation Kingdoms")
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--write", action="store_true")
    a = ap.parse_args()

    path = os.path.join(a.text, "03_missions.jsonl")
    rows = load_rows(path)
    by_id = {r["id"]: r for r in rows}
    have_src = {t["src"] for r in rows for t in r["targets"]}

    added, filled, files = [], [], 0
    for hpi_path in sorted(glob.glob(os.path.join(a.game, "*.hpi"))):
        hpi = HpiV2(hpi_path)
        hname = os.path.basename(hpi_path)
        for rec in hpi.records:
            if rec.block_size != 0 or not rec.name.lower().endswith(".txt"):
                continue
            low = rec.path.lower().replace("\\", "/")
            if not (low.startswith("missions/") or low.startswith("maps/")
                    or "/maps/" in low):
                continue
            src = "%s::%s" % (hname, rec.path)
            if src in have_src:
                continue
            raw = hpi.read_file(rec)
            text = raw.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
            lines = text.decode("cp1252", errors="replace").split("\n")
            files += 1
            for i, line in enumerate(lines, 1):
                en = line.strip()
                if not en or not re.search(r"[A-Za-z]", en):
                    continue
                zh = ZH.get(en) or map_line_zh(en)
                rid = rid_of(en)
                row = by_id.get(rid)
                if row is None:
                    row = {
                        "id": rid,
                        "en": en,
                        "zh": "",
                        "status": "pending",
                        "ctx": "任务目标 / 简报正文（逐行）",
                        "kind": "txt_line",
                        "placeholders": [],
                        "flags": ["plain_line"],
                        "targets": [],
                    }
                    rows.append(row)
                    by_id[rid] = row
                    added.append(row)
                t = {"src": src, "key": "L%d" % i}
                if t not in row["targets"]:
                    row["targets"].append(t)
                if zh and row["status"] != "done":
                    row["zh"] = zh
                    row["status"] = "done"
                    filled.append(row)
                have_src.add(src)

    print("补抽文件 %d 个，新增行 %d 条，填译 %d 条" % (files, len(added), len(filled)))
    miss = [r["en"] for r in rows if r["id"] in {x["id"] for x in added + filled}
            and r["status"] != "done"]
    if miss:
        print("仍缺译文 %d 条：" % len(miss))
        for m in miss:
            print("   ", m)

    if not a.write:
        for r in added:
            print("  %s  %s" % (r["id"], r["en"]))
        return 0

    rows.sort(key=lambda r: r["id"])
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n")

    # 并入 C 档定稿，保证重新抽取后可重放
    pp = os.path.join(a.text, "polish.json")
    pd = json.load(open(pp, encoding="utf-8"))
    n0 = len(pd["entries"])
    for r in rows:
        if r["status"] == "done" and r["zh"]:
            pd["entries"][r["id"]] = r["zh"]
    with open(pp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(pd, f, ensure_ascii=False, indent=2)
        f.write("\n")
    print("已写 %s（%d 条），polish %d -> %d" % (path, len(rows), n0, len(pd["entries"])))
    return 0


if __name__ == "__main__":
    sys.exit(main())
