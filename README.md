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
| 资产打包（明文 → 补丁，CI 三段式） | **完成**，见 `.github/workflows/build-assets.yml` |
| DLL 实现（文本 hook + 代理注入） | **完成**，见 `docs/03-DLL构建与验证.md` |
| 中文字库层（运行时纹理帧） | **完成并实机验证**，见 [`docs/04-中文字库层.md`](docs/04-中文字库层.md) |
| 4.1BB 光盘校验（内存短路，磁盘不动） | **完成并实机验证**，见 [`docs/05-版本差异与光盘校验.md`](docs/05-版本差异与光盘校验.md) |
| 代理宿主改为 `version.dll`（不再改 `binkw32.dll`） | **完成并实机验证**，见 [`docs/06-代理DLL注入.md`](docs/06-代理DLL注入.md) |
| 覆盖清单：动了原版哪些档案、多少内容 | **完成**，见 [`docs/07-游戏文件与文本覆盖清单.md`](docs/07-游戏文件与文本覆盖清单.md) |
| 内嵌网页（05）落地 | **已实现**（117 页 1097 段，重定向到 `chs_web/`），入口被停运的 Boneyards 挡住，见 [`docs/08-内嵌网页译文层.md`](docs/08-内嵌网页译文层.md) |
| 任务/GUI/单位资源落地（02/03/04） | **已实现**（1056 个文件 → `chs_res/`，hook 引擎的文件打开函数），见 [`docs/09-资源读取接管.md`](docs/09-资源读取接管.md) |
| CI 编译 DLL（`build-dll.yml`，带导出表门禁） | **完成** |
| 联机实测 | 待做 |

**IDA 核心结论**：引擎自带完整本地化链路 —— `WinMain` 读 `Language` →
`translate/*.tdf` 按语言名查表 → **中央翻译函数 `sub_544020`（221 处调用）** →
`TextHandler`。但 GAF 位图字库是「12 槽 × 每槽 256 单字节字形」，
且引擎不做 DBCS 处理。因此 DLL 采用**混合方案**：
`Chinese` 语言槽管 UI 词条 + hook `sub_544020`/`sub_544110` 管其余文本 +
hook 字形绘制补 GBK 中文 + `version.dll` 代理注入，**磁盘游戏文件零改动**
（连 `binkw32.dll` 都不再改名，卸载就是删掉一个我们放进去的系统 DLL 同名文件）。

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
| `tools/hpi_v2.py` | HPI v2 纯 Python 读取器：`list` / `extract` / `survey` / `inventory` / `verify` |
| `tools/extract_strings.py` | 从干净 41BB 抽取全部待译英文，生成 `text/*.jsonl` |
| `tools/verify_strings.py` | 独立校验：格式、唯一性、占位符、编码指纹、与基线一致 |
| `tools/translate.py` | 用 LLM API 批量回填 `zh`/`status`：跨组去重、占位符强校验、断点续传、失败降级 |
| `tools/check_quality.py` | 机器体检：专名锁定、英文残留、数字丢失（`--strict` 供 CI 门禁） |
| `tools/unify_translations.py` | 跨组译名统一：同一英文只允许一种译法 |
| `tools/retranslate_suspects.py` | 可疑条目单条重译（JSON 外壳污染 / 专名错 / 英文残留） |
| `tools/clean_text.py` | 清洗 GBK 编不了的字符，避免单字节嵌进双字节流造成错位 |
| `tools/build_assets.py` | 明文 → 资产补丁（CI 模式无需游戏本体） |
| `tools/verify_assets.py` | 产物独立复核：不复用构建器逻辑，专门防"两边同错" |
| `inventory/` | `hpi_files.csv`（9037 条资源记录）、`non_translatable.csv`（921 项需人工制作资产） |
| `TAUtil/` | git submodule，MIT，© 2014 ArmouredFish —— 提供 HPI/GAF/TDF 结构定义 |
| `LICENSES/` | 第三方许可全文 |

## 命令速查

```bash
GAME="<干净 41BB 游戏目录>"

python tools/hpi_v2.py verify "$GAME"
python tools/extract_strings.py --game "$GAME" --out text
python tools/verify_strings.py --text text --against-game "$GAME"
python tools/check_quality.py --text text --strict
python tools/build_assets.py --text text --out build_out --slot Chinese --strict
python tools/verify_assets.py --out build_out --strict

# 本地端到端：额外产出 build_out/update/**（改写后的完整资源）
python tools/build_assets.py --text text --out build_out --slot Chinese --game "$GAME"
```

CI 三段式（`.github/workflows/build-assets.yml`）：
`verify-text`（结构 + 质量 + GBK 安全）→ `package`（打包 + 独立复核）→
`recheck`（在干净容器里只下载 artifact 再验一次）。

范式参考：`G:/Projects/MajestyIIExtend`（明文词典 + fontgen + ASI）、
`G:/Projects/ArmyMenCHS`（不改 exe、`update/` 覆盖层、`LICENSES/`、CI 三段式）。

## 许可

项目自身许可待定稿；随包的第三方组件许可见 `LICENSES/`。
游戏素材版权归原权利人所有，本仓库不主张任何权利。
