// target.cpp —— 见 target.h 的说明
#include "target.h"

#include <windows.h>
#include <stdio.h>
#include <string.h>
#include <wchar.h>

namespace chs {
namespace {

// 我们打补丁用的是**虚拟地址**（0x58F220 这一族），不是 RVA。之前把
// 0x590000 直接当成 SizeOfImage 的下限，于是 SizeOfImage 只有 0x271000 的
// 真身被判成「非目标进程」——DllMain 静默 break，一个字节都不改，连日志
// 都不写。表现就是「换宿主 DLL 之后完全没反应」，很容易误判成宿主选错了。
//
// 正确约束：映像必须覆盖到最高补丁 VA。41BB 的 SizeOfImage = 0x271000，
// 覆盖到 VA 0x671000，余量充足。
const DWORD kHighestPatchVa = 0x58F220;   // MakeFrame
const DWORD kExpectedImageBase = 0x400000;
const DWORD kMinImageSize = (kHighestPatchVa - kExpectedImageBase) + 0x1000;

const wchar_t* const kExeNames[] = {L"KINGDOMS.icd", L"ironplague.icd"};

bool NameMatches(const wchar_t* name) {
    for (const wchar_t* want : kExeNames) {
        if (_wcsicmp(name, want) == 0) return true;
    }
    return false;
}

// 判定结果缓存：DllMain 与初始化线程都会问，避免重复解析 PE 头。
// 不加锁：两个线程各算一次结果完全相同，重复写同一份内容没有危害。
struct Verdict {
    bool done = false;
    bool ok = false;
    bool name_matched = false;
    char report[256] = {0};
};

Verdict g_verdict;

void Evaluate() {
    char* rep = g_verdict.report;
    wchar_t path[MAX_PATH] = {0};
    if (!GetModuleFileNameW(nullptr, path, MAX_PATH)) {
        _snprintf_s(rep, sizeof(g_verdict.report), _TRUNCATE,
                    "target: 取不到主模块路径 err=%lu", GetLastError());
        g_verdict.ok = false;
        return;
    }

    const wchar_t* name = wcsrchr(path, L'\\');
    name = name ? name + 1 : path;
    g_verdict.name_matched = NameMatches(name);
    if (!g_verdict.name_matched) {
        // 转成 GBK 便于日志阅读（此刻日志可能还没初始化，调用方会自行处理）
        char narrow[MAX_PATH] = {0};
        WideCharToMultiByte(936, 0, name, -1, narrow, sizeof(narrow), nullptr,
                            nullptr);
        _snprintf_s(rep, sizeof(g_verdict.report), _TRUNCATE,
                    "target: 主模块是 %s，非游戏本体 → 只做代理转发", narrow);
        g_verdict.ok = false;
        return;
    }

    BYTE* base = reinterpret_cast<BYTE*>(GetModuleHandleW(nullptr));
    IMAGE_DOS_HEADER* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    if (!base || dos->e_magic != IMAGE_DOS_SIGNATURE) {
        _snprintf_s(rep, sizeof(g_verdict.report), _TRUNCATE,
                    "target: DOS 头异常");
        g_verdict.ok = false;
        return;
    }
    IMAGE_NT_HEADERS* nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) {
        _snprintf_s(rep, sizeof(g_verdict.report), _TRUNCATE,
                    "target: NT 头异常");
        g_verdict.ok = false;
        return;
    }

    const DWORD image_base = nt->OptionalHeader.ImageBase;
    const DWORD image_size = nt->OptionalHeader.SizeOfImage;
    if (image_base != kExpectedImageBase || image_size < kMinImageSize) {
        _snprintf_s(rep, sizeof(g_verdict.report), _TRUNCATE,
                    "target: %ls 结构不符（ImageBase=0x%lX SizeOfImage=0x%lX）",
                    name, image_base, image_size);
        g_verdict.ok = false;
        return;
    }

    _snprintf_s(rep, sizeof(g_verdict.report), _TRUNCATE,
                "target: %ls 确认（base=0x%lX SizeOfImage=0x%lX）", name,
                image_base, image_size);
    g_verdict.ok = true;
}

}  // namespace

bool IsTargetGame() {
    if (!g_verdict.done) {
        Evaluate();
        g_verdict.done = true;
    }
    return g_verdict.ok;
}

const char* TargetGameReport() { return g_verdict.report; }

bool TargetNameMatched() {
    if (!g_verdict.done) {
        Evaluate();
        g_verdict.done = true;
    }
    return g_verdict.name_matched;
}

}  // namespace chs
