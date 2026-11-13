// cd_patch.h —— 4.1BB 零售版的光盘校验短路
//
// 背景：41BB 是零售补丁版，进战役前会遍历「光驱」（GetDriveTypeA==5）
// 去找根目录的 Kingdoms.ID / IronPlague.ID，找不到就弹 INSERT_CD 对话框。
// GOG 发行的 4.0BB 把这段检查整个短路了，所以免盘。
//
// 做法：只在内存里把 4.1BB 的几条分支改成 4.0BB 的写法，磁盘文件一个字节
// 不动（哈希不变、卸载即恢复）。字节不匹配就跳过，因此同一份 DLL 在
// 4.0BB / 4.1BB 上都能用。
#pragma once

namespace chs {

// 幂等。必须在游戏执行到这些代码之前调用（DllMain 的 PROCESS_ATTACH 里最稳）。
void CdCheckBypass();

// 返回一行统计，供初始化线程写日志用（DllMain 阶段日志文件还没打开）。
const char* CdCheckBypassReport();

}  // namespace chs
