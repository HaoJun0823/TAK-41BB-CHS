// cjk_font.cpp —— 中文字库层实现
//
// 依赖的四个引擎函数（41BB，RVA）：
//   0x58BF40  DrawGlyphRun(fontMgr, x, y, fontIdx, str, count)   ← hook
//   0x58BD70  MeasureText(fontMgr, &w, &h, fontIdx, str)         ← hook
//   0x58F6C0  AllocFrame(gafObj) -> handle                       ← 调用
//   0x58F220  MakeFrame(gafObj, info, palette, rec)              ← 调用
// 以及 gafObj 虚表：vtbl[8](gafObj, handle, x, y, w, h, flags)  ← 调用
#include "cjk_font.h"

#include "hook.h"
#include "log.h"

#include <cstdint>
#include <cstring>

namespace chs {
namespace {

// ── 地址表 ───────────────────────────────────────────────────────────
const uintptr_t kRvaDrawRun = 0x58BF40;
const uintptr_t kRvaMeasure = 0x58BD70;
const uintptr_t kRvaAllocFrame = 0x58F6C0;
const uintptr_t kRvaMakeFrame = 0x58F220;

const unsigned char kSigDrawRun[] = {
    0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x0C, 0x53, 0x56, 0x8B, 0x75, 0x10,
};
const unsigned char kSigMeasure[] = {
    0x55, 0x8B, 0xEC, 0x83, 0xEC, 0x08, 0x57, 0x8B, 0xF9, 0x8B, 0x4D, 0x10,
};
//   DrawGlyphRun: 55 | 8B EC | 83 EC 0C   → 6
//   MeasureText : 55 | 8B EC | 83 EC 08   → 6
const int kCopyRun = 6;
const int kCopyMeasure = 6;

// ── 字体槽布局（5160 字节/槽，共 12 槽） ────────────────────────────
//   +8   行高（绘制时直接加到 y 上）
//   +16  首字符（字形索引 = 字节 - firstChar）
//   +24  字体名指针
//   +28  已加载计数
//   +32  字距表指针
//   +36  起 256 条字形记录，每条 20 字节：w, h, overhang, |originY|, handle
//   +5156/+5160/+5164  该字体的文字颜色（见 sub_58B660 的写入顺序）
const int kSlotSize = 5160;
const int kGlyphBase = 36;
const int kGlyphSize = 20;

typedef int(__thiscall* FnAllocFrame)(void* gaf);
typedef void(__thiscall* FnMakeFrame)(void* gaf, const void* info,
                                      const void* palette, void* rec);
typedef void(__thiscall* FnDrawFrame)(void* gaf, int handle, int x, int y,
                                      int w, int h, int flags);
typedef void(__thiscall* FnDrawRun)(void* self, int x, int y, int fontIdx,
                                    const char* s, int count);
typedef void(__thiscall* FnMeasure)(void* self, int* outW, int* outH,
                                    int fontIdx, const char* s);

InlineHook g_hook_run;
InlineHook g_hook_measure;
FnDrawRun g_orig_run = nullptr;
FnMeasure g_orig_measure = nullptr;

FnAllocFrame g_alloc = nullptr;
FnMakeFrame g_make = nullptr;
FnDrawFrame g_draw = nullptr;

void* g_mgr = nullptr;   // 字库单例（hook 的 ECX）

const int kGlyphCount = 1024;   // 必须是 2 的幂
struct Glyph {
    unsigned int ch;
    unsigned int color;
    int handle;
    short px;
    short adv;
    short cell;
};
Glyph g_cache[kGlyphCount];

volatile LONG g_drawn_cjk = 0;
volatile LONG g_made_glyph = 0;
volatile LONG g_ascii_runs = 0;
volatile LONG g_fail = 0;
volatile LONG g_trace = 0;

CRITICAL_SECTION g_cs;
bool g_cs_ready = false;

// 崩溃定位：把"最后进到哪一步"记下来。引擎内部出错时异常地址会落在
// KINGDOMS.icd 里，减去基址就是 RVA，配合 IDA 一查就知道是谁。
volatile LONG g_phase = 0;   // 0 空闲 1 造帧 2 绘制
const char* const kPhaseName[] = {"idle", "make", "draw"};

LONG WINAPI CrashFilter(EXCEPTION_POINTERS* ep) {
    const DWORD code = ep->ExceptionRecord->ExceptionCode;
    const BYTE* addr = static_cast<const BYTE*>(ep->ExceptionRecord->ExceptionAddress);
    BYTE* base = reinterpret_cast<BYTE*>(GetModuleHandleW(nullptr));
    const long rva = (addr && base) ? static_cast<long>(addr - base) : -1;
    const LONG ph = g_phase;
    CHS_LOG("!! 异常 code=%08lX addr=%p rva=%08lX phase=%s", code, addr, rva,
            kPhaseName[(ph >= 0 && ph < 3) ? ph : 0]);
    return EXCEPTION_CONTINUE_SEARCH;
}

struct GdiFont {
    int px;
    HFONT font;
};
GdiFont g_fonts[8];
int g_font_count = 0;

// ── 工具 ─────────────────────────────────────────────────────────────
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
    if (rva < image_base) return nullptr;
    const uintptr_t off = rva - image_base;
    if (off >= nt->OptionalHeader.SizeOfImage) return nullptr;
    return base + off;
}

inline void* Gaf() {
    return g_mgr ? *reinterpret_cast<void**>(static_cast<BYTE*>(g_mgr) + 0xF1E8)
                 : nullptr;
}

// gafObj 的 vtbl[8]：DrawFrame(handle, x, y, w, h, flags)
inline void EnsureDraw() {
    if (g_draw) return;
    void* gaf = Gaf();
    if (!gaf) return;
    void** vt = *reinterpret_cast<void***>(gaf);
    if (!vt || !vt[8]) return;
    g_draw = reinterpret_cast<FnDrawFrame>(vt[8]);
    CHS_LOG("cjk: DrawFrame = %p", g_draw);
}

inline BYTE* Slot(int fontIdx) {
    return static_cast<BYTE*>(g_mgr) + kSlotSize * fontIdx;
}

int LineHeight(int fontIdx) { return *reinterpret_cast<int*>(Slot(fontIdx) + 8); }
int FirstChar(int fontIdx) {
    return *reinterpret_cast<BYTE*>(Slot(fontIdx) + 16);
}

// sub_58B660 把颜色的三个字节分别存到 +5156/+5160/+5164。
// 它拿到的 a3 是 0x00RRGGBB 还是 0x00BBGGRR 只有实机能看到，
// 所以这里读出来按 R,G,B 用，并把出现过的颜色都打进日志备查。
unsigned int g_seen_color[8] = {0};
volatile LONG g_seen_count = 0;

unsigned int TextColor(int fontIdx) {
    BYTE* s = Slot(fontIdx);
    unsigned int r = *reinterpret_cast<BYTE*>(s + 5156);
    unsigned int g = *reinterpret_cast<BYTE*>(s + 5160);
    unsigned int b = *reinterpret_cast<BYTE*>(s + 5164);
    const unsigned int rgb = (r << 16) | (g << 8) | b;
    bool known = false;
    const LONG n = g_seen_count;
    for (LONG i = 0; i < n && i < 8; ++i)
        if (g_seen_color[i] == rgb) known = true;
    if (!known && n < 8 && InterlockedIncrement(&g_seen_count) <= 8) {
        g_seen_color[n] = rgb;
        CHS_LOG("cjk: font%d 颜色 %06lX（原始字节 %u/%u/%u）", fontIdx,
                static_cast<unsigned long>(rgb), r, g, b);
    }
    return rgb;
}

// 拉丁字形的前进量：字形记录里 w + overhang（overhang 可以为负）
int AsciiAdvance(int fontIdx, unsigned char c) {
    int idx = static_cast<int>(c) - FirstChar(fontIdx);
    if (idx < 0 || idx > 255) return 0;
    int* g = reinterpret_cast<int*>(Slot(fontIdx) + kGlyphBase +
                                    kGlyphSize * idx);
    return g[0] + g[2];
}

// ── 布局补偿 ─────────────────────────────────────────────────────────
// sub_58C230（DrawText）在换行和居中时按"字节"累加宽度：
//     v16 += glyph[byte - firstChar].w + .overhang
// 双字节汉字被拆成两个字节各自查表，宽度就全错了（表里那些格子是空的）。
// 把 0x81..0xFE 对应的空记录填成 px/2，一个汉字两个字节正好累加成 px，
// 引擎自己的换行、居中、裁剪就都跟着对了。
// 这些格子原本装的是 Latin-1 字形，w 是英文半角的宽度；汉字走的是我们的
// hook（原函数不会拿这些 handle 去画），所以覆盖 w/overhang 是安全的。
void PatchGlyphTable(int fontIdx, int half) {
    BYTE* slot = Slot(fontIdx);
    const int first = FirstChar(fontIdx);
    int patched = 0;
    for (int b = 0x81; b <= 0xFE; ++b) {
        const int idx = b - first;
        if (idx < 0 || idx > 255) continue;
        int* rec = reinterpret_cast<int*>(slot + kGlyphBase + kGlyphSize * idx);
        rec[0] = half;    // w：一个字节占半个汉字
        rec[2] = 0;       // overhang
        ++patched;
    }
    CHS_LOG("cjk: font%d 字距表补齐 %d 格（half=%d）", fontIdx, patched, half);
}

// 字号可能变（同一槽位换字体大小），用 0x81 那格当探针判断要不要重补。
inline void EnsureLayout(int fontIdx) {
    if (fontIdx < 0 || fontIdx >= 12 || !g_mgr) return;
    const int half = (LineHeight(fontIdx) + 2) / 2;
    if (half <= 0) return;
    if (*reinterpret_cast<int*>(Slot(fontIdx) + 28) == 0) return;   // 还没加载
    const int probe = 0x81 - FirstChar(fontIdx);
    if (probe >= 0 && probe <= 255) {
        int* rec = reinterpret_cast<int*>(Slot(fontIdx) + kGlyphBase +
                                          kGlyphSize * probe);
        if (rec[0] == half && rec[2] == 0) return;   // 已经补过
    }
    PatchGlyphTable(fontIdx, half);
}

HFONT GetGdiFont(int px) {
    for (int i = 0; i < g_font_count; ++i)
        if (g_fonts[i].px == px) return g_fonts[i].font;
    if (g_font_count >= static_cast<int>(sizeof(g_fonts) / sizeof(g_fonts[0])))
        return g_fonts[0].font;
    // DEFAULT_QUALITY：宋体在 12/14/16px 有内嵌点阵，比矢量缩放干净
    HFONT f = CreateFontW(-px, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                          GB2312_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
                          DEFAULT_QUALITY, DEFAULT_PITCH, L"SimSun");
    if (!f)
        f = CreateFontW(-px, 0, 0, 0, FW_NORMAL, FALSE, FALSE, FALSE,
                        DEFAULT_CHARSET, OUT_TT_PRECIS, CLIP_DEFAULT_PRECIS,
                        DEFAULT_QUALITY, DEFAULT_PITCH, L"Microsoft YaHei");
    if (!f) return nullptr;
    g_fonts[g_font_count].px = px;
    g_fonts[g_font_count].font = f;
    ++g_font_count;
    return f;
}

// 把汉字画进 8 位索引位图，再交给引擎做成纹理帧。返回帧句柄，失败 -1。
int BuildGlyph(unsigned short ch, int px, unsigned int rgb, int* outAdv,
               int* outCell) {
    void* gaf = Gaf();
    if (!gaf || !g_alloc || !g_make) return -1;

    const int cell = px + 2;   // 四周留 1px 透明边，避免采样越界
    *outCell = cell;
    *outAdv = px;              // 中文按方框步进

    BITMAPINFO bi;
    memset(&bi, 0, sizeof(bi));
    bi.bmiHeader.biSize = sizeof(BITMAPINFOHEADER);
    bi.bmiHeader.biWidth = cell;
    bi.bmiHeader.biHeight = -cell;   // 负数 = 自上而下
    bi.bmiHeader.biPlanes = 1;
    bi.bmiHeader.biBitCount = 32;
    bi.bmiHeader.biCompression = BI_RGB;

    HDC dc = CreateCompatibleDC(nullptr);
    if (!dc) return -1;
    void* bits = nullptr;
    HBITMAP bmp = CreateDIBSection(dc, &bi, DIB_RGB_COLORS, &bits, nullptr, 0);
    HFONT font = GetGdiFont(px);
    if (!bmp || !bits || !font) {
        if (bmp) DeleteObject(bmp);
        DeleteDC(dc);
        return -1;
    }
    HGDIOBJ old_bmp = SelectObject(dc, bmp);
    RECT all = {0, 0, cell, cell};
    HBRUSH black = CreateSolidBrush(RGB(0, 0, 0));
    FillRect(dc, &all, black);
    DeleteObject(black);

    HGDIOBJ old_font = SelectObject(dc, font);
    SetBkMode(dc, TRANSPARENT);
    SetTextColor(dc, RGB(255, 255, 255));
    wchar_t wc = static_cast<wchar_t>(ch);
    TextOutW(dc, 1, 1, &wc, 1);
    GdiFlush();

    unsigned char* pix = static_cast<unsigned char*>(
        VirtualAlloc(nullptr, cell * cell, MEM_COMMIT | MEM_RESERVE,
                     PAGE_READWRITE));
    if (pix) {
        // 0 = 透明，1 = 文字色。引擎字库本来就是 1 位点阵，
        // 这里也走硬阈值，抗锯齿在 8 位调色板里只会糊成一团。
        const unsigned char* src = static_cast<const unsigned char*>(bits);
        for (int y = 0; y < cell; ++y) {
            for (int x = 0; x < cell; ++x) {
                const unsigned char* p = src + (y * cell + x) * 4;
                int v = (p[0] + p[1] + p[2]) / 3;
                pix[y * cell + x] = (v >= 100) ? 1 : 0;
            }
        }
    }

    SelectObject(dc, old_font);
    SelectObject(dc, old_bmp);
    DeleteObject(bmp);
    DeleteDC(dc);
    if (!pix) return -1;

    int handle = g_alloc(gaf);
    if (handle < 0) {
        VirtualFree(pix, 0, MEM_RELEASE);
        return -1;
    }

    // sub_58F220 的 info 头：u16 w, u16 h, u16 originX, u16 originY,
    // u8 透明色号, 7 字节填充, 像素指针（+16）
    struct ImgInfo {
        unsigned short w, h, ox, oy;
        unsigned char key;
        unsigned char pad[7];
        void* pixels;
    } info;
    memset(&info, 0, sizeof(info));
    info.w = static_cast<unsigned short>(cell);
    info.h = static_cast<unsigned short>(cell);
    info.key = 0;   // 0 号索引透明（引擎会写成洋红键色）
    info.pixels = pix;

    // 调色板：256 项 × 4 字节，按 [R, G, B, A] 排布
    // （sub_58F220 里的转换式 (B>>3)|(G<<3)|(R<<8) 表明 byte0 是 R）
    unsigned char pal[1024];
    memset(pal, 0, sizeof(pal));
    pal[4] = static_cast<unsigned char>((rgb >> 16) & 0xFF);   // R
    pal[5] = static_cast<unsigned char>((rgb >> 8) & 0xFF);    // G
    pal[6] = static_cast<unsigned char>(rgb & 0xFF);           // B
    pal[7] = 0xFF;

    g_phase = 1;
    g_make(gaf, &info, pal, static_cast<BYTE*>(gaf) + 28 * handle + 4);
    g_phase = 0;
    VirtualFree(pix, 0, MEM_RELEASE);
    if (InterlockedIncrement(&g_trace) <= 8) {
        CHS_LOG("cjk: make ok handle=%d cell=%d", handle, cell);
    }
    return handle;
}

// 取（必要时生成）一个汉字的帧句柄
int EnsureGlyph(unsigned short ch, int px, unsigned int color, int* outAdv,
                int* outCell) {
    unsigned int base = (static_cast<unsigned int>(ch) * 2654435761u +
                         static_cast<unsigned int>(px) * 40503u) &
                        (kGlyphCount - 1);
    if (g_cs_ready) EnterCriticalSection(&g_cs);
    for (int i = 0; i < 8; ++i) {
        Glyph& g = g_cache[(base + i) & (kGlyphCount - 1)];
        if (g.ch == ch && g.px == px && g.color == color) {
            *outAdv = g.adv;
            *outCell = g.cell;
            int h = g.handle;
            if (g_cs_ready) LeaveCriticalSection(&g_cs);
            return h;
        }
        if (g.ch == 0) break;
    }
    if (g_cs_ready) LeaveCriticalSection(&g_cs);

    int adv = 0, cell = 0;
    int handle = BuildGlyph(ch, px, color, &adv, &cell);
    if (handle < 0) {
        InterlockedIncrement(&g_fail);
        return -1;
    }
    *outAdv = adv;
    *outCell = cell;
    if (g_cs_ready) EnterCriticalSection(&g_cs);
    for (int i = 0; i < 8; ++i) {
        Glyph& g = g_cache[(base + i) & (kGlyphCount - 1)];
        if (g.ch == 0) {
            g.ch = ch;
            g.px = static_cast<short>(px);
            g.color = color;
            g.handle = handle;
            g.adv = static_cast<short>(adv);
            g.cell = static_cast<short>(cell);
            break;
        }
    }
    if (g_cs_ready) LeaveCriticalSection(&g_cs);

    LONG n = InterlockedIncrement(&g_made_glyph);
    if (n <= 24)
        CHS_LOG("cjk: 造字 U+%04X px=%d color=%06lX handle=%d cell=%d", ch, px,
                static_cast<unsigned long>(color), handle, cell);
    return handle;
}

// GBK 双字节 → Unicode；失败返回 0
unsigned short GbkToUnicode(const unsigned char* p) {
    wchar_t wc = 0;
    if (MultiByteToWideChar(936, MB_ERR_INVALID_CHARS,
                            reinterpret_cast<const char*>(p), 2, &wc, 1) == 1) {
        return static_cast<unsigned short>(wc);
    }
    return 0;
}

// ── detour ───────────────────────────────────────────────────────────
// 原函数是 thiscall（ECX=this、其余压栈、被调方 ret n 清栈）。
// MSVC 的 __fastcall 也是"前两个参数走 ECX/EDX、其余压栈、被调方清栈"，
// 多给一个 edx 占位即可 1:1 对上原型的栈布局，不必写汇编。
extern "C" void __fastcall DrawRunDetour(void* self, void* /*edx*/, int x, int y,
                                         int fontIdx, const char* s, int count) {
    if (!g_mgr) g_mgr = self;
    EnsureDraw();
    if (!s || !*s || fontIdx < 0 || fontIdx >= 12) {
        if (g_orig_run) g_orig_run(self, x, y, fontIdx, s, count);
        return;
    }
    EnsureLayout(fontIdx);

    const int lineH = LineHeight(fontIdx);
    const int px = lineH + 2;
    const unsigned int color = TextColor(fontIdx);
    const unsigned char* p = reinterpret_cast<const unsigned char*>(s);
    int cx = x;
    int used = 0;
    bool any = false;

    while (*p && (count <= 0 || used < count)) {
        if (*p >= 0x81 && p[1]) {
            unsigned short ch = GbkToUnicode(p);
            int adv = 0, cell = 0;
            int handle = ch ? EnsureGlyph(ch, px, color, &adv, &cell) : -1;
            if (handle >= 0) {
                LONG t = InterlockedIncrement(&g_trace);
                if (t <= 8) CHS_LOG("cjk: draw #%ld handle=%d at (%d,%d) %dx%d", t,
                                   handle, cx, y + lineH - cell, cell, cell);
                if (g_draw) {
                    g_phase = 2;
                    g_draw(Gaf(), handle, cx, y + lineH - cell, cell, cell, 0);
                    g_phase = 0;
                }
                if (t <= 8) CHS_LOG("cjk: draw #%ld done", t);
                cx += adv;
                p += 2;
                used += 2;
                any = true;
                InterlockedIncrement(&g_drawn_cjk);
                continue;
            }
        }
        // 拉丁字符：攒成一段交给原函数画，自己算这一段的前进量
        int run = 0;
        int advance = 0;
        while (p[run] && p[run] < 0x81 && (count <= 0 || used + run < count)) {
            advance += AsciiAdvance(fontIdx, p[run]);
            ++run;
        }
        if (run == 0) {   // 无法识别的 GBK，跳过首字节避免死循环
            ++p;
            ++used;
            continue;
        }
        if (g_orig_run) g_orig_run(self, cx, y, fontIdx,
                                   reinterpret_cast<const char*>(p), run);
        InterlockedIncrement(&g_ascii_runs);
        cx += advance;
        p += run;
        used += run;
        any = true;
    }
    if (!any && g_orig_run) g_orig_run(self, x, y, fontIdx, s, count);
}

extern "C" void __fastcall MeasureDetour(void* self, void* /*edx*/, int* outW,
                                         int* outH, int fontIdx, const char* s) {
    if (!g_mgr) g_mgr = self;
    if (!s || !outW || !outH || fontIdx < 0 || fontIdx >= 12) {
        if (g_orig_measure) g_orig_measure(self, outW, outH, fontIdx, s);
        return;
    }
    const unsigned char* p = reinterpret_cast<const unsigned char*>(s);
    bool has_cjk = false;
    for (const unsigned char* q = p; *q; ++q) {
        if (*q >= 0x81) {
            has_cjk = true;
            break;
        }
    }
    if (!has_cjk) {
        if (g_orig_measure) g_orig_measure(self, outW, outH, fontIdx, s);
        return;
    }
    // DrawText 会先量宽再做换行，趁这个时机把字距表补齐，同一帧就生效
    EnsureLayout(fontIdx);

    const int lineH = LineHeight(fontIdx);
    const int px = lineH + 2;
    // 按 '\n' 分行取最大行宽，行高按行数累加——引擎的 DrawText 就是这么用它的
    int w = 0;
    int maxW = 0;
    int lines = 1;
    while (*p) {
        if (*p == '\n') {
            if (w > maxW) maxW = w;
            w = 0;
            ++lines;
            ++p;
        } else if (*p >= 0x81 && p[1]) {
            w += px;
            p += 2;
        } else if (*p >= 0x81) {
            ++p;   // 落单的高位字节，跳过
        } else {
            w += AsciiAdvance(fontIdx, *p);
            ++p;
        }
    }
    if (w > maxW) maxW = w;
    *outW = maxW;
    *outH = lineH * lines;
}

}  // namespace

