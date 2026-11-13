// proxy_version.cpp —— version.dll 的代理导出
//
// 为什么选 version.dll 当代理宿主（对比见 docs/06）：
//   * KINGDOMS.icd 静态导入它（第 7 个），不在 KnownDLLs，应用目录优先；
//   * 只有 17 个导出，且**全部有名字**、没有仅序号导出，转发面最小；
//   * 它的函数只在启动时被调用几次，不在渲染/音频热路径上，转发开销无感。
// 对比 winmm.dll：192 个导出，而且 mss32.dll 自己还要用 28 个，面大得多。
//
// 关键约束：**同目录里任何 exe 都会加载我们**（ChooseRenderer.exe、
// unins000.exe……），所以这里除了转发什么都不做；打不打补丁由 target.cpp
// 判断，DllMain 里负责分叉。
#include "proxy.h"

#include <windows.h>
#include <stdio.h>
#include <string.h>

namespace chs {

// 必须先声明：MSVC 的内联汇编只有在**前面见过声明**时才知道
// ChsProxyResolve 是外部函数，否则会当成同文件里的局部标签，
// 报「标签 ChsProxyResolve 未定义」。声明必须与定义同级（chs），
// 放进匿名名字空间就成了两个不同的实体。
extern "C" FARPROC __cdecl ChsProxyResolve(const char* name);

// ── 要转发的导出清单 ────────────────────────────────────────────────
// 顺序与系统 version.dll 的导出顺序一致（序号 1..17），.def 里的 @序号
// 必须与此对应，改这里要同步改 version.def。
#define CHS_VERSION_EXPORTS(X)     \
    X(GetFileVersionInfoA)         \
    X(GetFileVersionInfoByHandle)  \
    X(GetFileVersionInfoExA)       \
    X(GetFileVersionInfoExW)       \
    X(GetFileVersionInfoSizeA)     \
    X(GetFileVersionInfoSizeExA)   \
    X(GetFileVersionInfoSizeExW)   \
    X(GetFileVersionInfoSizeW)     \
    X(GetFileVersionInfoW)         \
    X(VerFindFileA)                \
    X(VerFindFileW)                \
    X(VerInstallFileA)             \
    X(VerInstallFileW)             \
    X(VerLanguageNameA)            \
    X(VerLanguageNameW)            \
    X(VerQueryValueA)              \
    X(VerQueryValueW)

// 每个导出：一个名字常量 + 一个裸跳板（必须落在 chs 作用域，不能进匿名
// 名字空间 —— 那样会变成内部链接，.def 里的 name=chs_px_xxx 就找不到符号）。
//
// 跳板 push 名字而不是序号：序号得跟着 .def 手工维护，名字是编译期生成
// 的字符串，改清单时不会漏。
//
// 写法约束：必须**每句一个 __asm 块**。MSVC 内联汇编里 `;` 是注释符而非
// 语句分隔符，宏的续行符又会把换行全吃掉，写成 `__asm { a ; b }` 会让后半
// 句连同右括号一起变成注释，报错现场是一堆莫名其妙的括号不匹配。
#define CHS_DEFINE_THUNK(name)                          \
    extern "C" const char chs_name_##name[] = #name;     \
    extern "C" __declspec(naked) void chs_px_##name(void) { \
        __asm { push offset chs_name_##name }            \
        __asm { call ChsProxyResolve }                   \
        __asm { add esp, 4 }                             \
        __asm { jmp eax }                                \
    }
CHS_VERSION_EXPORTS(CHS_DEFINE_THUNK)
#undef CHS_DEFINE_THUNK

namespace {

// 名字表只做两件事：给 ProxyPrewarm 遍历，顺便保证上面的名字常量
// 不会被链接器当垃圾回收（asm 里的引用 /OPT:REF 不一定看得见）。
#define CHS_NAME_REF(name) chs_name_##name,
const char* const kExportNames[] = {CHS_VERSION_EXPORTS(CHS_NAME_REF)};
#undef CHS_NAME_REF

const int kExportCount = sizeof(kExportNames) / sizeof(kExportNames[0]);

// ── 原版 DLL 的加载与解析 ──────────────────────────────────────────
HMODULE g_real = nullptr;
bool g_tried = false;
wchar_t g_path[MAX_PATH] = {0};

// 小缓存：version 的几个函数在扫目录时会被连着调用几十次，
// 每次 GetProcAddress 都要遍历导出表，缓存下来更稳。
struct CacheEntry {
    const char* name;
    FARPROC fn;
};
CacheEntry g_cache[32];
int g_cache_count = 0;

HMODULE LoadReal() {
    if (g_tried) return g_real;
    g_tried = true;

    // 必须按**全路径**加载：直接 LoadLibraryW(L"version.dll") 会按搜索顺序
    // 又找到我们自己（应用目录优先），要么递归、要么把转发指回自己。
    // 32 位进程在 64 位系统上，GetSystemDirectoryW 返回的是 SysWOW64。
    wchar_t dir[MAX_PATH] = {0};
    if (GetSystemDirectoryW(dir, MAX_PATH) == 0) return nullptr;

    _snwprintf_s(g_path, MAX_PATH, _TRUNCATE, L"%s\\version.dll", dir);
    g_real = LoadLibraryW(g_path);
    if (!g_real) {
        g_path[0] = L'\0';
    }
    return g_real;
}

// 解析失败是致命的：继续往下走就是 jmp 到 0，崩溃现场毫无线索。
// 这里把原因说清楚再退出，比留个空指针好得多。
void FatalNoReal() {
    wchar_t msg[512] = {0};
    _snwprintf_s(msg, 512, _TRUNCATE,
                 L"TAK 汉化：无法加载系统的 version.dll。\n\n"
                 L"尝试路径：%s\n错误码：%lu\n\n"
                 L"请确认该文件存在于系统目录，或改用其他代理目标。",
                 g_path[0] ? g_path : L"(未取到系统目录)", GetLastError());
    MessageBoxW(nullptr, msg, L"TAK 汉化", MB_ICONERROR | MB_OK);
    ExitProcess(0xDEAD);
}

}  // namespace

// 被裸跳板 call。cdecl，参数是导出名；返回值是原版函数的真实地址。
extern "C" FARPROC __cdecl ChsProxyResolve(const char* name) {
    // 先按指针比：跳板传进来的是它自己的名字常量，命中率几乎 100%
    for (int i = 0; i < g_cache_count; ++i) {
        if (g_cache[i].name == name) return g_cache[i].fn;
    }

    HMODULE real = LoadReal();
    if (!real) FatalNoReal();

    FARPROC fn = GetProcAddress(real, name);
    if (!fn) FatalNoReal();   // 名字对不上说明清单与系统 DLL 版本不匹配

    if (g_cache_count < static_cast<int>(sizeof(g_cache) / sizeof(g_cache[0]))) {
        g_cache[g_cache_count].name = name;
        g_cache[g_cache_count].fn = fn;
        ++g_cache_count;
    }
    return fn;
}

void ProxyPrewarm() {
    for (int i = 0; i < kExportCount; ++i) {
        ChsProxyResolve(kExportNames[i]);
    }
}

bool ProxyReady() { return g_real != nullptr; }

const wchar_t* ProxyRealPath() { return g_path; }

}  // namespace chs
