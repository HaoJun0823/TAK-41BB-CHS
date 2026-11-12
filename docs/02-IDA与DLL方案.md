# 02 · IDA Pro 静态分析与 DLL 实施方案

> 本文回答用户上一轮指定的第二个任务：**「去用 IDA Pro 看看我们该怎么做这个 DLL」**。
> 前置任务（整理全量明文待译表并 git 提交）已完成，见 `docs/01-待译表与打包约定.md`。

## 0. 本文定位与分析基线

| 项 | 值 |
|---|---|
| 分析对象 | `I:/LocalGames/Total Annihilation Kingdoms/Total Annihilation Kingdoms_41BB/KINGDOMS.icd` |
| sha256 | `dfcb2673ddeb019ce6cf91b632498afdb4e40c49a713d51e7d5e1d17f288e1e7` |
| imagebase | `0x400000`（无 ASLR，地址可直接用） |
| IDA session | `0e0104bd`，Hex-Rays 可用，9062 函数 / 6041 字符串 |
| 段布局 | `.text 0x401000–0x5EB000`、`.idata`、`.rdata`、`.data` |
| 工具 | IDA Pro + `mrexodia/ida-pro-mcp`（不用 Ghidra） |

**本文所有地址只对这个干净 41BB 有效。** 原游戏目录里那份 `KINGDOMS.icd` 是
30BB（`1144a394889811ae…`），地址不通用；前人 v1.0 外挂的 `0x56A810` 在
41BB 上是函数尾部/NOP 区，签名全文件搜不到，**绝不能复用**。

硬约束沿用项目基线：汉化全部游戏内文本、游戏外不译、**保留联机**、
磁盘游戏文件哈希不变、明文 `text/*.jsonl` 是唯一真相源、由 GitHub Action 打包。

---

## 1. 结论速览

一句话：

> **引擎自带一条完整的本地化管线，但字库只有「12 槽 × 256 个单字节字形」。
> 所以 DLL 的重心不是「逆向渲染函数做逐词替换」，而是
> 「接管中央翻译查表 + 补一套 GBK 双字节字形渲染」。**

这比 `docs/00` 里最初的设想（重写渲染函数）轻得多，也比重写 HPI 安全得多。

| 决策问题 | 结论 | 依据 |
|---|---|---|
| 要不要改磁盘游戏文件 | **不要**，全部走运行时内存 | 联机版本校验 + 基线哈希约束 |
| 要不要重写文本渲染做逐词替换 | **不需要**作为主路径；hook 中央查表函数即可 | `sub_544020` 有 **221 处**调用，是所有 UI 文本统一出口 |
| 能否新增 `Language=Chinese` 语言槽 | **能**，查表用 `_strcmpi` 且无语言白名单 | `sub_543110` / `sub_543E40` |
| 能否占用现有 `German`/`Italian` 槽 | **不能占用 German** | `sub_49A030` 有德语特判分支 |
| 中文能否直接塞进现有 GAF 字库 | **不能**，单字节索引、每槽 256 字形 | `FontHandler` 构造函数 `sub_58B480` |
| 引擎是否 DBCS / GBK 感知 | **否**，纯字节串 | 所有 codepage API 的 xref 全在静态 MSVCRT |
| 注入通道 | `binkw32.dll` 代理（Ultimate ASI Loader） | 导入表按名字导入 10 个 `_Bink*` |
| 旧外挂 `0x56A810` | **不可用** | 41BB 该处不是函数入口 |

---

## 2. 引擎文本管线（完整调用链）

### 2.1 启动：读取语言配置

`sub_532F10`（WinMain，`0x532F10`）关键片段：

```c
sub_4FA030(1, "InterfaceOptions");            // HKCU\Software\Cavedog Entertainment\Kingdoms
sub_4FA740("Language", Data, 0x40u, nullptr); // -> sub_4FA2E0 -> RegQueryValueExA
if ( !Data[0] ) strcpy(Data, "english");      // 默认 0x615324 "english"
...
sub_48CFE0();                                 // 探测 CD / 数据目录
sub_48CD10();                                 // 枚举并加载 *.HPI / *.UFO
sub_543CD0("translate", "*.tdf", Data);       // 加载翻译表
```

