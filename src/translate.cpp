// translate.cpp
#include "translate.h"

#include "dict.h"
#include "hook.h"
#include "log.h"

namespace chs {

namespace {

// 41BB 的地址表（见 docs/02 附录 A）。这些是 RVA，相对 PE 头里的 ImageBase。
const uintptr_t kRvaTranslate = 0x544020;   // const char* __stdcall f(const char*)
const uintptr_t kRvaReverse   = 0x544110;   // const char* __stdcall f(const char*)

// 函数头签名（docs/02 附录 B）。0xCC 是通配，用于跳过重定位点。
const unsigned char kSigTranslate[] = {
    0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08, 0x53, 0x8B,
    0x5D, 0x08, 0x56, 0x57, 0x85, 0xDB, 0x75, 0x0B,
};
const unsigned char kSigReverse[] = {
    0x55, 0x8B, 0xEC, 0x56, 0x57, 0x8B, 0x7D, 0x08,
    0x85, 0xFF, 0x74, 0x31,
};

// 被覆盖的字节数。必须正好落在指令边界上：
//   sub_544020: 55 | 8B EC | 83 EC 08                     → 1+2+3 = 6
//   sub_544110: 55 | 8B EC | 56 | 57 | 8B 7D 08           → 1+2+1+1+3 = 8
// 换成 5 会切在 "83 EC 08" 和 "8B 7D 08" 中间，trampoline 立刻执行非法指令。
const int kCopyTranslate = 6;
const int kCopyReverse = 8;

typedef const char*(__stdcall* FnTranslate)(const char*);

Dict g_dict;
InlineHook g_hook_fwd;
InlineHook g_hook_rev;
FnTranslate g_orig_fwd = nullptr;
FnTranslate g_orig_rev = nullptr;

// 统计用于验证：如果 hit 一直是 0，说明键对不上（多半是拿 en 去查，
// 而引擎传进来的是短语 ID），此时看日志就能立刻定位，不用猜。
volatile LONG g_calls = 0;
volatile LONG g_hits = 0;
volatile LONG g_rev_calls = 0;
volatile LONG g_rev_hits = 0;

// 前 N 条未命中的原文，出问题时能直接看出"哪一类串没被翻译"
const int kMissLog = 40;
volatile LONG g_miss_count = 0;
CRITICAL_SECTION g_miss_cs;
bool g_miss_cs_ready = false;

void LogMiss(const char* s) {
    if (!s || InterlockedIncrement(&g_miss_count) > kMissLog) return;
    if (g_miss_cs_ready) EnterCriticalSection(&g_miss_cs);
    CHS_LOG("miss: %s", s);
    if (g_miss_cs_ready) LeaveCriticalSection(&g_miss_cs);
}

const char* __stdcall DetourForward(const char* s) {
    InterlockedIncrement(&g_calls);
    // 先让引擎自己走一遍：原生 Chinese 语言槽若已命中，结果比我们的字典
    // 更权威（它有 TDF 上下文），不要抢。
    const char* r = g_orig_fwd ? g_orig_fwd(s) : s;
    if (!s || !*s) return r;
    if (r != s) {
        InterlockedIncrement(&g_hits);   // 引擎命中
        return r;
    }
    const char* zh = g_dict.ToChinese(s);
    if (zh) {
        InterlockedIncrement(&g_hits);
        return zh;                        // 指向字典常驻内存，生命周期安全
    }
    LogMiss(s);
    return r;
}

const char* __stdcall DetourReverse(const char* s) {
    InterlockedIncrement(&g_rev_calls);
    const char* r = g_orig_rev ? g_orig_rev(s) : nullptr;
    if (r) {
        InterlockedIncrement(&g_rev_hits);
        return r;
    }
    if (!s || !*s) return r;
    const char* en = g_dict.ToEnglish(s);
    if (en) {
        InterlockedIncrement(&g_rev_hits);
        return en;
    }
    return r;
}

// 取主模块实际加载基址 + PE 里的 ImageBase：两者相减得到重定位偏移。
// 直接用 GetModuleHandle(NULL) 当 0x400000 会在 ASLR 打开时算错。
//
// ★ 必须校验结果落在模块自己的 SizeOfImage 内。宿主进程若不是 41BB
// （冒烟测试、或者版本不对），0x144020 这种偏移会指到未映射内存上，
// 后续读签名就是访问冲突 —— 表现为"启动即闪退"，最难查的那种。
void* RvaToVa(uintptr_t rva) {
    HMODULE mod = GetModuleHandleW(nullptr);
    if (!mod) return nullptr;
    BYTE* base = reinterpret_cast<BYTE*>(mod);
    IMAGE_DOS_HEADER* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return nullptr;
    IMAGE_NT_HEADERS* nt =
        reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return nullptr;

    const uintptr_t image_base = nt->OptionalHeader.ImageBase;
    if (rva < image_base) {
        CHS_LOGE("RvaToVa: RVA 0x%lX 小于 ImageBase 0x%lX",
                 static_cast<unsigned long>(rva),
                 static_cast<unsigned long>(image_base));
        return nullptr;
    }
    const uintptr_t offset = rva - image_base;
    const uintptr_t size = nt->OptionalHeader.SizeOfImage;
    if (offset >= size) {
        CHS_LOGE("RvaToVa: 偏移 0x%lX 超出模块映像大小 0x%lX —— 这不是 41BB",
                 static_cast<unsigned long>(offset),
                 static_cast<unsigned long>(size));
        return nullptr;
    }
    return base + offset;
}

}  // namespace

bool TranslateInit(HMODULE self, const wchar_t* dict_path) {
    InitializeCriticalSection(&g_miss_cs);
    g_miss_cs_ready = true;

    int n = 0;
    if (!g_dict.Load(dict_path, &n)) {
        CHS_LOGE("translate: 字典加载失败，hook 不安装（保持游戏原样）");
        return false;
    }
    CHS_LOG("translate: 字典 %d 条", n);

    void* fwd = RvaToVa(kRvaTranslate);
    void* rev = RvaToVa(kRvaReverse);
    if (!fwd || !rev) {
        CHS_LOGE("translate: 无法解析地址（主模块不是预期 PE？）");
        return false;
    }
    CHS_LOG("translate: target fwd=%p rev=%p", fwd, rev);

    void* tramp_fwd = nullptr;
    if (!g_hook_fwd.Install(fwd, reinterpret_cast<void*>(&DetourForward),
                            kSigTranslate,
                            static_cast<int>(sizeof(kSigTranslate)),
                            kCopyTranslate, &tramp_fwd)) {
        CHS_LOGE("translate: 正向 hook 失败");
        return false;
    }
    g_orig_fwd = reinterpret_cast<FnTranslate>(tramp_fwd);

    void* tramp_rev = nullptr;
    if (!g_hook_rev.Install(rev, reinterpret_cast<void*>(&DetourReverse),
                            kSigReverse,
                            static_cast<int>(sizeof(kSigReverse)),
                            kCopyReverse, &tramp_rev)) {
        CHS_LOGE("translate: 反向 hook 失败，回滚正向（避免只挂一半）");
        g_hook_fwd.Remove();
        g_orig_fwd = nullptr;
        return false;
    }
    g_orig_rev = reinterpret_cast<FnTranslate>(tramp_rev);

    CHS_LOG("translate: 两个 hook 已就绪");
    return true;
}

void TranslateShutdown() {
    g_hook_rev.Remove();
    g_hook_fwd.Remove();
    CHS_LOG("translate: 统计 正向 %ld 调 / %ld 命中（含引擎），反向 %ld 调 / %ld 命中",
            g_calls, g_hits, g_rev_calls, g_rev_hits);
    if (g_miss_cs_ready) {
        DeleteCriticalSection(&g_miss_cs);
        g_miss_cs_ready = false;
    }
}

}  // namespace chs
