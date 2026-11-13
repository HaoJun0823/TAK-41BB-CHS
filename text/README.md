# 明文待译表 · 格式说明与翻译契约

本目录是**翻译内容的唯一真相源**。游戏原始资源（HPI、网页、exe）不进仓库；
GitHub Action 从这里读明文，打包成游戏资产。

## 为什么用 JSONL

参考了两个既有仓库的范式后做的取舍：

| 范式 | 出处 | 优点 | 本项目为何没直接照搬 |
|---|---|---|---|
| `#KEY` + 译文两行，GBK | `MajestyIIExtend/Text/DictRead.txt` | 人工可读、极简 | 原文含 `;`、引号、`%s`、`<FONT>` 标签，且同一句在几十处出现，两行格式装不下出处与状态 |
| INI `序号=译文 ; 英文原文` | `ArmyMenCHS/Army1_Extend_Dll/docs/translation_format.md` | 中文直观、Windows 原生可读 | 面向「手写译文」；本项目译文将由 LLM API 批量回填，需要机器可解析与完备转义 |

**选 JSONL** 的四个理由：一行一条 → `git diff` 精确；JSON 转义完备 → 原文里的标点不会撑破格式；
结构化字段 → 能带出处、占位符、状态；LLM API 可以逐行喂、按 `id` 幂等回写。
文件统一 **UTF-8 无 BOM、LF 换行**。

> 翻译数据外置、不写死在 DLL 里，这条 ArmyMen 的原则保持不变：DLL 只做「读表 → 应用」。

## 字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 稳定唯一 ID：`<组号>-<sha1(kind|en) 前12位>`。游戏基线不变则永不变化 |
| `en` | string | **英文原文，禁止修改**。校验脚本会拿干净 41BB 重新抽取比对 |
| `zh` | string | 中文译文。初始为空字符串 |
| `status` | enum | `pending` 待译 / `done` 已译 / `skip` 不译 / `review` 待人工确认 |
| `ctx` | string | 送给翻译模型看的场景说明 |
| `kind` | enum | 资产内定位方式：`tdf_entry` / `tdf_field` / `txt_line` / `gui_line` / `web_text` / `icd_string` / `icd_ascii` |
| `placeholders` | string[] | 必须原样保留的占位符（`%s`、`%d`、`<FONT ...>`、`{0}` 等） |
| `flags` | string[] | 附加约束，见下 |
| `targets` | object[] | **所有写回位置**，每项 `{src, key}`。`src` 形如 `english.hpi::translate/gui_text.tdf` |

**关键设计：组内按英文原文去重，但保留全部写回位置。**
例如 `Click to select color` 在 GUI 里出现 5 次 → 表里只有 1 条，但 `targets` 有 5 项。
这样 LLM 只翻译一次，workflow 却能精确改写每一处，不会因为漏改位置而留下英文。

常用 `flags`：

| 值 | 含义 |
|---|---|
| `english_from_key` | 原 TDF 没写 `English` 槽，英文取自 `[词条名]` 本身 |
| `gui_fixed_length:N` | GUI 控件的文本是定长字段，改写时需同步更新长度数字 |
| `needs_review` | 需人工确认是否玩家可见（主程序组全部带此标记） |
| `candidate` | 候选池条目，默认不送翻 |

## 分组与优先级

| 文件 | 条数 | 内容 | 默认 status |
|---|---:|---|---|
| `01_ui_tdf.jsonl` | 1476 | 引擎自带多语言词条表 `translate/*.tdf` + 根目录 `Startup.tdf` | `pending` |
| `02_units.jsonl` | 699 | 单位/建筑定义里的 `name` / `description` 等显示字段 | `pending` |
| `03_missions.jsonl` | 298 | 任务目标简报（`missions/*.txt` 的 `•` 行）、`.ota` 任务名 | `pending` |
| `04_gui.jsonl` | 156 | GUI 布局里的按钮、标签、提示文本 | `pending` |
| `05_web.jsonl` | 1028 | 游戏内嵌网页（Boneyards / atlas / GC）的可见段落 | `pending` |
| `06_icd.jsonl` | 986 | 主程序硬编码串中高置信的 UI 文案 | `pending` + `needs_review` |
| `07_icd_candidates.jsonl` | 3766 | 主程序全部 ASCII 串候选池 | `review`（**不送翻**） |

合计 **8409 条唯一英文、15355 个写回位置**。`manifest.json` 记录各组条数与源游戏哈希。

> `07` 组存在的意义：静态规则无法可靠区分「玩家可见文案」和「C++ 符号 / 第三方库报错」。
> 保留全量候选，等 IDA 做完字符串交叉引用后，再把确认是 UI 的条目提升进 `06`。
> 这是「宁可留证据，不可瞎猜」；直接把 3766 条全送翻译只会污染术语和成本。

