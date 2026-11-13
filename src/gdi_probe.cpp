// gdi_probe.cpp —— GDI 文本路径探针（IAT hook）
//
// 为什么用 IAT hook 而不是 inline hook：
//   * 只改主模块导入表里的一个指针，不动 GDI32 的代码段，别的进程/DLL 不受影响；
//   * 不需要操心 GDI32 各版本的函数头字节（copy_len 猜错就是崩溃）；
//   * 游戏是 1999 年的 PE，没有 ASLR，IAT 地址固定。
//
// 代价：只能拦到「主模块自己的调用」。若将来发现别的 DLL 也画字，得另做。
#include "gdi_probe.h"

#include <windows.h>

#include "log.h"

namespace chs {

namespace {

typedef HFONT(WINAPI* FnCreateFontIndirectA)(const LOGFONTA*);
typedef BOOL(WINAPI* FnTextOutA)(HDC, int, int, LPCSTR, int);
typedef BOOL(WINAPI* FnGetTextExtentPoint32A)(HDC, LPCSTR, int, LPSIZE);

FnCreateFontIndirectA g_orig_cf = nullptr;
FnTextOutA g_orig_to = nullptr;
FnGetTextExtentPoint32A g_orig_gt = nullptr;

volatile LONG g_font_calls = 0;
volatile LONG g_text_calls = 0;
volatile LONG g_extent_calls = 0;
volatile LONG g_text_logged = 0;

bool g_force_chinese = false;
CRITICAL_SECTION g_cs;
bool g_cs_ready = false;

// 日志限速：主循环每帧都会调用，全记会把日志撑爆
const LONG kLogLimit = 80;

HFONT WINAPI DetourCreateFontIndirectA(const LOGFONTA* plf) {
    if (!plf) return g_orig_cf ? g_orig_cf(plf) : nullptr;
    LONG n = InterlockedIncrement(&g_font_calls);

    LOGFONTA lf = *plf;
    if (n <= 16) {
        char face[LF_FACESIZE + 1] = {0};
        memcpy(face, lf.lfFaceName, LF_FACESIZE);
        CHS_LOG("gdi: CreateFontIndirectA #%ld face='%s' h=%d w=%d charset=%u "
                "quality=%u weight=%d italic=%u",
                n, face, lf.lfHeight, lf.lfWidth, lf.lfCharSet,
                lf.lfQuality, lf.lfWeight, lf.lfItalic);
    }

    if (g_force_chinese) {
        // 只改「用哪种字」的两项，字号/粗细/斜体/下划线全部保留，
        // 这样 UI 的排版指标不会整体跑偏，只有字形来源变了。
        lf.lfCharSet = GB2312_CHARSET;
        memset(lf.lfFaceName, 0, LF_FACESIZE);
        // 优先 SimSun（宋体）：GBK 全覆盖、点阵清晰，Win 全版本都有
        const char* kFace = "SimSun";
        memcpy(lf.lfFaceName, kFace, strlen(kFace));
        return g_orig_cf(&lf);
    }
    return g_orig_cf(plf);
}

BOOL WINAPI DetourTextOutA(HDC hdc, int x, int y, LPCSTR s, int cch) {
    LONG n = InterlockedIncrement(&g_text_calls);
    if (s && n <= kLogLimit) {
        if (g_cs_ready) EnterCriticalSection(&g_cs);
        // 只记前 60 字符，避免一条长散文刷屏
        int show = (cch >= 0 && cch < 60) ? cch : 60;
        CHS_LOG("gdi: TextOutA #%ld hdc=%p (%d,%d) len=%d '%.*s'",
                n, hdc, x, y, cch, show, s);
        if (g_cs_ready) LeaveCriticalSection(&g_cs);
        InterlockedIncrement(&g_text_logged);
    }
    return g_orig_to ? g_orig_to(hdc, x, y, s, cch) : FALSE;
}

BOOL WINAPI DetourGetTextExtentPoint32A(HDC hdc, LPCSTR s, int cch, LPSIZE ps) {
    InterlockedIncrement(&g_extent_calls);
    return g_orig_gt ? g_orig_gt(hdc, s, cch, ps) : FALSE;
}

// 在模块的导入表里找 slot，返回它所在的 IAT 项地址
void** FindIatSlot(BYTE* base, const char* dllname, const char* funcname) {
    IMAGE_DOS_HEADER* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) return nullptr;
    IMAGE_NT_HEADERS* nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) return nullptr;

