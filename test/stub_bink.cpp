// stub_bink.cpp —— 假的「原版」binkw32，只为让代理 DLL 的转发能解析
//
// 真实部署时这里替换成玩家游戏目录里的原版 binkw32.dll；测试时代替它，
// 使 binkw32.dll（我们的代理）能被 LoadLibrary 成功加载 ——
// 导出表里的 forwarder 在加载阶段就要找到目标模块与同名导出，缺一个就
// 整个加载失败。
//
// 函数体是空的：冒烟测试只关心「能不能加载 + DllMain 有没有跑」，
// 不会真的去播 Bink 视频。
#include <windows.h>

extern "C" {

void __stdcall Stub_BinkOpenMiles(void*) {}
void __stdcall Stub_BinkClose(void*) {}
void __stdcall Stub_BinkPause(void*, int) {}
void __stdcall Stub_BinkNextFrame(void*) {}
void __stdcall Stub_BinkCopyToBuffer(void*, void*, unsigned, unsigned,
                                     unsigned, void*, unsigned) {}
void __stdcall Stub_BinkDoFrame(void*) {}
void __stdcall Stub_BinkWait(void*) {}
void __stdcall Stub_BinkOpen(const char*, unsigned) {}
void __stdcall Stub_BinkGoto(void*, unsigned, int) {}
void __stdcall Stub_BinkSetSoundSystem(void*, void*) {}

}  // extern "C"

BOOL APIENTRY DllMain(HMODULE, DWORD, LPVOID) { return TRUE; }
