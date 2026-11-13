// cd_patch.cpp —— 见 cd_patch.h 的说明
//
// 结论先行：4.1BB 的「要光盘」不是一个开关，而是同一个检查函数被 13 处调用
// ——主菜单 PlayStory、Battles、战役入口 A/B、HostGame、WinMain 启动门、
// 电影加载器的 BinkOpen 兜底……其中战役入口还带 10 次 × 500ms 的重试，
// 表现为「点进去先卡几秒，然后弹框」。
//
// 逐处改分支既漏又危险：4.0BB（GOG 免 DRM 版）里那些 je→jmp 是配套改动，
// 单独照抄会跳过必要初始化（实测：6 处全抄 → 左半屏花屏、启动从 4s 涨到 26s）。
//
// 所以改在源头。sub_48CF00 / sub_48CF10 只是两个 12 字节的薄包装：
//     mov eax, off_60E578("Kingdoms.ID") ; push eax ; call sub_48CF20 ; ret
// 把函数体换成「直接返回一个盘符」，13 个调用点全部通过。
//
// 返回值必须是**合法盘符**，不能图省事返回 1：调用方会拿它拼 "X:\..." 路径
// （sub_423700 找不到电影时就用它去光驱里捞）。返回 'Z' 而不是 'C'，是为了
// 万一某处真去拼 "C:\Movies\intro.bik" 而 C 盘恰好存在同名文件时不会加载错
// 东西；Z 盘基本不存在，BinkOpen 立即失败，电影静默跳过 —— 与无光盘时一致。
//
// 为什么不改文件：游戏的 .text 段文件偏移恰好等于 RVA，改文件很容易，
// 但那样哈希就变了；而哈希是联机与「原版完整性」的凭据。
#include "cd_patch.h"

#include <windows.h>
#include <stdio.h>
#include <string.h>

#include "log.h"

// 被 naked 跳板 call 的目标，必须是 C 链接、cdecl 无参。
extern "C" void __cdecl CdNoteCall();

namespace {

// 1999 年构建，没有 ASLR，ImageBase 固定 0x400000（IDA 里看到的就是这个基址）。
const uintptr_t kImageBase = 0x400000;

// 伪造的光驱盘符。见文件头说明为什么是 'Z'。
const unsigned char kFakeDrive = 'Z';

struct FuncSite {
    uintptr_t va;             // IDA 地址 = ImageBase + RVA
    unsigned char before[8];  // 4.1BB 原字节（不匹配就不动，防止改错版本）
    int len;
    const char* what;
};

const FuncSite kSites[] = {
    // mov eax, off_60E578 ; push eax ; call sub_48CF20 ; ret
    {0x48CF00, {0xA1, 0x78, 0xE5, 0x60, 0x00, 0x50, 0xE8, 0x15}, 8,
     "sub_48CF00(Kingdoms.ID)"},
    // mov eax, off_60E57C ; push eax ; call sub_48CF20 ; ret
    {0x48CF10, {0xA1, 0x7C, 0xE5, 0x60, 0x00, 0x50, 0xE8, 0x05}, 8,
     "sub_48CF10(IronPlague.ID)"},
};

// 调用计数：只为了在日志里证明「这条路径确实被走到了」，不是功能需要。
volatile LONG g_cd_calls = 0;

char g_report[256] = "cd: 未执行";
LONG g_done = 0;

// 跳板本体。用 naked 是为了保持调用约定不变：调用方以为调的是 cdecl 无参
// 函数，栈上没有任何参数、也没有返回值空间，所以 pushad/popad + ret 即可。
// 中途 call 一次记录函数，靠 pushad 保住所有寄存器（含 eax 的高位）。
extern "C" __declspec(naked) void CdReturnDrive() {
    __asm {
        pushad
        call CdNoteCall
        popad
        mov  al, 5Ah          // 'Z'
        ret
    }
}

// 注意：这个函数跑在游戏主线程里，只做计数和少量日志。
void NoteCall() {
    LONG n = InterlockedIncrement(&g_cd_calls);
    if (n <= 6) {
        CHS_LOG("cd: 光盘校验入口被调用 #%ld —— 已放行（伪盘符 %c）", n,
                static_cast<char>(kFakeDrive));
    }
}

}  // namespace

extern "C" void __cdecl CdNoteCall() { NoteCall(); }

namespace chs {

void CdCheckBypass() {
    // DllMain 阶段就会被调用，可能与初始化线程并发，用原子量保证只跑一次。
    if (InterlockedExchange(&g_done, 1) != 0) {
        return;
    }

    BYTE* img = reinterpret_cast<BYTE*>(GetModuleHandleA(nullptr));
    if (!img) {
        strcpy_s(g_report, "cd: 取不到主模块基址");
        CHS_LOG("%s", g_report);
        return;
    }

    CHS_LOG("cd: 主模块 base=%p（按名字取 KINGDOMS.icd=%p，Kingdoms.exe=%p）", img,
            GetModuleHandleA("KINGDOMS.icd"), GetModuleHandleA("Kingdoms.exe"));

    int patched = 0;
    int skipped = 0;   // 字节不匹配：要么版本不对，要么已经被改过
    int failed = 0;

    for (const FuncSite& s : kSites) {
        BYTE* p = img + (s.va - kImageBase);
        if (memcmp(p, s.before, s.len) != 0) {
            ++skipped;
            CHS_LOG("cd: %s 字节不匹配，跳过（内存 %02X %02X %02X %02X %02X %02X %02X %02X）",
                    s.what, p[0], p[1], p[2], p[3], p[4], p[5], p[6], p[7]);
            continue;
        }

        DWORD old = 0;
        if (!VirtualProtect(p, s.len, PAGE_EXECUTE_READWRITE, &old)) {
            ++failed;
            continue;
        }

        // E9 rel32 跳到跳板，其余字节填 nop（原指令已不可达）
        const intptr_t rel = reinterpret_cast<intptr_t>(&CdReturnDrive) -
                             (reinterpret_cast<intptr_t>(p) + 5);
        if (rel > 0x7FFFFFFFLL || rel < -0x7FFFFFFFLL) {
            VirtualProtect(p, s.len, old, &old);
            ++failed;
            CHS_LOG("cd: %s 跳转距离越界，放弃", s.what);
            continue;
        }

        p[0] = 0xE9;
        *reinterpret_cast<int*>(p + 1) = static_cast<int>(rel);
        for (int i = 5; i < s.len; ++i) {
            p[i] = 0x90;
        }

        VirtualProtect(p, s.len, old, &old);
        FlushInstructionCache(GetCurrentProcess(), p, s.len);
        ++patched;
    }

    _snprintf_s(g_report, sizeof(g_report), _TRUNCATE,
                "cd: 光盘校验已接管 %d 处（跳过 %d，失败 %d），伪盘符 %c",
                patched, skipped, failed, static_cast<char>(kFakeDrive));
    CHS_LOG("%s", g_report);
}

const char* CdCheckBypassReport() {
    // 报告顺带反映「到底被调用了几次」，测试时一眼能看出路径是否走到。
    static char buf[320];
    _snprintf_s(buf, sizeof(buf), _TRUNCATE, "%s；被调用 %ld 次", g_report,
                g_cd_calls);
    return buf;
}

}  // namespace chs