    const IMAGE_DATA_DIRECTORY& dir =
        nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir.VirtualAddress) return nullptr;

    IMAGE_IMPORT_DESCRIPTOR* imp =
        reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base + dir.VirtualAddress);

    for (; imp->Name; ++imp) {
        const char* mod = reinterpret_cast<const char*>(base + imp->Name);
        if (_stricmp(mod, dllname) != 0) continue;

        IMAGE_THUNK_DATA* orig =
            reinterpret_cast<IMAGE_THUNK_DATA*>(base + imp->OriginalFirstThunk);
        IMAGE_THUNK_DATA* iat =
            reinterpret_cast<IMAGE_THUNK_DATA*>(base + imp->FirstThunk);
        if (!orig) continue;

        for (; orig->u1.AddressOfData; ++orig, ++iat) {
            if (orig->u1.Ordinal & IMAGE_ORDINAL_FLAG) continue;
            IMAGE_IMPORT_BY_NAME* ibn =
                reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(base + orig->u1.AddressOfData);
            if (strcmp(reinterpret_cast<const char*>(ibn->Name), funcname) == 0) {
                return reinterpret_cast<void**>(iat);
            }
        }
    }
    return nullptr;
}

bool PatchSlot(void** slot, void* replacement, void** old) {
    if (!slot) return false;
    DWORD old_prot = 0;
    if (!VirtualProtect(slot, sizeof(void*), PAGE_READWRITE, &old_prot)) {
        CHS_LOGE("gdi: VirtualProtect 失败 %lu", GetLastError());
        return false;
    }
    if (old) *old = *slot;
    *slot = replacement;
    VirtualProtect(slot, sizeof(void*), old_prot, &old_prot);
    return true;
}

}  // namespace

bool GdiProbeInit(bool force_chinese) {
    InitializeCriticalSection(&g_cs);
    g_cs_ready = true;
    g_force_chinese = force_chinese;

    HMODULE mod = GetModuleHandleW(nullptr);
    if (!mod) return false;
    BYTE* base = reinterpret_cast<BYTE*>(mod);

    // 先确认这是 41BB：IAT 里的地址应该落在 GDI32 的映像范围内
    void** p_cf = FindIatSlot(base, "GDI32.dll", "CreateFontIndirectA");
    void** p_to = FindIatSlot(base, "GDI32.dll", "TextOutA");
    void** p_gt = FindIatSlot(base, "GDI32.dll", "GetTextExtentPoint32A");

    CHS_LOG("gdi: IAT slot cf=%p to=%p gt=%p", p_cf, p_to, p_gt);
    if (!p_cf || !p_to) {
        CHS_LOGE("gdi: 找不到 GDI IAT 项，跳过探针（游戏照常运行）");
        return false;
    }

    int n = 0;
    if (PatchSlot(p_cf, reinterpret_cast<void*>(&DetourCreateFontIndirectA),
                  reinterpret_cast<void**>(&g_orig_cf))) ++n;
    if (PatchSlot(p_to, reinterpret_cast<void*>(&DetourTextOutA),
                  reinterpret_cast<void**>(&g_orig_to))) ++n;
    if (p_gt && PatchSlot(p_gt, reinterpret_cast<void*>(&DetourGetTextExtentPoint32A),
                          reinterpret_cast<void**>(&g_orig_gt))) ++n;

    CHS_LOG("gdi: 已挂 %d 个 IAT 探针（强制中文=%d）", n, force_chinese ? 1 : 0);
    return n >= 2;
}

void GdiProbeReport() {
    CHS_LOG("gdi: 统计 字体 %ld 次 / TextOutA %ld 次 / 量宽 %ld 次",
            g_font_calls, g_text_calls, g_extent_calls);
    if (g_cs_ready) {
        DeleteCriticalSection(&g_cs);
        g_cs_ready = false;
    }
}

}  // namespace chs
