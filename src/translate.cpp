// translate.cpp
#include "translate.h"

#include <string.h>

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
// 引擎词条表命中、但给的是英文，最后由字典补译的条数
volatile LONG g_engine_en = 0;

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

// ---- 结构化提示串：#Enter#Ok#Esc#Cancel ----
//
// 这类串写在 .gui 里（build_out/update/**/guis/*.gui 第 14 行附近），从来
// 没进过抽取清单，所以运行时整串 miss、界面上露英文。格式是
// `#按键#标签#按键#标签…`，偶数位是按键名（Enter/Esc，不能译），奇数位
// 是动作标签（Ok/Play/Cancel，必须译）。
//
// 不走字典的原因：字典里 "Play" 是「播放」（影视语境），而提示条里应是
// 「开始」；两处语义不同，硬改字典会污染别处。
struct HintWord {
    const char* en;
    const char* zh;
};
const HintWord kHintWords[] = {
    {"ok", "确定"},        {"okay", "确定"},      {"cancel", "取消"},
    {"play", "开始"},      {"previous", "返回"},  {"proceed", "继续"},
    {"mainmenu", "主菜单"}, {"resume", "继续"},    {"join", "加入"},
    {"yes", "是"},         {"no", "否"},          {"savegame", "保存游戏"},
    {"loadgame", "载入游戏"}, {"exit", "退出"},     {"back", "返回"},
    {"next", "下一步"},    {"done", "完成"},      {"close", "关闭"},
    {"start", "开始"},     {"quit", "退出游戏"},   {"restart", "重新开始"},
};

bool EqualsNoCase(const char* a, int n, const char* b) {
    int i = 0;
    for (; i < n; ++i) {
        if (!b[i]) return false;
        char c = a[i];
        if (c >= 'A' && c <= 'Z') c = static_cast<char>(c + 32);
        if (c != b[i]) return false;
    }
    return b[i] == 0;
}

const char* HintWordZh(const char* s, int n) {
    for (int i = 0; i < static_cast<int>(sizeof(kHintWords) / sizeof(kHintWords[0])); ++i) {
        if (EqualsNoCase(s, n, kHintWords[i].en)) return kHintWords[i].zh;
    }
    return nullptr;
}

// 环形缓冲：返回给引擎的串必须活到下一次调用之后（引擎可能整帧持有）。
// 8 槽足够，且这里只在主线程的 UI 绘制路径上调用。
char g_hint_buf[8][192];
volatile LONG g_hint_slot = 0;
volatile LONG g_hint_hits = 0;

const char* TranslateHint(const char* s) {
    if (!s || s[0] != '#') return nullptr;

    const char* tok[10];
    int len[10];
    int ntok = 0;
    const char* p = s + 1;
    while (*p && ntok < 10) {
        const char* q = p;
        while (*q && *q != '#') ++q;
        tok[ntok] = p;
        len[ntok] = static_cast<int>(q - p);
        ++ntok;
        p = *q ? q + 1 : q;
    }
    if (ntok < 2) return nullptr;   // 只有一个 #，多半不是提示串

    char* out = g_hint_buf[InterlockedIncrement(&g_hint_slot) & 7];
    int n = 0;
    bool changed = false;
    for (int i = 0; i < ntok; ++i) {
        if (n + 1 >= static_cast<int>(sizeof(g_hint_buf[0]))) return nullptr;
        out[n++] = '#';
        const char* zh = (i & 1) ? HintWordZh(tok[i], len[i]) : nullptr;
        const char* src = zh ? zh : tok[i];
        const int sl = zh ? static_cast<int>(strlen(zh)) : len[i];
        if (n + sl >= static_cast<int>(sizeof(g_hint_buf[0]))) return nullptr;
        memcpy(out + n, src, sl);
        n += sl;
        if (zh) changed = true;
    }
    out[n] = 0;

    if (changed && InterlockedIncrement(&g_hint_hits) <= 8) {
        CHS_LOG("hint: %s -> %s", s, out);
    }
    return changed ? out : nullptr;
}

const char* __stdcall DetourForward(const char* s) {
    InterlockedIncrement(&g_calls);
    // 先让引擎自己走一遍。引擎的词条表在启动时按注册表
    // HKCU\Software\Cavedog Entertainment\Kingdoms\InterfaceOptions\Language
    // 建好（本机实测是 ENGLISH），所以 TDF 里凡是带 English 字段的 [词条]
    // 它都会命中，并把英文原样返回。
    const char* r = g_orig_fwd ? g_orig_fwd(s) : s;
    if (!s || !*s) return r;
    if (r != s) {
        // 引擎命中 ≠ 已经是译文。Language=ENGLISH 时它回的是英文，直接
        // return 就把字典挡在门外 —— 表现就是"键 ≠ 英文原文"的那批词条
        // 永远露英文：
        //   BYHost a Game       -> "Host a Game"      (boneyards.tdf)
        //   book of darien.tdf  -> "Book of Darien"   (guiexpansion.tdf)
        //   the iron plague.tdf -> "The Iron Plague"  (guiexpansion.tdf)
        // 这几条的 Chinese 槽写在 TDF 覆盖层里也没用：引擎只认当前
        // Language 槽。所以命中后必须再查一次字典，让它兜住这类英文回包。
        const char* zh = g_dict.ToChinese(r);
        if (zh) {
            InterlockedIncrement(&g_engine_en);
            return zh;
        }
        InterlockedIncrement(&g_hits);   // 引擎命中且字典没有：保持原样
        return r;
    }
    // 结构化提示串（#Enter#Ok#Esc#Cancel）先按语法拆开，只译标签位。
    // 逐条列 15 种组合既难维护又漏大小写变体，这里一次解决。
    const char* hint = TranslateHint(s);
    if (hint) {
        InterlockedIncrement(&g_hits);
        return hint;
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
    CHS_LOG("translate: 统计 正向 %ld 调 / %ld 命中（含引擎，其中 %ld 条是引擎回英文后由字典补译），反向 %ld 调 / %ld 命中",
            g_calls, g_hits, g_engine_en, g_rev_calls, g_rev_hits);
    if (g_miss_cs_ready) {
        DeleteCriticalSection(&g_miss_cs);
        g_miss_cs_ready = false;
    }
}

}  // namespace chs
