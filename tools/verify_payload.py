#!/usr/bin/env python3
"""独立校验 payload/chs_res.zip 与记录、与当前译文是否一致。

为什么单独一个脚本
    pack_payload 自己校验自己等于没校验 —— 同一个 bug 会同时写进包和判断
    逻辑。这里从头读 zip、逐条重算摘要，不复用打包时的任何中间状态。

CI 里的位置
    build-release.yml 的构建阶段，解压 payload 之前先跑。失败只有两种可能：
      * 改了 text/ 却没重跑 pack_payload → 发出去的包「资源层旧、字典新」；
      * zip 被截断或篡改 → 玩家装上去缺文件。
    两种都不该发出去。

用法
    python tools/verify_payload.py
    python tools/verify_payload.py --text text --strict
"""

import argparse
import hashlib
import json
import os
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from pack_payload import text_digest  # noqa: E402  摘要算法必须与打包器一致

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def fail(msg):
    print("  [错误] " + msg)
    return 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--payload", default=os.path.join(ROOT, "payload", "chs_res.zip"))
    ap.add_argument("--meta", default=os.path.join(ROOT, "payload", "chs_res.json"))
    ap.add_argument("--text", default=os.path.join(ROOT, "text"))
    ap.add_argument("--strict", action="store_true",
                    help="译文摘要不符也判失败（发布时必须加）")
    a = ap.parse_args()

    errs = 0
    for p in (a.payload, a.meta):
        if not os.path.isfile(p):
            return fail("缺少 %s" % p)

    with open(a.meta, encoding="utf-8") as f:
        meta = json.load(f)
    prefix = meta.get("prefix", "chs_res") + "/"
    print("payload %s（%d 个文件，%.1f MB）"
          % (os.path.basename(a.payload), meta["files"],
             meta["zip_bytes"] / 1048576.0))

    with zipfile.ZipFile(a.payload) as z:
        names = [i.filename for i in z.infolist() if not i.is_dir()]

        if len(names) != meta["files"]:
            errs += fail("文件数不符：包内 %d，记录 %d" % (len(names), meta["files"]))

        if len(set(names)) != len(names):
            errs += fail("zip 里有重复条目")

        bad = [n for n in names
               if n.replace("\\", "/").startswith("/")
               or ".." in n.replace("\\", "/").split("/")]
        if bad:
            errs += fail("条目路径越界：%s" % bad[:3])

        outside = [n for n in names if not n.startswith(prefix)]
        if outside:
            errs += fail("%d 条不在 %s 下，例如 %s"
                         % (len(outside), prefix, outside[:3]))

        raw = sum(i.file_size for i in z.infolist() if not i.is_dir())
        if raw != meta["raw_bytes"]:
            errs += fail("解压后总字节不符：%d，记录 %d" % (raw, meta["raw_bytes"]))

        h = hashlib.sha256()
        for n in sorted(names, key=lambda x: x[len(prefix):].lower()):
            h.update(n[len(prefix):].lower().encode("utf-8"))
            h.update(b"\0")
            h.update(hashlib.sha256(z.read(n)).digest())
        if h.hexdigest() != meta["entries_sha256"]:
            errs += fail("entries_sha256 不符：包是 %s…，记录是 %s…"
                         % (h.hexdigest()[:16], meta["entries_sha256"][:16]))
        else:
            print("  内容摘要 %s… 一致" % h.hexdigest()[:16])

    cur = text_digest(a.text)
    if cur != meta.get("text_sha256"):
        msg = ("payload 落后于 text/（记录 %s…，当前 %s…）："
               "先跑 tools/pack_payload.py 重打资源层"
               % (str(meta.get("text_sha256"))[:16], cur[:16]))
        if a.strict:
            errs += fail(msg)
        else:
            print("  [警告] " + msg)
    else:
        print("  译文摘要 %s… 一致" % cur[:16])

    if errs:
        print("payload 校验失败（%d 项）" % errs)
        return 1
    print("payload 校验通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
