// log.h —— 极简日志：同时写调试器和 DLL 同目录的 tak_chs.log
//
// 为什么不用 OutputDebugString 就够：玩家机器上通常没有调试器，出问题时
// 只能靠日志文件回传。日志必须能回答三个问题：
//   1. DLL 有没有被加载（一行启动横幅）
//   2. 签名有没有匹配上（失败时说明是版本不对，而不是代码写错）
//   3. 字典有没有读到（条数、路径）
#pragma once

#include <windows.h>

namespace chs {

// 初始化日志文件。path 为空则只写调试器。
void LogInit(const wchar_t* path);

// 关闭句柄（DLL_PROCESS_DETACH 时调用）
void LogShutdown();

// 内部使用，外部请用下面的宏
void LogWrite(const char* fmt, ...);

}  // namespace chs

#define CHS_LOG(...) ::chs::LogWrite(__VA_ARGS__)

// 失败也要留下痕迹：返回码 + 行号，避免"什么都没发生"的黑箱
#define CHS_LOGE(...) ::chs::LogWrite("[ERR] " __VA_ARGS__)
