# TAK 41BB 全量汉化（CHS）

《Total Annihilation: Kingdoms》**4.1BB** 的全量简体中文化工程。

**硬约束**：汉化游戏内全部可见文本；游戏外文档不译；**保留联机**；
磁盘上的游戏文件保持原版哈希（改动只在运行时内存 / `update/` 覆盖层）。

> 本仓库不包含、也不分发任何游戏原始素材。使用者需自行持有合法副本。

## 当前状态

| 阶段 | 状态 |
|---|---|
| 版本裁决与基线 | 完成，见 `docs/00-调研结论.md` |
| HPI v2 容器解析、全量文本盘点 | 完成，9037 个文件可导出、逐条校验 0 失败 |
| 全量待译表（明文、可 diff、LLM 可回填） | **完成**，8409 条 / 15355 个写回位置 |
| 翻译（待接入可靠的翻译 LLM API） | 未开始 |
| DLL 方案（IDA 定位语言槽 / 字库 / hook 点） | **完成**，见 `docs/02-IDA与DLL方案.md` |
| workflow 编译与打包、联机实测 | 待做 |

**IDA 核心结论**：引擎自带完整本地化链路 —— `WinMain` 读 `Language` →
`translate/*.tdf` 按语言名查表 → **中央翻译函数 `sub_544020`（221 处调用）** →
`TextHandler`。但 GAF 位图字库是「12 槽 × 每槽 256 单字节字形」，
且引擎不做 DBCS 处理。因此 DLL 采用**混合方案**：
`Chinese` 语言槽管 UI 词条 + hook `sub_544020`/`sub_544110` 管其余文本 +
hook 字形绘制补 GBK 中文 + `binkw32.dll` 代理注入，**磁盘游戏文件零改动**。

## 目录

| 路径 | 内容 |
|---|---|
| [`text/`](text/README.md) | **明文待译表**：7 个 `.jsonl`、`manifest.json`、格式说明与 [`glossary.md`](text/glossary.md) |
| [`docs/00-调研结论.md`](docs/00-调研结论.md) | 前期调查：版本谱系、六类载体、字库、注入方案 |
| [`docs/01-待译表与打包约定.md`](docs/01-待译表与打包约定.md) | HPI v2 索引块格式、数据规模、workflow 三段式接口、未解决项 |
| [`docs/02-IDA与DLL方案.md`](docs/02-IDA与DLL方案.md) | IDA 静态分析：文本管线、语言槽、字库上限、DLL 分层架构、hook 签名、联机保护、验证清单 |
| `tools/hpi_v2.py` | HPI v2 纯 Python 读取器：`list` / `extract` / `survey` / `inventory` / `verify` |
| `tools/extract_strings.py` | 从干净 41BB 抽取全部待译英文，生成 `text/*.jsonl` |
| `tools/verify_strings.py` | 独立校验：格式、唯一性、占位符、编码指纹、与基线一致 |
| `tools/build_assets.py` | 明文 → 资产补丁（CI 模式无需游戏本体） |
| `inventory/` | `hpi_files.csv`（9037 条资源记录）、`non_translatable.csv`（921 项需人工制作资产） |
| `TAUtil/` | git submodule，MIT，© 2014 ArmouredFish —— 提供 HPI/GAF/TDF 结构定义 |
| `LICENSES/` | 第三方许可全文 |

## 命令速查

```bash
GAME="<干净 41BB 游戏目录>"

python tools/hpi_v2.py verify "$GAME"
python tools/extract_strings.py --game "$GAME" --out text
python tools/verify_strings.py --text text --against-game "$GAME"
python tools/build_assets.py --text text --out build_out --slot Italian --strict
```

范式参考：`G:/Projects/MajestyIIExtend`（明文词典 + fontgen + ASI）、
`G:/Projects/ArmyMenCHS`（不改 exe、`update/` 覆盖层、`LICENSES/`、CI 三段式）。

## 许可

项目自身许可待定稿；随包的第三方组件许可见 `LICENSES/`。
游戏素材版权归原权利人所有，本仓库不主张任何权利。