void CjkFontInit() {
    InitializeCriticalSection(&g_cs);
    g_cs_ready = true;
    SetUnhandledExceptionFilter(CrashFilter);

    g_alloc = reinterpret_cast<FnAllocFrame>(RvaToVa(kRvaAllocFrame));
    g_make = reinterpret_cast<FnMakeFrame>(RvaToVa(kRvaMakeFrame));
    if (!g_alloc || !g_make) {
        CHS_LOGE("cjk: 找不到造帧函数（alloc=%p make=%p）", g_alloc, g_make);
        return;
    }

    // gafObj 的 vtbl[8] 就是带缩放的贴图帧绘制
    g_draw = nullptr;   // 等第一次拿到 gafObj 再从虚表取

    void* p_run = RvaToVa(kRvaDrawRun);
    if (p_run) {
        if (!g_hook_run.Install(p_run, reinterpret_cast<void*>(&DrawRunDetour),
                                kSigDrawRun, sizeof(kSigDrawRun), kCopyRun,
                                reinterpret_cast<void**>(&g_orig_run))) {
            CHS_LOGE("cjk: DrawGlyphRun hook 安装失败");
        }
    }
    void* p_measure = RvaToVa(kRvaMeasure);
    if (p_measure) {
        if (!g_hook_measure.Install(p_measure,
                                    reinterpret_cast<void*>(&MeasureDetour),
                                    kSigMeasure, sizeof(kSigMeasure),
                                    kCopyMeasure,
                                    reinterpret_cast<void**>(&g_orig_measure))) {
            CHS_LOGE("cjk: MeasureText hook 安装失败");
        }
    }
    CHS_LOG("cjk: 字库层就绪");
}

void CjkFontShutdown() {
    CHS_LOG("cjk: 造字 %ld 个，绘制汉字 %ld 次，拉丁段 %ld 次，失败 %ld 次",
            g_made_glyph, g_drawn_cjk, g_ascii_runs, g_fail);
    g_hook_run.Remove();
    g_hook_measure.Remove();
    if (g_cs_ready) {
        DeleteCriticalSection(&g_cs);
        g_cs_ready = false;
    }
}

}  // namespace chs
