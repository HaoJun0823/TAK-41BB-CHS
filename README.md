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
| DLL 方案（IDA 定位语言槽 / 字库 / hook 点） | 待做 |
| workflow 编译与打包、联机实测 | 待做 |

## 目录

| 路径 | 内容 |
|---|---|
| [`text/`](text/README.md) | **明文待译表**：7 个 `.jsonl`、`manifest.json`、格式说明与 [`glossary.md`](text/glossary.md) |
| [`docs/00-调研结论.md`](docs/00-调研结论.md) | 前期调查：版本谱系、六类载体、字库、注入方案 |
| [`docs/01-待译表与打包约定.md`](docs/01-待译表与打包约定.md) | HPI v2 索引块格式、数据规模、workflow 三段式接口、未解决项 |
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
