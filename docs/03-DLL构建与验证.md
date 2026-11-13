# 03 · DLL 构建、部署与验证

本文对应 `dllmain.cpp` + `src/` 的实现，讲怎么编、怎么装、怎么判断它到底有没有工作。
方案依据见 [`02-IDA与DLL方案.md`](02-IDA与DLL方案.md)。

> **先说清楚当前进度**：翻译层（两个 hook）与注入层已完成并可编译；
> **字库层还没做**。所以现在装上去，日志会显示 hook 正常、命中数在涨，
> 但画面上中文大概率是空白或方块 —— 那是 GAF 位图字库没有中文字形，
> 不是 DLL 没生效。验证顺序必须按本文第 4 节来，别拿"看不看得见中文"
> 当作 DLL 是否工作的判据。

---

## 1. 它做了什么

| 层 | 实现 | 状态 |
|---|---|---|
| 注入 | 本 DLL 命名 `binkw32.dll` 顶替原版，10 个 `_Bink*` 用链接器转发到 `binkw32_orig.dll` | 完成 |
| 翻译（正向） | inline hook `0x544020`，引擎未命中时查 `chs.dict` | 完成 |
| 翻译（反向） | inline hook `0x544110`，把中文译回英文键 | 完成 |
| 字典 | 启动时读同目录 `chs.dict`（GBK，`键\t译文\n`），建三张哈希表 | 完成 |
| 语言槽 | 由 `update/translate/*.tdf` 的 `Chinese` 槽承担，DLL 不写注册表 | 见 §6 |
| 字库 | GBK 字形渲染 + 宽度修正 | **未实现** |

**磁盘文件零改动**：不碰 `KINGDOMS.icd`，不碰任何 `.hpi`。卸载＝删掉
`binkw32.dll` 与 `chs.dict`，把 `binkw32_orig.dll` 改回 `binkw32.dll`。

---

## 2. 编译

### 2.1 Visual Studio（推荐）

打开 `TAK_41BB_CHS.sln`，选 **Release | Win32**，生成。
输出 `Release\binkw32.dll`。

工程已配好这些，改回去会出问题：

| 配置 | 值 | 原因 |
|---|---|---|
| 平台 | 仅 Win32 | 游戏是 32 位；x64 配置只会编出装不进去的 DLL |
| 字符集 | MultiByte | 全程按单字节/GBK 处理文本 |
| `/utf-8` | 开 | 源码中文注释是 UTF-8，MSVC 默认按 936 解释会乱码 |
| 运行库 | `/MT` 静态 | 玩家机器不一定有对应 VC 运行库 |
| 输出名 | `binkw32` | 顶替原版做代理 |
| 工具集 | `v141_xp` | 游戏是 XP 时代程序，新版 CRT 会引入不存在的 API |

### 2.2 命令行

```bat
build.bat          :: Release
build.bat debug    :: 带符号、无优化
```

脚本会用 `vswhere` 定位 VS，`-vcvars_ver=14.16` 锁定 v141，
并在最后断言导出表正好 10 个 `_Bink*` 转发。

> 若提示加载 v141 失败：VS Installer → 单个组件 → 勾选
> 「MSVC v141 - VS 2017 C++ x64/x86 生成工具」。

### 2.3 产物体检

```bat
dumpbin /dependents build_dll\binkw32.dll
```

应当**只**列出 `KERNEL32.dll`。出现 `VCRUNTIME140.dll` / `MSVCP140.dll`
说明静态 CRT 没生效，玩家机器上会直接加载失败（表现为双击游戏没反应）。

```bat
dumpbin /exports build_dll\binkw32.dll
```

应当是 10 条 `forwarded to binkw32_orig.*`。

---

## 3. 部署

```
<游戏目录>\
    KINGDOMS.icd            ← 不动
    binkw32.dll             ← 本工程产物（代理）
    binkw32_orig.dll        ← 原版 binkw32.dll 改名而来
    chs.dict                ← CI 产物 build_out\chs.dict
    update\                 ← 可选：TDF 覆盖层（见 §6）
```

步骤：

1. 备份原版 `binkw32.dll`，改名为 `binkw32_orig.dll`。
2. 把编译出的 `binkw32.dll` 放进去。
3. 把 `build_out\chs.dict` 放进去。
4. 启动游戏。

首次启动会在游戏目录生成 `tak_chs.log`。

---

## 4. 验证顺序（按这个顺序看，别跳）

### 第 1 步：DLL 有没有被加载

看 `tak_chs.log` 有没有这两行：

```
==== TAK CHS DLL 启动 ====
自身: <游戏目录>\binkw32.dll
```

没有 → 代理没生效，检查是不是忘了把原版改名成 `binkw32_orig.dll`，
或游戏根本没导入 binkw32（`dumpbin /imports KINGDOMS.icd | findstr bink` 确认）。

