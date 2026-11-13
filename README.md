# TAK 41BB 全量汉化（CHS）

《Total Annihilation: Kingdoms》**4.1BB** 的全量简体中文化工程。

**硬约束**：汉化游戏内全部可见文本；游戏外文档不译；**保留联机**；
磁盘上的游戏文件保持原版哈希（改动只在运行时内存 / `chs_res/` 资源副本）。

> 本仓库不包含、也不分发任何游戏原始素材。使用者需自行持有合法副本。

## 当前状态

| 阶段 | 状态 |
|---|---|
| 版本裁决与基线 | 完成，见 `docs/00-调研结论.md` |
| HPI v2 容器解析、全量文本盘点 | 完成，9037 个文件可导出、逐条校验 0 失败 |
| 全量待译表（明文、可 diff、LLM 可回填） | **完成**，8409 条 / 15355 个写回位置 |
| 翻译 | **完成**，4297 译 / 346 跳过 / 0 待确认（07 组 3766 条候选池待 IDA 确认） |
| DLL 方案（IDA 定位语言槽 / 字库 / hook 点） | **完成**，见 `docs/02-IDA与DLL方案.md` |
| 资产打包（明文 → 补丁，门禁与构建同一工作流） | **完成** |
| DLL 实现（文本 hook + 代理注入） | **完成**，见 `docs/03-DLL构建与验证.md` |
| 中文字库层（运行时纹理帧） | **完成并实机验证**，见 [`docs/04-中文字库层.md`](docs/04-中文字库层.md) |
| 4.1BB 光盘校验（内存短路，磁盘不动） | **完成并实机验证**，见 [`docs/05-版本差异与光盘校验.md`](docs/05-版本差异与光盘校验.md) |
| 代理宿主改为 `version.dll`（不再改 `binkw32.dll`） | **完成并实机验证**，见 [`docs/06-代理DLL注入.md`](docs/06-代理DLL注入.md) |
| 覆盖清单：动了原版哪些档案、多少内容 | **完成**，见 [`docs/07-游戏文件与文本覆盖清单.md`](docs/07-游戏文件与文本覆盖清单.md) |
| 内嵌网页（05）落地 | **已实现**（117 页 1097 段，重定向到 `chs_web/`），入口被停运的 Boneyards 挡住，见 [`docs/08-内嵌网页译文层.md`](docs/08-内嵌网页译文层.md) |
| 任务/GUI/单位资源落地（02/03/04） | **已实现**（1056 个文件 → `chs_res/`，hook 引擎的文件打开函数），见 [`docs/09-资源读取接管.md`](docs/09-资源读取接管.md) |
| CI 编译 DLL（msvc-wine v141，PE / 导出表门禁） | **完成**，见 `.github/workflows/build-release.yml` |
| 发布（构建 → 组装 zip → 自动 Release） | **完成**，推 `master` / `main` 即自动发版 |
| 联机实测 | 待做 |

**IDA 核心结论**：引擎自带完整本地化链路 —— `WinMain` 读 `Language` →
`translate/*.tdf` 按语言名查表 → **中央翻译函数 `sub_544020`（221 处调用）** →
`TextHandler`。但 GAF 位图字库是「12 槽 × 每槽 256 单字节字形」，
且引擎不做 DBCS 处理。因此 DLL 采用**混合方案**：
`Chinese` 语言槽管 UI 词条 + hook `sub_544020`/`sub_544110` 管其余文本 +
hook 字形绘制补 GBK 中文 + `version.dll` 代理注入，**磁盘游戏文件零改动**
（连 `binkw32.dll` 都不再改名，卸载就是删掉一个我们放进去的系统 DLL 同名文件）。
实机注册表里 `Language` 是 `ENGLISH`，引擎会对带 `English` 字段的词条"抢答"英文，
所以 DLL 在引擎命中后仍查一次字典 —— 详见 [`docs/02` §6.4.1](docs/02-IDA与DLL方案.md)。

## 目录

