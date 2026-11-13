// target.h —— 「当前进程是不是我们要打补丁的那份游戏」
//
// 为什么需要它：代理 DLL 是按名字被加载的，游戏目录里任何 exe 启动时只要
// 导入同名 DLL，都会把我们加载进去：
//   KINGDOMS.icd      ← 目标
//   ironplague.icd    ← 资料片（同一套引擎，若结构一致也能用）
//   ChooseRenderer.exe / Cartographer.exe / unins000.exe  ← 工具与卸载器
// 在非目标进程里必须**只做转发、一行补丁都不打**：我们的地址表是 41BB 的
// RVA，硬套到别的模块上轻则读到垃圾内存，重则把别人的代码改坏。
#pragma once

namespace chs {

// 主模块是可执行文件、ImageBase == 0x400000、映像大小够覆盖 0x58F220，
// 且文件名是 KINGDOMS.icd / ironplague.icd —— 四条都满足才算目标进程。
bool IsTargetGame();

// 只判文件名。用来区分两种「不是目标进程」：
//   false → 同目录的启动器/卸载器，静默转发是正常行为；
//   true  → 名字对上了但结构不符，说明我们的地址表或判定常量错了，
//           必须留下痕迹，否则现场表现就是「部署完什么都没发生」。
bool TargetNameMatched();

// 供日志用：返回判定依据（"KINGDOMS.icd/SizeOfImage=0x..." 之类），
// 失败时说明是哪一条没过。指向静态缓冲，不需要释放。
const char* TargetGameReport();

}  // namespace chs
