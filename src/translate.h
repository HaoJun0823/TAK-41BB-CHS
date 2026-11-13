// translate.h —— 翻译层：正向 sub_544020 + 反向 sub_544110，成对安装
//
// 为什么必须成对：只挂正向时，引擎的翻译映射里只有 TDF 的原生词条，
// 我们临时返回的中文串不在映射中。TextHandler 在取回/析构时会用
// sub_544110 把「译文」反查回「英文键」，查不到就拿到 NULL，
// 调用方再拿它去做标识或序列化 —— 存档与联机协议都会被污染。
#pragma once

#include <windows.h>

namespace chs {

// 安装翻译 hook。image_base 是主模块（KINGDOMS.icd）加载后的实际基址。
// dict_path 是 chs.dict 的完整路径。
bool TranslateInit(HMODULE self, const wchar_t* dict_path);

// 卸载 hook、打印统计
void TranslateShutdown();

}  // namespace chs