**顺序很重要**：HPI 银行先初始化，之后才枚举 `translate/*.tdf`，
所以翻译表既可以来自 HPI 内部，也可以来自磁盘散文件。

### 2.2 翻译表总控

`sub_543CD0`（`0x543CD0`）：

```c
strcpy(&byte_655328, v3);        // 语言名存到全局 0x655328
sub_53AAA0(a1, a2, v14);         // 枚举 "translate\*.tdf"
for ( i = 0; i < 文件数; ++i )
    sub_543E40(文件名[i]);        // 逐文件解析
```

`byte_655328` 是 **0x40 字节**全局缓冲（下一个符号正是 `0x655368`），
且这里是**无界 `strcpy`**：语言名必须短，`"Chinese"` 安全，
别用 `chinese-simplified` 这种长串。`sub_543CB0()` 负责读取（空串返回 NULL）。

### 2.3 TDF 解析：语言槽没有白名单

`sub_543E40`（`0x543E40`）逐块处理：

```c
sub_542F30(Destination, 0xFFu);                       // 取 [块名]
sub_5432C0(v7, &byte_655328, 0xFFu, &unk_655368);    // 在块内以「语言名」为键取值
if ( strlen(v7) ) { /* 插入全局映射 dword_655324 */ }
```

`sub_5432C0` → `sub_543110`（`0x543110`）是 `std::map` 风格的二分查找：

```c
if ( _strcmpi(*v4, String2) >= 0 ) ...                // 大小写不敏感
if ( v3 == end || _strcmpi(String2, *v3) < 0 ) return nullptr;
return v3[1];                                        // 返回该键的值
```

**推论 1**：`Language=Chinese` + 每个 `[词条]` 里补一行 `Chinese = 译文;`
即可让引擎原生生效，零 hook。语言名只是 TDF 里的键名，**没有枚举白名单**。

**推论 2（实测 TDF 印证）**：`translate/gui_text.tdf` 里

```tdf
[Attack]
{
    German = Angreifen;
    French = A l'attaque;
    Italian = Attacca;
    Spanish = Ataque;
}
```

没有 `English` 槽 —— 因为查不到就原样返回，此时 **`[块名]` 本身就是英文显示文本**。
而 `translate/messages.tdf` 里

```tdf
[DIFFERENT_MAPS_SAME_NAME]
{
    English = Not all players have the same version of this map;
    German  = ...;
}
```

有 `English` 槽 —— 此时显示文本是 `English` 的值，**块名只是短语 ID**。
这个区别会直接影响运行时字典的键怎么生成，见 §6.2。

### 2.4 【核心】中央翻译函数 `sub_544020`

`0x544020`，原型 `const char *__stdcall sub_544020(const char *a1)`（`ret 4`）：

```c
if ( !a1 ) return nullptr;
if ( !dword_655324 ) return a1;                 // 映射未建 -> 返回原文
// 在全局映射里 strcmp 二分查找（区分大小写）
if ( 命中 ) return v3[1];                       // 返回译文
return v1;                                      // 未命中 -> 返回原文
```

**IDA 统计：221 处代码交叉引用**，覆盖 `0x428C70`、`0x44EFC0`、
`0x49B6A0`、`0x4BFA50`、`0x4C7A50`、`0x58C840` 等几乎所有 UI 模块。
这是全游戏 UI 文本的统一出口，也是 DLL 最值得挂的一个点。

### 2.5 文本登记与引擎自带的「免翻」标记

`sub_58C840`（`0x58C840`，`TextHandler` 登记文本）：

```c
if ( (a3 & 0x8000) == 0 )
    Source = (char *)sub_544020(Source);   // 未打标记才翻译
// 存入最多 1500 条文本表
```

`a3 & 0x8000` 是引擎**自带的动态串隔离位**：玩家名、聊天、房间名这类
不该本地化的内容大概率已带此标记。`sub_58CAB0` / `sub_58CD50` 也用
`BYTE1(flag) & 0x80` 判断同一个位。这是联机白名单设计的现成依据。

### 2.6 反查函数 `sub_544110`（必须成对 hook）

`0x544110` 把「译文」反查回「英文键」：

```c
while ( _strcmpi(v1[1], String2) )  // 用 value 比较
    v1 += 2;
return *v1;                         // 返回 key
```

