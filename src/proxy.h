// proxy.h —— 代理 DLL 的公共部分
//
// 我们的 DLL 以系统 DLL 的名字（version.dll）被游戏加载，所以本身必须
// 完整长成那个 DLL 的样子：导出表里的每一项都要有，且行为与系统原件一致。
// 做法是每个导出一个「裸跳板」：第一次被调用时按全路径加载系统目录里的
// 原版，取到真函数地址后直接跳过去。之后每次调用只多 4 条指令。
#pragma once

namespace chs {

// 预先解析全部导出。在初始化线程里调用：那时已经脱离 loader lock，
// 把 LoadLibrary 的代价挪到游戏开始绘制之前，避免运行期首次调用时踩锁。
void ProxyPrewarm();

// 原版是否已成功加载（转发全部可用）。
bool ProxyReady();

// 原版模块的完整路径（日志用），失败时为空串。
const wchar_t* ProxyRealPath();

}  // namespace chs