| 路径 | 内容 |
|---|---|
| [`text/`](text/README.md) | **明文待译表**：7 个 `.jsonl`、`manifest.json`、格式说明、[`glossary.md`](text/glossary.md) 与专名锁定表 [`terms.json`](text/terms.json) |
| [`docs/00-调研结论.md`](docs/00-调研结论.md) | 前期调查：版本谱系、六类载体、字库、注入方案 |
| [`docs/01-待译表与打包约定.md`](docs/01-待译表与打包约定.md) | HPI v2 索引块格式、数据规模、workflow 三段式接口、未解决项 |
| [`docs/02-IDA与DLL方案.md`](docs/02-IDA与DLL方案.md) | IDA 静态分析：文本管线、语言槽、字库上限、DLL 分层架构、hook 签名、联机保护、验证清单 |
| [`docs/03-DLL构建与验证.md`](docs/03-DLL构建与验证.md) | DLL 构建链、部署方式、实机验证流程 |
| [`docs/04-中文字库层.md`](docs/04-中文字库层.md) | **中文字库层**：字体槽布局、造帧 API、GBK 双字节绘制、换行/居中的字距表补丁、踩过的坑 |
| [`docs/05-版本差异与光盘校验.md`](docs/05-版本差异与光盘校验.md) | **4.0BB 与 4.1BB 的 43 字节差异**、版本号来源、光盘校验机制、内存短路的两处补丁 |
| [`docs/06-代理DLL注入.md`](docs/06-代理DLL注入.md) | **代理宿主选型**（为何是 `version.dll`）、17 个导出的裸跳板、进程守卫与那次「什么都没发生」的判定 bug、验证手段 |
| [`docs/07-游戏文件与文本覆盖清单.md`](docs/07-游戏文件与文本覆盖清单.md) | **档案级覆盖清单**：16 个 HPI / 1858 个文本文件逐个对账，三口径覆盖率、落地状态、没覆盖的 472 个文件到底是什么 |
| [`docs/08-内嵌网页译文层.md`](docs/08-内嵌网页译文层.md) | **05 组落地**：按原始字节区间改写 HTML、GBK + gb2312、`CreateFileA` 重定向到 `chs_web/`、为什么实机点不开这些页 |
| [`docs/09-资源读取接管.md`](docs/09-资源读取接管.md) | **02/03/04 组落地**：松散文件为何无效（`Override File Search Path`）、hook `sub_53AFF0`、构造引擎文件对象、`chs_res/` 与多版本取舍 |
| `tools/hpi_v2.py` | HPI v2 纯 Python 读取器：`list` / `extract` / `survey` / `inventory` / `verify`；同时支持 SQSH 压缩块与**裸字节条目**（block_size=0，少数任务简报/地图信息这样存） |
| `tools/extract_strings.py` | 从干净 41BB 抽取全部待译英文，生成 `text/*.jsonl` |
| `tools/extract_missing.py` | 补抽裸字节条目（missions/IPMissions 的 26 份任务简报、23 份地图信息），只追加不重建 |
| `tools/verify_strings.py` | 独立校验：格式、唯一性、占位符、编码指纹、与基线一致 |
| `tools/translate.py` | 用 LLM API 批量回填 `zh`/`status`：跨组去重、占位符强校验、断点续传、失败降级 |
| `tools/check_quality.py` | 机器体检：专名锁定、英文残留、数字丢失、搭配硬伤、人称统一、单位名词缀一致（`--strict` 供 CI 门禁） |
| `tools/fix_unit_names.py` | 单位名归一：按「石 / 冰封 / 受损 / 被毁 + 基础名」机械推导 260 余个状态变体，按英文名写进 `fixes.json` |
| `tools/audit_terms.py` | 单位名漂移审计：原文提到某单位、译文却用了别的叫法（`Drake Carcass` 曾译成「龙虾尸体」） |
| `tools/unify_translations.py` | 跨组译名统一：同一英文只允许一种译法 |
| `tools/retranslate_suspects.py` | 可疑条目单条重译（JSON 外壳污染 / 专名错 / 英文残留） |
| `tools/clean_text.py` | 清洗 GBK 编不了的字符，避免单字节嵌进双字节流造成错位 |
| `tools/build_assets.py` | 明文 → 资产补丁（CI 模式无需游戏本体） |
| `tools/verify_assets.py` | 产物独立复核：不复用构建器逻辑，专门防"两边同错" |
| `tools/check_tdf.py` | 按引擎真实实现（`sub_5429D0`）校验 TDF 语法，部署前拦截解析错误 |
| `tools/pack_payload.py` | 把 `build_out/update` 的资源打成 `payload/chs_res.zip` 入库（CI 无原版游戏，资源层必须预打） |
| `tools/verify_payload.py` | 独立复核 payload：解压重算摘要，并检查资源层是否已落后于 `text/` |
| `tools/stage_release.py` | 组装 `stage/`：DLL + 字典 + 资源层 + 说明 + 许可（含 `LICENSES/` 全文） |
| `tools/make_release_zip.py` | 打发布 zip：路径干净、固定时间戳、拦源码混入 |
| `tools/build_dll.sh` | 用 msvc-wine 的 `cl` / `link` 编译 `version.dll`（与 `build_version.bat` 同一组开关） |
| `tools/check_dll.py` | 发布门禁：PE32/i386、导出表与 `version.def` 逐条对齐、无转发 / 自导入、linker 必须是 14.16 |
| `inventory/` | `hpi_files.csv`（9037 条资源记录）、`non_translatable.csv`（921 项需人工制作资产） |
| `TAUtil/` | git submodule，MIT，© 2014 ArmouredFish —— 提供 HPI/GAF/TDF 结构定义 |
| `LICENSES/` | 基础许可 Apache-2.0 与第三方许可全文 |

