// cd_patch.cpp —— 见 cd_patch.h 的说明
//
// 这几处字节来自 4.0BB（GOG 免 DRM 版）与 4.1BB（零售版）的逐字节对比：
// 两版主程序只差 43 字节，其中与「要光盘」相关的就是下面几条分支被
// GOG 改成了无条件跳过。我们在内存里照抄同样的写法。
//
// 为什么不改文件：游戏的 .text 段文件偏移恰好等于 RVA，改文件很容易，
// 但那样哈希就变了；而哈希是联机与「原版完整性」的凭据。
#include "cd_patch.h"

#include <windows.h>
#include <stdio.h>
#include <string.h>

namespace chs {
namespace {

// 1999 年构建，没有 ASLR，ImageBase 固定 0x400000（IDA 里看到的就是这个基址）。
const uintptr_t kImageBase = 0x400000;

struct Site {
    uintptr_t va;             // IDA 地址 = ImageBase + RVA
    unsigned char before[6];  // 4.1BB 原字节（不匹配就不动，防止改错版本）
    unsigned char after[6];   // 4.0BB 写法
    int len;
    const char* what;
};

const Site kSites[] = {
    // 主菜单 PlayStory：原本 je 才跳过 Kingdoms.ID / IronPlague.ID 检查，
    // 改成无条件跳，直接进章节流程。
    {0x4A5146, {0x74, 0x12}, {0xEB, 0x12}, 2, "PlayStory 检查分支"},
    {0x4A5159, {0x7D}, {0x00}, 1, "PlayStory 失败偏移"},
};

char g_report[256] = "cd: 未执行";
LONG g_done = 0;

}  // namespace

void CdCheckBypass() {
    // DllMain 阶段就会被调用，可能与初始化线程并发，用原子量保证只跑一次。
    if (InterlockedExchange(&g_done, 1) != 0) {
        return;
    }

    BYTE* img = reinterpret_cast<BYTE*>(GetModuleHandleA(nullptr));
    if (!img) {
        strcpy_s(g_report, "cd: 取不到主模块基址");
        OutputDebugStringA(g_report);
        return;
    }

    int patched = 0;
    int already = 0;   // 已经是 4.0BB 的写法（GOG 版），无需处理
    int failed = 0;

    for (const Site& s : kSites) {
        BYTE* p = img + (s.va - kImageBase);
        if (memcmp(p, s.before, s.len) != 0) {
            // 已经是目标写法，或者根本不是这个版本 —— 两种情况都不该硬写。
            ++already;
            continue;
        }
        DWORD old = 0;
        if (!VirtualProtect(p, s.len, PAGE_EXECUTE_READWRITE, &old)) {
            ++failed;
            continue;
        }
        memcpy(p, s.after, s.len);
        VirtualProtect(p, s.len, old, &old);
        FlushInstructionCache(GetCurrentProcess(), p, s.len);
        ++patched;
    }

    _snprintf_s(g_report, sizeof(g_report), _TRUNCATE,
                "cd: 光盘校验短路 %d 处（无需处理 %d，失败 %d）",
                patched, already, failed);
    OutputDebugStringA(g_report);
}

const char* CdCheckBypassReport() { return g_report; }

}  // namespace chs
