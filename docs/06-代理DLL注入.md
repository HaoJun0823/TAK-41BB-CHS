# 06 · 代理 DLL 注入（`version.dll`）

> 目标：把我们的代码送进 `KINGDOMS.icd` 的进程，而**磁盘上的游戏文件一个字节都不改**。
> 结论：在游戏目录放一个同名 `version.dll`，转发系统原件的 17 个导出，顺手打补丁。
> 状态：实机验证通过（主菜单中文 + `v4.1BB` 版本串 + 无光盘提示）。

## 1. 为什么不改 exe、不改 binkw32

老方案是把 `binkw32.dll` 改名成 `binkw32_orig.dll`，再放我们的代理顶替。
能用，但有两个缺点：

* 动了游戏自带文件的名字（虽然内容没丢），用户看着像被"替换"了；
* Bink 是播片库，万一某天要换 `binkw32` 版本，两件事会缠在一起。

新方案挑一个**游戏静态导入、但属于系统 DLL** 的模块做宿主：我们放的同名文件
只存在于游戏目录，删掉即还原，游戏目录里没有任何文件被改名。

## 2. 宿主选型（实测数据，不是猜的）

`KINGDOMS.icd` 的静态导入共 13 个：

```
WINMM.dll 4   KERNEL32.dll 149  USER32.dll 70   GDI32.dll 22
WSOCK32.dll 2 ADVAPI32.dll 11   VERSION.dll 3   binkw32.dll 10
DDRAW.dll 2   DPLAYX.dll 3      SHELL32.dll 1   mss32.dll 25
ole32.dll 3
```

| 候选 | 能不能用 | 原因 |
|---|---|---|
| `dinput.dll` | ✗ | 游戏根本不导入（DirectPlay / WinMM 时代，没有 DirectInput） |
| `ddraw.dll` | 理论上可以 | 只有 2 个导入、导出也少；但它处在渲染热路径，且玩家常装 dgVoodoo 之类包装器，天生冲突 |
| `winmm.dll` | 勉强 | 192 个导出，且 `mss32.dll` 自己还要用其中 28 个，转发面太大 |
| `binkw32.dll` | 旧方案 | 见 §1 |
| **`version.dll`** | **✓ 选它** | 游戏只用 3 个函数；系统 DLL 共 **17 个导出、全部有名字、无纯序号导出**；只在启动期被调用几次，不在热路径 |

两条容易踩的前提，都实测过：

1. **它不在 KnownDLLs。** 本机
   `HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\KnownDLLs`
   里没有 `version`。若在，加载器会直接映射系统那份，放同名文件完全无效
   （表现同样是"什么都没发生"）。
2. **应用目录优先于系统目录。** 这是 exe 静态导入的默认搜索顺序；用
   `_probe/vtest.exe` 验证过（见 §6）。

## 3. 导出表：17 个裸跳板

`version.def` 按系统 DLL 的导出顺序写死序号 1..17，`src/proxy_version.cpp`
用 X-macro 生成同序的名字常量与跳板。每个导出是 14 字节：

```
68 xx xx xx xx   push offset chs_name_XXX
E8 xx xx xx xx   call ChsProxyResolve
83 C4 04         add  esp, 4
FF E0            jmp  eax
```

`ChsProxyResolve(name)` 第一次被调用时按
`GetSystemDirectoryW()` + `\version.dll` **全路径**加载原件，取
`GetProcAddress`，之后命中指针缓存（跳板传的永远是自己的名字常量，命中率
100%）。所以运行期每次转发只多四条指令。

**为什么不用 PE 的导出转发**（`VerQueryValueA=version_orig.VerQueryValueA`）：
转发记录只能写"模块名.函数名"，加载器按名字找模块时会**又找到我们自己**
（应用目录优先），转发就成了自我递归。`binkw32` 方案能用转发是因为原件被
改名成了 `binkw32_orig.dll`——这正是新方案要去掉的改动。

原生加载失败时不静默：弹一个说明路径与错误码的框，然后 `ExitProcess`，
避免跳板 `jmp 0` 留下毫无线索的崩溃。

## 4. 进程守卫：同目录的 exe 都会加载我们

`ChooseRenderer.exe`、`Cartographer.exe`、`unins000.exe` 都在同一目录，它们
启动时也会加载我们这份 `version.dll`（只要它们导入 version）。我们的地址表
是 41BB 的绝对地址，硬套到别的模块上会把别人的代码改坏。所以 `DllMain` 里
先过四道判据（`src/target.cpp`）：

