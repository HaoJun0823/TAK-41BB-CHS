// hook.h —— 极简 inline detour（x86，5 字节 E9 jmp rel32）
//
// 为什么不用 MinHook/DETOURS：本工程只挂两个地址固定的函数，签名已知，
// 且都要在 v141_xp 下编出尽量小的 DLL。引入上千行第三方代码要连带处理
// 许可、编译告警与 xp 兼容，收益不抵成本。代价是**换版本必须重新核对
// 签名与被覆盖的指令长度**，所以这里把长度做成显式参数并强制校验。
#pragma once

#include <windows.h>

namespace chs {

class InlineHook {
public:
    InlineHook();
    ~InlineHook();

    // 安装 hook。
    //   target     目标函数绝对地址
    //   detour     我们的替换函数
    //   pattern    期望的函数头字节，0x?? 用 kWildcard 表示
    //   pattern_len 签名长度（只比较这么多个字节）
    //   copy_len   需要搬到 trampoline 的字节数，**必须落在指令边界上**
    //   out_tramp  返回 trampoline 地址，调用它等于调用原函数
    //
    // copy_len 为什么必须由调用方给出：x86 是变长指令集，从函数头切够 5 字节
    // 很容易切在指令中间。手写反汇编器不值得，所以对每个目标人工确认一次。
    bool Install(void* target, void* detour,
                 const unsigned char* pattern, int pattern_len, int copy_len,
                 void** out_tramp);

    // 卸载：把函数头恢复原字节
    void Remove();

    bool installed() const { return installed_; }

    // 通配字节
    static const unsigned char kWildcard = 0xCC;

private:
    bool RestoreProtect(void* addr, size_t len, DWORD* old);

    void* target_ = nullptr;
    void* tramp_ = nullptr;
    int copy_len_ = 0;
    unsigned char saved_[16];
    bool installed_ = false;
};

}  // namespace chs