调用方：`sub_58C7C0`（TextHandler 析构）、`sub_58CAB0`、`sub_58CD50`。
**如果 DLL 只 hook 正向查表，中文译文不会进引擎的映射，反查会返回 NULL**，
调用方就会拿着中文串去做标识/序列化 → 存档与联机协议风险。
所以 `0x544020` 与 `0x544110` 必须成对 hook，共用一份双向字典。

### 2.7 第二条本地化通道：字段级语言前缀

`sub_544160`（`0x544160`）：

```c
strcpy(String2, &byte_655328);     // "english" / "Chinese"
strcat(String2, a3);               // 例如 "englishmissionname"
if ( sub_5432C0(out, String2, n, def) ) return 1;   // 先查带语言前缀的字段
return sub_5432C0(out, a3, n, def);                  // 回退到原字段
```

即 TDF 数据文件允许写 `<Language><field>` 覆盖同名字段（如任务名）。
`sub_4C7580` 用它取 `missionname`。可用于 03 组任务简报/任务名。

---

## 3. 编码与字库：真正的技术障碍

### 3.1 引擎完全不认 DBCS

KERNEL32 导入里确实有 `GetCPInfo`、`GetACP`、`IsValidCodePage`、
`MultiByteToWideChar`、`WideCharToMultiByte`、`GetStringTypeA/W`、
`LCMapStringA/W`、`CompareStringA/W` —— 但把 xref 全列出来后，
**引用点无一例外落在 `0x5C0000–0x5E8000`，即静态链接的 MSVCRT**
（`__setmbcp`、`___crtCompareStringA`、`__mbtowc_lk`、`___init_ctype` …）。
游戏自己的代码区间 `0x401000–0x5BFFFF` 一处都没有，
而且**没有导入 `IsDBCSLeadByte`**。

结论：引擎字符串是纯字节串 —— `strlen` / `strcmp` / `_strcmpi` / 逐字节索引。
GBK 双字节汉字会被当成两个独立字符，既不会正确断行，也不会正确取字形。

### 3.2 GAF 位图字库：256 字形上限（决定性证据）

`FontHandler` 构造函数 `sub_58B480`（`0x58B480`），vtable
`??_7FontHandler@@6B@ @ 0x5F57C8`：