1. 主模块文件名是 `KINGDOMS.icd` / `ironplague.icd`（大小写不敏感）；
2. `ImageBase == 0x400000`；
3. `SizeOfImage` 覆盖到最高补丁点 VA `0x58F220`；
4. 能读出合法的 DOS/NT 头。

任一不满足就**只转发、一行补丁不打**，也不建初始化线程。

### 踩过的坑：把 VA 当成映像大小

第一版把第 3 条写成 `SizeOfImage >= 0x590000`（把最高补丁 VA `0x58F220`
直接当成了"映像要多大"）。而 41BB 实际的 `SizeOfImage = 0x271000`
（映像从 `0x400000` 起，覆盖到 VA `0x671000`，够用）。结果：

* 游戏本体被判成"非目标进程" → `DllMain` 直接 `break`；
* 不打补丁、不建线程、**连日志都不写**；
* 现场表现 = "换了宿主 DLL 之后完全没反应"，非常容易误判成"version.dll
  这个宿主不行"。

正确写法是让常量自己算出来：

```cpp
const DWORD kHighestPatchVa = 0x58F220;   // MakeFrame
const DWORD kMinImageSize = (kHighestPatchVa - kExpectedImageBase) + 0x1000;
```

并加了两道防复发措施：

* `TargetNameMatched()`：文件名对得上、结构判定没过时，在游戏目录写
  `tak_chs_reject.txt` 说明原因——"部署完什么都没发生"必须留痕；
* 验证顺序固定为"先证明 DLL 被加载，再证明加载后干了活"（§6），
  不要一上来就怀疑宿主选型。

## 5. 加载顺序与时机

* `DllMain(DLL_PROCESS_ATTACH)`：过守卫 → `CdCheckBypass()`（必须同步，
  见 `docs/05`）→ 建 `InitThread` 立刻返回。
* `InitThread`：`LogInit` → `ProxyPrewarm()`（此时已脱离 loader lock，
  把 LoadLibrary 的代价摊在游戏画第一帧之前）→ 光盘补丁报告 →
  `TranslateInit` → `GdiProbeInit` → `CjkFontInit`。
* `DLL_PROCESS_DETACH`：只在 `lpReserved == nullptr`（FreeLibrary）时还原
  函数头；进程终止时只落统计并关日志。

## 6. 验证手段（按顺序用，别跳步）

1. **`_probe/vtest.exe`** —— 一个只调用 `GetFileVersionInfoSizeA` 的 32 位
   小程序，导入表里就是 `VERSION.dll`。把它和代理一起放进游戏目录运行：

   ```
   LoadLibraryA(version.dll) = 6ACC0000
     -> I:\...\Total Annihilation Kingdoms\VERSION.dll
   VerQueryValueA           = 6ACC4120
   GetFileVersionInfoSizeA(notepad.exe) = 1764 (err=0)
   ```

   路径指向游戏目录 = 代理被优先加载；返回 1764 = 跳板转发到原件成功。
   若路径是 `SysWOW64`，说明宿主不可用，别再往下试。

2. **`_probe/diag_proxy.py`** —— 启动游戏后枚举游戏进程的模块，确认
   `version.dll` 有两份：游戏目录那份（我们）与 `system32` 那份（原件）。
   这一步能把"没加载"和"加载了但没干活"分开。

3. **日志首三行**（`tak_chs.log`）：

   ```
   ==== TAK CHS DLL 启动 ====
   自身: I:\...\VERSION.dll
   target: Kingdoms.ICD 确认（base=0x400000 SizeOfImage=0x271000）
   ```

   第三行不是"确认"就去看 `tak_chs_reject.txt`。

## 7. 部署与撤回

`tools/deploy.py` 默认 `--proxy version`：

```
python tools/deploy.py --game "<41BB目录>" --apply              # 安装
python tools/deploy.py --game "<41BB目录>" --apply --uninstall  # 撤回
```

* 写入清单 `.tak_chs_files.txt` 只记我们放进去的文件；`binkw32.dll`
  **不在**清单里（从 bink 方案切过来时，脚本会先把原版从
  `binkw32_orig.dll` 还原并校验，再删备份）。
* 两种宿主互斥：同时装会在同一进程里跑两份我们的代码，hook 装两遍必炸。
  `plan_install()` 里有这道还原动作，别绕过脚本手工拷。

## 8. 遗留

* `ironplague.icd`（资料片）共用同一套引擎，守卫里放了它的名字，但地址表
  未逐条复核，**暂未验证**。
* 联机（DirectPlay / WSOCK32）尚未实测；代理只碰 `version.dll`，理论上与
  网络层无关。
