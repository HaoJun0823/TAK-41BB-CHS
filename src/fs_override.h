// fs_override.h —— 把引擎的资源读取重定向到 chs_res/
#pragma once

#include <windows.h>

namespace chs {

// 在主模块的「打开文件」函数（RVA 0x13AFF0）上装 inline hook：
// 请求的虚拟路径若在 <游戏目录>\chs_res\ 下有译文副本，就改用那份文件，
// 其余请求原样转发。
//
// 为什么必须做到这一层：任务 .txt、GUI .gui、单位 .fbi/.tdf 都打包在 HPI 里，
// 引擎的读取全部经过这个函数；松散文件与 update/ 覆盖层都不被读取。
// 返回 false 表示没装上（游戏照常运行，只是这些资源仍是英文）。
bool FsOverrideInit(HMODULE self);

// 退出时把命中计数写进日志。
void FsOverrideReport();

}  // namespace chs
