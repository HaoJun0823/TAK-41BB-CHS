// web_override.h —— 把游戏对内嵌网页的读取重定向到 chs_web/
#pragma once

namespace chs {

// 在主模块导入表上接管 CreateFileA，把 atlas/ Boneyards/ GC/ 下的
// .htm/.html 请求改到 <游戏目录>\chs_web\ 里的同名译文副本。
// 返回 false 表示没装上（游戏照常运行，只是网页仍是英文）。
bool WebOverrideInit();

// 退出时把命中/未命中计数写进日志。
void WebOverrideReport();

}  // namespace chs
