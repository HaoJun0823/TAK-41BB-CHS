// dllmain.cpp —— 注入入口
//
// 加载路径（见 docs/02 §6.1）：
//   KINGDOMS.icd 按名字导入 binkw32.dll → 我们用同名代理 DLL 顶替 →
//   代理把 10 个 _Bink* 转发给原版 binkw32_orig.dll，
//   同时在本进程里调用 chs::TranslateInit()。
// 磁盘上的游戏文件一个字节都不改，卸载只需删掉代理与我们的 DLL。
#include "pch.h"

#include "src/cjk_font.h"
#include "src/gdi_probe.h"
#include "src/log.h"
#include "src/translate.h"

namespace {

// DllMain 里不能做重活：此时持有 loader lock，读文件/建线程都可能死锁。
// 所以真正的初始化放进独立线程。
DWORD WINAPI InitThread(LPVOID param) {
    HMODULE self = static_cast<HMODULE>(param);

    wchar_t dll_path[MAX_PATH] = {0};
    if (!GetModuleFileNameW(self, dll_path, MAX_PATH)) {
        return 1;
    }
    // 取目录（含结尾反斜杠）
    wchar_t dir[MAX_PATH] = {0};
    wcscpy_s(dir, dll_path);
    wchar_t* slash = wcsrchr(dir, L'\\');
    if (slash) *(slash + 1) = L'\0';

    wchar_t log_path[MAX_PATH] = {0};
    wcscpy_s(log_path, dir);
    wcscat_s(log_path, L"tak_chs.log");
    chs::LogInit(log_path);

    CHS_LOG("==== TAK CHS DLL 启动 ====");
    CHS_LOG("自身: %ls", dll_path);

    wchar_t dict_path[MAX_PATH] = {0};
    wcscpy_s(dict_path, dir);
    wcscat_s(dict_path, L"chs.dict");

    if (!chs::TranslateInit(self, dict_path)) {
        CHS_LOGE("初始化失败，游戏按原样运行");
    }

    // GDI 文本探针：先确认主 UI 到底走 GAF 位图字库还是系统 GDI。
    // 强制换字体用开关文件控制，免得每次都要重编译：
    //   在游戏根目录放一个 chs.gdifont（内容随意）就启用「宋体 + GB2312」。
    wchar_t flag_path[MAX_PATH] = {0};
    wcscpy_s(flag_path, dir);
    wcscat_s(flag_path, L"chs.gdifont");
    const bool force_cn =
        GetFileAttributesW(flag_path) != INVALID_FILE_ATTRIBUTES;
    chs::GdiProbeInit(force_cn);

    // 中文字库层：把汉字做成运行时纹理帧，交给引擎自己的贴图管线绘制。
    chs::CjkFontInit();

    return 0;
}

}  // namespace

BOOL APIENTRY DllMain(HMODULE hModule, DWORD reason, LPVOID lpReserved) {
    switch (reason) {
    case DLL_PROCESS_ATTACH:
        DisableThreadLibraryCalls(hModule);   // 减少 THREAD_* 通知开销
        {
            HANDLE t = CreateThread(nullptr, 0, InitThread, hModule, 0, nullptr);
            if (t) {
                CloseHandle(t);
            }
        }
        break;

    case DLL_PROCESS_DETACH:
        // lpReserved != NULL 表示进程正在终止，此时改内存没有意义，
        // 只把统计落盘；为 NULL 才是 FreeLibrary，需要真正还原函数头。
        if (lpReserved == nullptr) {
            chs::TranslateShutdown();
        } else {
            CHS_LOG("==== 进程退出 ====");
        }
        chs::GdiProbeReport();
        chs::CjkFontShutdown();
        chs::LogShutdown();
        break;

    default:
        break;
    }
    return TRUE;
}
