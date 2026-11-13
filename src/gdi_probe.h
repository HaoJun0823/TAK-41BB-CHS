// gdi_probe.h —— GDI 文本路径探针
//
// 目的：先确认游戏的文字到底走哪条路。
//   A) GAF 位图字库（FontHandler，12 槽 × 256 字形）—— 主 UI 的像素字体
//   B) GDI 的 TextOutA（0x447530 那一套）—— 用系统字体渲染到 DIB
//
// 走 B 的话，中文是免费的：GDI 在 CP936 系统上天生认 GBK 双字节，
// 只要字体支持汉字，TextOutA 收到 GBK 串就能画出来。
// 走 A 才需要自己做字形图集与 blit。
#pragma once

namespace chs {

// 装上探针。会 hook KINGDOMS.icd 导入表里的 CreateFontIndirectA / TextOutA /
// GetTextExtentPoint32A，把调用记进 tak_chs.log。
// force_chinese=true 时同时把字体换成「宋体 + GB2312_CHARSET」。
bool GdiProbeInit(bool force_chinese);

// 统计：命中次数与首个样本，退出时落盘用
void GdiProbeReport();

}  // namespace chs
