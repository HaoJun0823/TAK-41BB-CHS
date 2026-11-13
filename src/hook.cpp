// hook.cpp
#include "hook.h"

#include <cstdint>
#include <cstring>

#include "log.h"

namespace chs {

const unsigned char InlineHook::kWildcard;

InlineHook::InlineHook() { memset(saved_, 0, sizeof(saved_)); }

InlineHook::~InlineHook() { Remove(); }

bool InlineHook::RestoreProtect(void* addr, size_t len, DWORD* old) {
    return VirtualProtect(addr, len, PAGE_EXECUTE_READWRITE, old) != FALSE;
}

bool InlineHook::Install(void* target, void* detour,
                         const unsigned char* pattern, int pattern_len,
                         int copy_len, void** out_tramp) {
    if (!target || !detour || !pattern || pattern_len <= 0) return false;
    if (copy_len < 5 || copy_len > static_cast<int>(sizeof(saved_))) {
        CHS_LOGE("hook: copy_len=%d 越界（需 5..%d）", copy_len,
                 static_cast<int>(sizeof(saved_)));
        return false;
    }

    // 第 0 步：先确认这块地址真的能读。
    // RVA 是按 41BB 的地址表算出来的，换一个宿主进程（或版本不符）就可能
    // 落在未映射页上 —— 直接读签名会访问冲突，整个游戏当场崩掉。
    // 宁可在这里失败，也不要让玩家看到"启动就闪退"。
    MEMORY_BASIC_INFORMATION mbi;
    if (!VirtualQuery(target, &mbi, sizeof(mbi))) {
        CHS_LOGE("hook: VirtualQuery(%p) 失败 err=%lu", target, GetLastError());
        return false;
    }
    if (mbi.State != MEM_COMMIT) {
        CHS_LOGE("hook: 目标 %p 未提交（state=%lu），放弃", target, mbi.State);
        return false;
    }
    const DWORD kReadable = PAGE_READONLY | PAGE_READWRITE | PAGE_EXECUTE_READ |
                            PAGE_EXECUTE_READWRITE | PAGE_WRITECOPY |
                            PAGE_EXECUTE_WRITECOPY;
    if (!(mbi.Protect & kReadable) || (mbi.Protect & PAGE_GUARD)) {
        CHS_LOGE("hook: 目标 %p 不可读（protect=0x%lX），放弃", target, mbi.Protect);
        return false;
    }
    // 签名与要搬的字节不能跨出这个已提交区域
    const BYTE* region_end = static_cast<const BYTE*>(mbi.BaseAddress) + mbi.RegionSize;
    if (static_cast<const BYTE*>(target) + copy_len > region_end) {
        CHS_LOGE("hook: 目标 %p 跨越区域边界，放弃", target);
        return false;
    }

    unsigned char* p = static_cast<unsigned char*>(target);

    // 第一步永远是校验签名。版本不对时宁可什么都不做，也不要往一个
    // 未知函数里写 jmp —— 那会变成随机崩溃，比"没汉化"难查一百倍。
    for (int i = 0; i < pattern_len; ++i) {
        if (pattern[i] == kWildcard) continue;
        if (p[i] != pattern[i]) {
            CHS_LOGE("hook: 签名不匹配 @%p 偏移 %d：期望 %02X 实际 %02X",
                     target, i, pattern[i], p[i]);
            return false;
        }
    }

    // trampoline = 被覆盖的指令副本 + 跳回原函数后续
    const size_t kTrampSize = copy_len + 8;
    void* tramp = VirtualAlloc(nullptr, kTrampSize,
                              MEM_COMMIT | MEM_RESERVE, PAGE_EXECUTE_READWRITE);
    if (!tramp) {
        CHS_LOGE("hook: trampoline 分配失败 err=%lu", GetLastError());
        return false;
    }

    memcpy(tramp, target, copy_len);
    unsigned char* t = static_cast<unsigned char*>(tramp) + copy_len;
    t[0] = 0xE9;                                  // jmp rel32
    intptr_t back = reinterpret_cast<intptr_t>(p + copy_len) -
                    (reinterpret_cast<intptr_t>(t) + 5);
    if (back > 0x7FFFFFFF || back < -0x7FFFFFFF) {
        // E9 只能跳 ±2GB；拿到这么远的地址说明分配器不合作，直接放弃
        CHS_LOGE("hook: trampoline 距目标 %lld 字节，超出 rel32 范围",
                 static_cast<long long>(back));
        VirtualFree(tramp, 0, MEM_RELEASE);
        return false;
    }
    const int32_t rel_back = static_cast<int32_t>(back);
    memcpy(t + 1, &rel_back, 4);

    // 第二步才改目标：先把原字节存好，再写 jmp
    memcpy(saved_, target, copy_len);
    DWORD old = 0;
    if (!RestoreProtect(target, copy_len, &old)) {
        CHS_LOGE("hook: VirtualProtect 失败 err=%lu", GetLastError());
        VirtualFree(tramp, 0, MEM_RELEASE);
        return false;
    }

    p[0] = 0xE9;
    int32_t rel = static_cast<int32_t>(reinterpret_cast<intptr_t>(detour) -
                                       (reinterpret_cast<intptr_t>(p) + 5));
    memcpy(p + 1, &rel, 4);
    // copy_len 可能大于 5（例如 8），多出来的字节填 nop，避免残留半条指令
    for (int i = 5; i < copy_len; ++i) p[i] = 0x90;

    DWORD tmp = 0;
    VirtualProtect(target, copy_len, old, &tmp);
    FlushInstructionCache(GetCurrentProcess(), target, copy_len);

    target_ = target;
    tramp_ = tramp;
    copy_len_ = copy_len;
    installed_ = true;
    if (out_tramp) *out_tramp = tramp;

    CHS_LOG("hook: 已挂 %p（copy_len=%d，tramp=%p）", target, copy_len, tramp);
    return true;
}

void InlineHook::Remove() {
    if (!installed_ || !target_) return;
    DWORD old = 0;
    if (RestoreProtect(target_, copy_len_, &old)) {
        memcpy(target_, saved_, copy_len_);
        DWORD tmp = 0;
        VirtualProtect(target_, copy_len_, old, &tmp);
        FlushInstructionCache(GetCurrentProcess(), target_, copy_len_);
    }
    installed_ = false;
}

}  // namespace chs