```c
*(this + 15483) = -1;
*this = &FontHandler::`vftable';
v3 = this + 3;
v7 = 12;                              // 12 个字库槽
do {
    ...
    v5 = 256;                         // 每槽 256 个字形条目
    do {                              // 每条 5 个 DWORD = 20 字节
        *(v4-2)=0; *(v4-1)=0; *v4=0; v4[1]=0; v4[2]=-1;
        v4 += 5; --v5;
    } while (v5);
    v3 += 1290; --v7;
} while (v7);
```

**12 槽 × 256 字形 = 按单字节索引、每串最多 256 个不同字形。**
中文常用字上千，物理上塞不进去，改结构体会破坏整个 `FontHandler` 内存布局。

相关链路：

| 地址 | 作用 |
|---|---|
| `sub_58B5D0` | 设置字体目录字符串，值是 `Fonts\`（`0x61E534`） |
| `sub_56FFC0` | 字体初始化，加载 `Times New Roman (100B).gaf` |
| `sub_58C460` | 选择当前字库槽（`a2 < 12`） |
| `sub_58D360` | 按「目录 + GAF 名」载入 GAF 资源（最多 1024 项） |
| `sub_575B10` | 通过 `dword_65DDC0` 的虚方法 +24 按名取字体 |
| `sub_58BA90` | 释放某槽，同样遍历 256 个字形条目 |

### 3.3 GDI 旁路存在，但不能单独依赖

另有 `CreateFontIndirectA`（`0x5EB04C`，缓存于 `sub_448A30`）、
`TextOutA`（`sub_447530`）、`GetTextExtentPoint32A`（`sub_447450` /
`sub_44BE90`）、`GetTextMetricsA`（`sub_44BF20`）、`SetTextColor`、
`SelectObject`。这是一套 GDI 字体实现（vtable 在 `0x5EDAB0` 附近）。

但 `TextOutA` 是 **A 版，按系统 ACP 解释字节**：
中文 Windows（CP936）能渲染 GBK；西文 Windows（CP1252）必然乱码。
所以它只能做兜底，不能作为唯一方案。

### 3.4 由以上两点得到的硬结论

1. 译文编码选 **GBK / CP936**（与已验证的 `chn12.dot` HZK、系统 ACP 一致），
   **不能用 UTF-8**。`build_assets.py` 里的 `encode_game()` 已经是这个思路
   （原单字节走 cp1252，中文逐字符走 GBK）。
2. 必须自己补 DBCS 字形渲染：hook 文本绘制，遇 `0x81–0xFE` 前导字节时
   取双字节 → 查外置字形图集 → blit 到目标表面；ASCII 仍走原 GAF 字形。
3. 断行与宽度要同步：汉字按「2 字节 = 1 个全角宽」计，
   `GetTextExtentPoint32A` / GAF 宽度表都要一致，否则文字会重叠或溢出按钮。
4. 可用已验证的 `chn12.dot`：标准 12×12 HZK，24066 字形 × 24 字节，
   GBK 索引 `offset = ((lead - 0x81) * 191 + (trail - 0x40)) * 24`；
   也可在 CI 用 TTF 生成更清晰的字形图集。

---

## 4. 语言槽机制的陷阱

| 陷阱 | 证据 | 处理 |
|---|---|---|
| **德语特判** | `sub_49A030`（`0x49A030`）用 `_strcmpi(sub_543CB0(), "German")` 置一个选项标志，属于德语版特有行为 | **不要用 German 槽装中文**，否则连带触发德语版逻辑 |
| english 分支 | `sub_4C7740` 里只有「语言 != english」才走任务名翻译 | 设成 `Chinese` 反而覆盖更完整 |
| 缓冲区 | `byte_655328` 只有 0x40 字节，`sub_543CD0` 用无界 `strcpy` | 语言名保持短串，`"Chinese"` 即可 |
| 大小写 | `sub_544020` 内部 `strcmp`（区分大小写）；`sub_4C7740` 会先 `_strlwr` 再查 | 字典需同时提供原大小写与全小写键 |
| CRT locale 字符串误导 | `0x5F7D88` 附近有 `chinese` / `chinese-simplified` / `pr-china` / `OCP` / `ACP` | 那是 **MSVCRT locale 名称表**，不是引擎原生中文支持，别据此判断 |
| 只初始化一次 | `sub_543CD0` 在 WinMain 中仅调一次 | 语言 hook 必须在 `DLL_PROCESS_ATTACH` 阶段就绪（ASI Loader 早于 WinMain，满足） |

---

## 5. 资源加载与覆盖层（可用但不推荐主用）

- `sub_48CD10`（`0x48CD10`）：`_findfirst("*.HPI")`、`_findfirst("*.UFO")`、
  逐盘符 `sprintf("%c:\\*.hpi")` 枚举，交给 `sub_53CD80` 注册。
- `sub_539DC0`（`0x539DC0`）是 VFS 目录枚举：先遍历所有 HPI bank 的匹配项，
  再 `_findfirst` 磁盘目录，再按 `v5[2]` 前缀合并第二数据路径，用 `_strcmpi` 去重。
- 因此**新增一个小 HPI 或磁盘散文件确实能注入 `translate/*.tdf`**；
  但覆盖优先级（同名时谁赢）代码里的合并条件较复杂，**尚未实机验证**。

**联机建议**：不新增 HPI、不改任何规则/数值数据。
主路径走 DLL 内存补丁 + 外置 `chs.dict` / `chs.glyph`，
HPI 覆盖层只作为「静态资源确实无法运行时替换」时的备选（如 05 组内嵌 HTML）。

---

## 6. 推荐 DLL 架构（分层）

| 层 | 组件 | 挂点 / 数据 | 风险 |
|---|---|---|---|
| 注入 | Ultimate ASI Loader 作 `binkw32.dll` 代理 | 原 DLL 改名转发 10 个 `_Bink*` | 低 |
| 翻译 | 正向字典 hook | `sub_544020` `0x544020`，221 调用点 | 中（动态串、大小写） |
| 反查 | 反向字典 hook | `sub_544110` `0x544110` | 中（必须成对） |
| 语言 | 强制 `Language=Chinese` | 注册表写入 或 `RegQueryValueExA` hook | 低 |
| 字库 | GBK 字形渲染 + 宽度修正 | `FontHandler` 绘制虚方法 / `sub_447450` | **高**（本方案最难部分） |
| 数据 | `chs.dict` / `chs.glyph` / 调用点白名单 | CI 由 `text/*.jsonl` 生成 | 低 |

### 6.1 注入层

`KINGDOMS.icd` 按名字导入 `binkw32.dll`（`0x5EB454–0x5EB478`：
`_BinkOpenMiles@4`、`_BinkClose@4`、`_BinkPause@8`、`_BinkNextFrame@4`、
`_BinkCopyToBuffer@28`、`_BinkDoFrame@4`、`_BinkWait@4`、`_BinkOpen@8`、
`_BinkGoto@12`、`_BinkSetSoundSystem@8`）。

做法：把官方 Ultimate ASI Loader 的 `binkw32.dll` 放进游戏目录，
原版改名为 `binkw32_original.dll`（或 loader 约定的名字）并由代理转发；
我们的 DLL 编成 `.asi`/`.dll` 由 loader 在游戏启动时加载。
`KINGDOMS.icd` 与全部原 HPI **一个字节都不改**，卸载只需删掉这几个文件。

> 前人的「ASI loader 外挂」其实是给原版 `binkw32.dll` 打了 41 字节补丁
> 插 `LoadLibrary("gtsp.dll")`，`MP3DEC.ASI` 只是 Miles 音频插件。
> 代理方案更干净、可回退，且与「保留联机」不冲突。

工程侧：现有 `TAK_41BB_CHS.vcxproj` 是 Win32 / `v141_xp`，方向正确；
需补 `/utf-8`、MinHook（或手写 5 字节 detour）、以及按签名而非硬编码地址定位。

### 6.2 翻译层（核心）

Hook `sub_544020`，`__stdcall const char*(const char*)`，1 个参数、`ret 4`。
定位签名（避免硬编码地址，也便于以后适配小版本）：

```text
55 8B EC 83 EC 08 53 8B 5D 08 56 57 85 DB 75 0B
5F 5E 33 C0 5B 8B E5 5D C2 04 00 A1 ?? ?? ?? ??
```

Detour 逻辑：

```text
1. 先调用原函数（保留引擎 TDF 的结果，不破坏原生语言槽）。
2. 若原函数返回值 == 输入串（未命中），用外置字典按输入串精确匹配。
3. 命中则返回一个 DLL 内常驻的 GBK 字节串（绝不能返回栈上临时缓冲）。
4. 未命中原样返回。
```

四个必须写进实现的数据契约：

1. **字典键是「引擎传入的串」**，不是 `en` 字段。
   对 TDF 词条来说键 = `[块名]`（短语 ID），对硬编码串来说键 = 字面量。
   CI 生成器应从 `text/01_ui_tdf.jsonl` 的 `targets[].key` 取键、`zh` 取值。
   `en` 只是显示英文（例如 `[DIFFERENT_MAPS_SAME_NAME]` 的
   `English` 槽是整句话，与键完全不同）。
2. **占位符原样保留**。`sub_428C70` 把译文直接当格式串：
   `sub_544020("BYPAGE_REQ_SENT")` → `_snprintf(buf, 0x1FE, v12, v10)`。
   TDF 实测该条目 `English = A page request for %s has been sent.;`。
   `%s` / `%d` / `<FONT ...>` 必须保序保留 —— `verify_strings.py` 已在校验。
3. **大小写双键**。`sub_544020` 内部是 `strcmp`（区分大小写），
   而 `sub_4C7740` 会先 `_strlwr`。建议字典以「小写化字节」做 hash 索引，
   再对原串做二次确认；CI 同时生成原大小写与全小写键。
4. **首字节陷阱（必须例外处理）**。`sub_432290` 用
   `*sub_544020("MESG_BOX_OK")` 取**首字节**当按钮热键；
   `translate/shortcutkeys.tdf` 实测 `English = O;` / `C;` / `Y;` / `S;`。
   译成中文后首字节变成 GBK 高位（`0xC8`…），热键判断立刻失效。
   这 4 个 `MESG_BOX_*` 词条应标 `status=skip`，或译文首字节强制保留 ASCII 字母。

### 6.3 反查层

Hook `sub_544110`：

```text
55 8B EC 56 57 8B 7D 08 85 FF 74 31 A1 ?? ?? ?? ??
85 C0 74 28 8B 70 05 8B 48 09 3B F1 73 1E 8B
```

用同一份字典建「中文 → 英文键」反表（大小写不敏感），
保证 `TextHandler` 取回/序列化时仍能拿到英文标识，
避免存档与联机协议里混入中文。

### 6.4 语言层（可选但推荐）

目的：让 `sub_4C7740` 的「非 english 才翻译」分支生效，
并让 `translate/*.tdf` 的 `Chinese` 槽被原生读取。

- 做法 A（轻）：DLL 启动时写一次注册表
  `HKCU\Software\Cavedog Entertainment\Kingdoms\InterfaceOptions\Language = "Chinese"`，
  退出时恢复；不改游戏文件。
- 做法 B（无副作用）：hook `RegQueryValueExA` 或 `sub_4FA740`，
  仅当 `lpValueName == "Language"` 时返回 `"Chinese"`。

### 6.5 字库层（最难，需分阶段验证）

- `FontHandler` vtable 在 `0x5F57C8`，字体绘制相关方法集中在
  `0x58C6C0–0x58DE60`（`sub_58CFF0`、`sub_58C820`、`sub_58C840`、
  `sub_58CC00`、`sub_58D360`、`sub_58DE60` …）。
  注意 `sub_58C840` 是**文本登记**不是绘制；首个里程碑要先在调试器里
  确认真正的「按字节取字形并 blit」的方法，再下 hook。
- 渲染：逐字节扫描；`0x81–0xFE` 前导 + 合法尾字节 → 取 HZK/图集字形，
  按当前字高缩放后 blit；其余仍走原 GAF 路径。
- 宽度：同步 hook `sub_447450` / `sub_44BE90`，
  让 `GetTextExtentPoint32A` 把双字节汉字按 1 个全角宽计。
- GDI 兜底：hook `sub_448A30`（`CreateFontIndirectA`）强制
  `lfCharSet = GB2312_CHARSET` + 中文字体名，
  使 `TextOutA` 分支在 CP936 系统上也能显示。
- 字形数据：CI 由 TTF 或 `chn12.dot` 生成 `chs.glyph`，随 release 外置。

### 6.6 数据层与 CI 接口

`text/*.jsonl`（UTF-8 明文）依旧是唯一真相源，CI 扩展为：

```text
text/*.jsonl
   ├─ build_out/translate/*.tdf        # Chinese = ...; 槽，引擎原生路径
   ├─ build_out/chs.dict               # 运行时 hook 词典（GBK 编码，双向索引）
   ├─ build_out/chs.glyph              # 字形图集
   ├─ build_out/icd_patch.json         # -> 生成调用点白名单表
   └─ build_out/patches.json / summary.json（沿用 docs/01 契约）
```

新增一个独立校验 `verify_runtime_assets.py`：词典键去重、
占位符完整性、GBK 可编码性（不可编码字符要报错而不是 `?`）、
首字节 ASCII 例外表（`MESG_BOX_*`）、调用点白名单与 221 个 xref 对齐。

---

## 7. 与 7 组待译表的对应关系

| 组 | 条数 | 走哪条路径 | 备注 |
|---|---|---|---|
| `01_ui_tdf` | 1476 | `Chinese` 槽（原生）+ 运行时字典 | 键取 `targets[].key`，不是 `en` |
| `02_units` | 699 | 运行时字典 + 数据文件 patch | 需确认字段值是否经过 `sub_544020` |
| `03_missions` | 298 | 运行时字典 + `<Language><field>` 前缀通道 | `sub_544160` |
| `04_gui` | 156 | 运行时字典 / `.gui` 文本补丁 | 注意长度字段 N 同步 |
| `05_web` | 1028 | **静态替换 HTML 文本资源** | 内嵌网页不经引擎文本管线，hook 不到 |
| `06_icd` | 986 | 运行时字典（字面量键） | 需逐个核对是否真被 `sub_544020` 引用 |
| `07_icd_candidates` | 3766 | 默认 `review`，不送翻 | 候选池，避免误译 |

> ⚠️ **数据质量待修**：`06_icd` 里有相当一部分是 `MESG_BOX_OK`、
> `BYPAGE_REQ_SENT` 这类**短语 ID**，不是显示英文。
> 运行时字典按 ID 作键是对的，但译文应对应 `English` 槽的值；
> 接入 LLM 前应把 06/07 与 `translate/*.tdf` 的块名做一次交叉去重与标注。

---

## 8. 联机保护清单

1. 磁盘 `KINGDOMS.icd` 与全部原 HPI 哈希不变；不新增 HPI；不改 `.fbi`/`.ota` 数值。
2. 玩家名 / 聊天 / 房间名 / IP 串：
   - **调用点白名单**：只在 IDA 导出的 221 个 UI 调用点返回地址范围内翻译；
     网络/聊天模块的地址一律直接放行。
   - 尊重引擎自带的 `a3 & 0x8000` 免翻标记。
   - 字典只做「英文字节串精确匹配」，中文输入天然不会命中。
3. `sub_544110` 反查保证协议/存档里仍是英文标识。
4. 不动 `sub_534260(4, 6, 2, 436)` 与 `sub_5AC3C0(2, 3, "0.93")` 这类版本校验。
5. **验收项**：两台机器进同一房间、中文房间名、中文聊天、
   中文单位名正常显示、无版本不匹配提示、存档可互相读取。

---

## 9. 方案对比

| 方案 | 覆盖 | 改动 | 联机风险 | 评价 |
|---|---|---|---|---|
| A 纯 TDF 语言槽（0 hook） | 仅 01 组 | 最小 | 低 | 字库仍显示不了中文，**单独不可用** |
| B 新增 HPI 覆盖层 | 01–05 | 需 HPI v2 writer | 中（优先级未验证、数据被多加载） | 备选 |
| C 纯运行时 hook（旧外挂思路） | 全覆盖 | 要重写渲染 | 低 | 地址脆、工作量大 |
| **D 混合（推荐）** | 全覆盖 | `Chinese` 槽管 01 + 正/反查 hook 管其余 + 字形 hook 管显示 + 少量静态资源 patch 管 05 | 低 | **磁盘游戏文件零改动** |

---

## 10. 风险与验证清单

- [ ] HPI / 磁盘同名文件的覆盖优先级（`sub_539DC0` 合并条件）需实机确认。
- [ ] 12 个字库槽分别被哪些 UI 使用、是否所有界面都走同一绘制方法。
- [ ] 06/07 组哪些 ICD 串真的被 `sub_544020` 的调用点引用（排除 CRT/第三方库报错串）。
- [ ] 05 组 HTML：需要保留标签结构的段落替换器，不能整串正则乱替。
- [ ] 系统 ACP ≠ 936 时的中文显示兜底（不能只依赖 `TextOutA`）。
- [ ] 译文变长后的按钮/对话框溢出（`.gui` 的长度字段 N 需同步）。
- [ ] 快捷键词条首字节例外表完整性（至少 4 个 `MESG_BOX_*`）。
- [ ] 真实联机进房、中文房间名、中文聊天、存档互通。

---

## 11. 实施顺序（建议）

1. **最小 DLL**：只 hook `sub_544020`，塞 3 条手工 GBK 词条。
   先在调试器确认 hook 命中（此时即使乱码也能证明链路通）。
2. 加 `Language=Chinese` 与 `translate/*.tdf` 的 `Chinese` 槽，验证引擎原生路径。
3. 加字形 hook，验证中文显示 + 行宽/换行。
4. 加 `sub_544110` 反查 + 调用点白名单，做联机隔离。
5. 接 GitHub Action：从 `text/*.jsonl` 生成 `chs.dict` / `chs.glyph` / 白名单表，
   编译 DLL，组装 release，跑独立校验。
6. 实机联机验收（§8 第 5 项）。

前 4 步不依赖翻译 LLM，可以先做；第 5 步等用户提供可靠翻译 API 后接入，
按 `text/README.md` 的契约只回填 `zh` 与 `status`。

---

## 附录 A · 41BB 关键地址表

| 地址 | 作用 | 用途 |
|---|---|---|
| `0x532F10` | `WinMain`，读 `Language` | 语言 hook 时机 |
| `0x4FA030` / `0x4FA740` / `0x4FA2E0` | 注册表配置读写 | 强制 `Chinese` |
| `0x48CD10` / `0x53CD80` | 枚举并注册 HPI/UFO | 覆盖层可行性 |
| `0x539DC0` / `0x53AAA0` / `0x53A2F0` | VFS 目录枚举 | TDF 来源 |
| `0x543CD0` | 翻译表总控，语言名 → `0x655328` | 初始化 |
| `0x543E40` | TDF 逐块解析、按语言名取值 | 语言槽机制 |
| `0x543110` / `0x5432C0` | `_strcmpi` map 查找 / 取值封装 | 无语言白名单的证据 |
| **`0x544020`** | **中央翻译（221 xref）** | **正向 hook** |
| **`0x544110`** | **译文→英文键反查** | **反向 hook** |
| `0x544160` | `<Language><field>` 前缀回退 | 字段级本地化 |
| `0x58C840` | TextHandler 登记，`0x8000` 免翻位 | 动态串隔离 |
| `0x58CAB0` / `0x58CD50` / `0x58C7C0` | 文本取回 / 析构反查 | 反查 hook 依据 |
| `0x58B480` | `FontHandler` 构造：12 槽 × 256 字形 | 字库上限证据 |
| `0x5F57C8` | `??_7FontHandler@@6B@` vtable | 绘制 hook 定位 |
| `0x58B5D0` / `0x56FFC0` / `0x58C460` / `0x58D360` | `Fonts\`、GAF 载入、槽选择 | 字库链路 |
| `0x448A30` / `0x448910` | `CreateFontIndirectA` 缓存 | GDI 兜底 |
| `0x447530` / `0x447450` / `0x44BE90` / `0x44BF20` | `TextOutA` / 宽度 / 度量 | 宽度修正 |
| `0x49A030` | `Language == "German"` 特判 | 不能占 German 槽 |
| `0x4C7740` | `Language != "english"` 分支 | 设 Chinese 的理由 |
| `0x655328` / `0x655324` / `0x655368` | 语言名缓冲 / 全局翻译 map / 默认空串 | 运行时状态 |

## 附录 B · Hook 签名字节

```text
sub_544020  55 8B EC 83 EC 08 53 8B 5D 08 56 57 85 DB 75 0B
            5F 5E 33 C0 5B 8B E5 5D C2 04 00 A1 ?? ?? ?? ??
sub_544110  55 8B EC 56 57 8B 7D 08 85 FF 74 31 A1 ?? ?? ?? ??
            85 C0 74 28 8B 70 05 8B 48 09 3B F1 73 1E 8B
sub_58C840  55 8B EC 83 EC 08 8B 55 08 53 8B D9 56 85 D2
            57 89 5D FC 0F 84 ?? ?? ?? ?? 8B FA 83 C9 FF 33 C0
sub_543CD0  55 8B EC 83 EC 10 53 56 57 8B 7D 10 33 DB 3B FB
```

`??` 是绝对地址/相对偏移通配位（重定位点），匹配时跳过。

## 附录 C · 版本与工具边界

- 原目录 `KINGDOMS.icd` 是 30BB，本文地址全部不适用。
- 旧外挂 `0x56A810` 仅适配 v1.0；41BB 上不是函数入口。
- 41BB 的 16 个 HPI 全是 **v2**（`0x00020000`）；
  `TAUtil/TAUtil/Hpi/HpiArchive.cs` 只认 v1，所以本项目自研了 `tools/hpi_v2.py`。
- `HPIUtil.dll` 是 x86 C++/CLI，本机 Python 全 64 位，无法直接加载；
  纯 Python 复刻是刻意为之。
- 全程只用 IDA Pro（含 Hex-Rays 与 IDA MCP），未使用 Ghidra 系工具。

## 附录 D · 本文仍未解决的问题

见 §10。其中「真正的 GAF 字形绘制方法」和「HPI 覆盖优先级」
需要在下一阶段用调试器/实机确认，本文只给出定位范围，不做无证据断言。