### 第 2 步：签名匹配没有

```
translate: 字典 4195 条
translate: target fwd=0x00544020 rev=0x00544110
hook: 已挂 0x00544020（copy_len=6，tramp=...）
hook: 已挂 0x00544110（copy_len=8，tramp=...）
translate: 两个 hook 已就绪
```

任一行变成 `hook: 签名不匹配 @...` → 游戏不是你预期的 41BB 版本，
或者 exe 被别的补丁改过。**这种情况下 DLL 会主动放弃 hook，游戏按原样跑**，
不会崩 —— 这是刻意设计：宁可不汉化，也不要随机崩溃。

### 第 3 步：翻译有没有被调用

进主菜单随便点点，然后退出游戏，看日志末尾：

```
translate: 统计 正向 1832 调 / 412 命中（含引擎），反向 37 调 / 37 命中
```

- `正向 0 调` → hook 挂上了但没人调用，说明地址不对或这版游戏不经过该函数。
- `命中 0` → 函数在被调用，但字典键对不上。看紧随其后的 `miss:` 行，
  那是引擎实际传进来的原始串，拿它去 `chs.dict` 里搜一下就知道为什么没中。
- `miss:` 最多记 40 条，防止刷爆日志。

### 第 4 步：这时候才看画面

到这一步 hook 已经确认工作。画面上中文显示不出来是**字库层**的事，
属于下一阶段（§7）。当前阶段的成功判据就是第 3 步的命中数 > 0。

---

## 5. 已知行为与陷阱

| 现象 | 原因 | 处置 |
|---|---|---|
| 中文位置显示空白/方块 | GAF 位图字库只有 256 个单字节字形，没有汉字 | 字库层未实现，属预期 |
| 某几个按钮显示成乱码 | 该词条被 `sub_432290` 取首字节当热键（`MESG_BOX_*`） | 这 4 条应保持 `skip`（见 docs/02 §6.2 第 4 条） |
| 联机房间名/聊天变中文 | 动态串本应带 `0x8000` 免翻位 | 若出现，说明该路径没带标记，需要在 DLL 里加白名单 |
| 存档读不出来 | 反查层没起作用，存档里写了中文 | 确认 `0x544110` hook 装上了（第 2 步日志） |
| 日志里 `miss:` 出现大量内部 ID | 引擎传的是短语 ID 而非英文 | 正常，`chs.dict` 已收录 01 组的无歧义短语 ID |

---

## 6. 语言槽（`Chinese`）与 DLL 的分工

两条路可选，建议先试 A：

**A. 只用 TDF 覆盖层（不装 DLL 也能验证一部分）**

把 `build_out\translate\**` 按目录结构放进游戏目录的 `update\`，
并把语言设成 `Chinese`（注册表
`HKCU\Software\Cavedog Entertainment\Kingdoms\InterfaceOptions\Language`）。
引擎按语言名查 `tdf` 的 `Chinese` 槽，命中就直接显示。

这一步能验证「TDF 覆盖层 + 语言槽」是否被引擎接受，与 DLL 无关。

**B. DLL 翻译层**

不依赖语言槽，走 `0x544020`。两者可以同时开：DLL 先让引擎自己查一遍，
命中就用引擎结果（它有 TDF 上下文，更权威），未命中才查 `chs.dict`。

> `docs/00` 已确认 `_strcmpi` 没有语言白名单，`Chinese` 可用；
> `German` 有版本特判不能占用。

---

## 7. 下一阶段：字库层

这是全工程最难的一块，`docs/02` §3、§6.5 给了定位范围，但没有实测结论。
需要先用调试器确认「真正的按字节取字形并 blit」的方法（注意
`sub_58C840` 是文本登记，不是绘制），再下 hook。

在此之前，DLL 的翻译层可以独立验证 —— 这也是本阶段先做翻译层的原因。

---

## 8. 快速排错表

| 日志/现象 | 含义 | 下一步 |
|---|---|---|
| 没有 `tak_chs.log` | DLL 未被加载 | 查 `binkw32_orig.dll` 是否就位 |
| `字典加载失败` | 找不到或读不了 `chs.dict` | 确认与 DLL 同目录、大小约 350KB |
| `dict: 解析出 0 条` | 文件格式不对 | 首行必须是 `# TAK CHS dict v1` |
| `签名不匹配` | 版本不对 | `dumpbin /imports` 看 exe，或换回正确 41BB |
| `trampoline 距目标 ... 超出 rel32 范围` | 分配器给的内存太远 | 罕见；重试启动，或改用近地址分配 |
| 游戏启动即崩 | 代理转发或 DllMain 出错 | 先只放 `binkw32.dll` 不放 `chs.dict`，看是否仍崩 |