## 命令速查

```bash
GAME="<干净 41BB 游戏目录>"

python tools/hpi_v2.py verify "$GAME"
python tools/extract_strings.py --game "$GAME" --out text
python tools/verify_strings.py --text text --against-game "$GAME"
python tools/check_quality.py --text text --strict

# 重跑抽取后恢复人工成果：顺序固定 B → A → C → 排版（详见 text/README.md）
python tools/apply_lore_terms.py --text text --write
python tools/fix_unit_names.py  --text text --write     # 单位名词缀归一（按英文名写入 fixes.json）
python tools/apply_term_fixes.py --text text --write
python tools/apply_polish.py    --text text --write
python tools/normalize_punct.py --text text --write
python tools/audit_terms.py     --text text             # 单位名漂移审计，应为 0
python tools/build_assets.py --text text --out build_out --slot Chinese --strict
python tools/verify_assets.py --out build_out --strict

# 本地端到端：额外产出 build_out/update/**（改写后的完整资源）
python tools/build_assets.py --text text --out build_out --slot Chinese --game "$GAME"
python tools/check_tdf.py build_out/update          # TDF 语法预检（deploy --res 会自动跑）
python tools/deploy.py --game "$GAME" --apply --res
```

CI（`.github/workflows/build-release.yml`）：
文本门禁（结构 + 质量 + GBK 安全）→ 资产（打包 + 独立复核 + payload 校验）→
msvc-wine 编译 DLL（PE / 导出表门禁）→ 组装 zip。push 到 `master` / `main` /
`rebuild` 时在**同一个 run** 里打 tag 并发 Release；PR 只构建不发版。

## 发布

发版是自动的：push 到 `master` / `main` / `rebuild`，工作流跑完门禁与构建后，
在同一个 run 里创建 `汉化补丁-YYYY-MM-DD-HH-MM-SS` 标签并发布 Release。
也可以在 Actions 页面手动触发，勾 `release` 立即发一版。

版本号写在仓库根的 `VERSION`（形如 `v0.1.0`），zip 名带日期时间：
`TAK-41BB-CHS-v0.1.0-YYYYMMDDHHMMSS.zip`。

DLL 由 msvc-wine 在 ubuntu 上以 **MSVC 14.16 (v141) + Win10 SDK 26100** 编译：
windows 托管镜像不带 v141，而工程要 `v141_xp` 才能保住老系统兼容。
`tools/check_dll.py` 会验 PE 头的 linker 版本必须是 14.16，工具集回退直接失败。

```bash
# 改了译文、要发新版时的完整顺序
python tools/build_assets.py --text text --out build_out --slot Chinese --game "$GAME"
python tools/pack_payload.py                 # 重打资源层，否则 payload 落后于 text/
python tools/verify_payload.py --strict      # 发布前必须绿

git add -A && git commit -m "..." && git push   # push 即发版
```

发布包只含玩家要用的东西（`version.dll`、`chs.dict`、`chs_res/`、`INSTALL.txt`、
`LICENSE`、`LICENSES/`、`NOTICE`），不含源码。本地想先看包长什么样：

```bash
python tools/stage_release.py --version v0.1.0 --dll build_dll/version.dll
python tools/make_release_zip.py --stage stage --out dist/TAK-41BB-CHS-v0.1.0-test.zip
```

`payload/chs_res.zip` 是入库的构建产物（1130 个文件，0.7 MB）。之所以不现场生成：
`chs_res` 由原版 HPI 改写而来，runner 上没有原版游戏（版权 + 体积）。

范式参考：`G:/Projects/MajestyIIExtend`（明文词典 + fontgen + ASI + msvc-wine CI）、
`G:/Projects/ArmyMenCHS`（不改 exe、`update/` 覆盖层、`LICENSES/`）。

## 许可

原创内容（代码、译文、本地化数据、构建与打包工具、文档）采用
**HaoJun0823 非商业源码可得许可 1.0（HJNCSA-1.0）**：以 Apache License 2.0 为基础
并附加非商业条件，**不是**开源许可，也**不是** Apache-2.0。允许非商业使用、修改与
再分发；再分发须随附完整对应源码与改动说明，并保留署名。

全文见 `LICENSE`，基础许可逐字全文见 `LICENSES/Apache-2.0.txt`。
第三方组件与游戏内容权属见 `packaging/NOTICE.txt`。
游戏素材版权归原权利人所有，本仓库不主张任何权利，也不分发游戏原始数据。