## 翻译契约（给稍后接入的 LLM API）

1. **只准写 `zh` 与 `status` 两个字段**，其余字段（尤其 `id` / `en` / `targets`）一律不动。
2. 按 `id` 幂等回写：先筛 `status == "pending"`，翻译完置 `status = "done"`；
   断点续传时跳过已 `done` 的条。
3. `placeholders` 里的每一项必须**原样出现**在 `zh` 里，顺序可变但不可丢、不可改写。
4. 专有名词按 [`glossary.md`](glossary.md) 统一；术语表未定的，先保留英文并在 `status` 用 `review` 标记。
5. `07_icd_candidates.jsonl` 与所有 `status == "review"` 的条**默认不送翻**。
6. 每批回写后跑一次 `python tools/verify_strings.py --text text`，失败即停止（ArmyMen 的 CI 三段式第三段）。

## 翻译回填

译文由 `tools/translate.py` 调 LLM API 批量回填，只改 `zh` 与 `status`：

```bash
# 凭据放在仓库外，用文件或环境变量指过来（不要提交）
export TAK_API_FILE="<本地凭据文件>"

python tools/translate.py --text text --groups 01,02,03,04,05,06 --workers 8
python tools/translate.py --text text --dry-run          # 只统计分批与跳过项
python tools/translate.py --text text --retry-review     # 重试失败条目
```

要点：

* 同一英文**跨组共享译文**，避免 `Archer` 在 UI 表和单位表里译法不同；
* 批量请求以**英文原文为键**，不用序号 —— 模型少返一条也不会整体错位；
* `status` 流转：`pending → done`（成功）/ `skip`（快捷键、模板变量、纯符号）/ `review`（多次重试仍不合格）；
* 占位符、行首 `•`、长度异常、无中文、原样照抄，任一不满足即重试，最终仍失败才落 `review`；
* 断点续传：缓存 `build_out/cache/translations.json`，中断后重跑会跳过已译条目。

专名由 [`terms.json`](terms.json) 锁定：翻译脚本把整张表塞进 system prompt。
改完专名表会自动忽略旧缓存（`terms_hash` 不一致），但**已写入 jsonl 的译文不会自动更新**，
需要人工改或删掉对应 `zh` 后重跑。

## 命令

```bash
# 从干净 41BB 重新抽取（游戏基线不变时产出应逐字节一致）
python tools/extract_strings.py --game <41BB目录> --out text

# 只统计不写文件
python tools/extract_strings.py --game <41BB目录> --check

# 独立校验（格式 / 唯一性 / 占位符 / 编码指纹 / 与基线一致）
python tools/verify_strings.py --text text
python tools/verify_strings.py --text text --against-game <41BB目录>

# workflow 打包（CI 模式，不需要游戏本体）
python tools/build_assets.py --text text --out build_out --slot Chinese --strict
```

## 人工定稿的三层（重跑抽取后按序重放）

`*.jsonl` 是抽取产物，重跑 `extract_strings.py` 会把人工改的 `zh` 冲掉。
人工成果一律外置成表，任何时候按顺序各跑一次即可完整恢复：

| 层 | 真相源 | 内容 | 重放命令 |
|---|---|---|---|
| A 档 | `fixes.json` | 界面短词的定稿译法 + 定点替换 | `python tools/apply_term_fixes.py --text text --write` |
| B 档 | `lore_terms.json` | 专名与术语锁定（`canonical` 英文→定稿，`variants` 旧译名→定稿） | `python tools/apply_lore_terms.py --text text --write` |
| C 档 | `polish.json` | **518 条长文本逐段重写**（剧情、百科、帮助长句），`id -> 定稿 zh` | `python tools/apply_polish.py --text text --write` |
| 排版 | —（就地改写） | 列表符号、半/全角标点、`"…"`→`「…」`、引号两侧空格 | `python tools/normalize_punct.py --text text --write` |

**顺序固定为 B → A → C → 排版**：先统一专名，再套短词定稿，接着用整段定稿覆盖，
最后跑一遍标点归一——因为 `polish.json` 里存的是人手原文，可能带弯引号或半角标点，
必须由最后一步收口。`apply_polish.py` 每次套用前会校验 `id` 存在、
`<$模板$>` 与 `%s/%d` 原样保留，任一不符就整体中止。

C 档的体例是「意译优先，音译兜底」：语序按中文重排，括号补充只在必要时留，
专名写法一律以 `lore_terms.json` 为准。质量门 `check_quality.py` 另外查
旧译名残留、重复括注、超长句，共 10 项，加 `--strict` 有违规即退出 1。

打包产物与 TODO 见 [`docs/01-待译表与打包约定.md`](../docs/01-待译表与打包约定.md)。
