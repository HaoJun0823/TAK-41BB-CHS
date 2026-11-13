// dllmain.cpp —— 注入入口
//
// 加载路径（见 docs/02 §6.1、docs/06）：
//   KINGDOMS.icd 按名字静态导入系统 DLL → 我们在游戏目录放一个同名代理，
//   应用目录优先，于是先加载到我们 → 代理把每个导出转发给系统目录的原件，
//   同时在本进程里调用 chs::TranslateInit()。
// 磁盘上的游戏文件一个字节都不改，卸载只需删掉代理与我们的 DLL。
//
// 代理宿主可以换（version.dll / binkw32.dll），靠 .def 与输出名切换；
// 本文件不关心宿主是谁，只按「当前进程是不是游戏」分叉。
#include "pch.h"

#include "src/cd_patch.h"
#include "src/cjk_font.h"
#include "src/gdi_probe.h"
#include "src/log.h"
#include "src/proxy.h"
#include "src/target.h"
#include "src/translate.h"

namespace {

// 把「和本 DLL 放一起的某个文件」的路径拼出来（tak_chs.log / tak_chs_reject.txt）。
// DllMain 里只做字符串拼接，不碰文件系统。
bool SidecarPath(HMODULE self, const wchar_t* leaf, wchar_t (&out)[MAX_PATH]) {
    if (!GetModuleFileNameW(self, out, MAX_PATH)) {
        return false;
    }
    wchar_t* slash = wcsrchr(out, L'\\');
    if (!slash) {
        return false;
    }
    *(slash + 1) = L'\0';
    if (wcslen(out) + wcslen(leaf) + 1 > MAX_PATH) {
        return false;
    }
    wcscat_s(out, leaf);
    return true;
}

// 名字是游戏本体、但结构判定没过：这是「部署完什么都没发生」的现场。
// 在 loader lock 下落一小段原因，比让人对着空目录猜半天强。
void WriteRejectNote(HMODULE self) {
    wchar_t path[MAX_PATH] = {0};
    if (!SidecarPath(self, L"tak_chs_reject.txt", path)) {
        return;
    }
    chs::LogInit(path);
    CHS_LOG("代理已加载，但拒绝在本进程生效：");
    CHS_LOG("%s", chs::TargetGameReport());
    CHS_LOG("（若这是游戏本体，说明 target.cpp 的判定常量与这份 exe 不符）");
    chs::LogShutdown();
}

// DllMain 里不能做重活：此时持有 loader lock，读文件/建线程都可能死锁。
// 所以真正的初始化放进独立线程。
DWORD WINAPI InitThread(LPVOID param) {
    HMODULE self = static_cast<HMODULE>(param);

    wchar_t dll_path[MAX_PATH] = {0};
    if (!GetModuleFileNameW(self, dll_path, MAX_PATH)) {
        return 1;
    }
    wchar_t log_path[MAX_PATH] = {0};
    if (!SidecarPath(self, L"tak_chs.log", log_path)) {
        return 1;
    }
    chs::LogInit(log_path);

    CHS_LOG("==== TAK CHS DLL 启动 ====");
    CHS_LOG("自身: %ls", dll_path);
    CHS_LOG("%s", chs::TargetGameReport());

    // 先把系统原版 DLL 解析好：此刻不在 loader lock 下，代价摊在这里，
    // 游戏运行期的转发就只剩一个 jmp。
    chs::ProxyPrewarm();
    if (chs::ProxyReady()) {
        CHS_LOG("proxy: 原版已接管 %ls", chs::ProxyRealPath());
    } else {
        CHS_LOGE("proxy: 原版加载失败，转发不可用");
    }

    CHS_LOG("%s", chs::CdCheckBypassReport());

    wchar_t dict_path[MAX_PATH] = {0};
    SidecarPath(self, L"chs.dict", dict_path);

    if (!chs::TranslateInit(self, dict_path)) {
        CHS_LOGE("初始化失败，游戏按原样运行");
    }

    // GDI 文本探针：先确认主 UI 到底走 GAF 位图字库还是系统 GDI。
    // 强制换字体用开关文件控制，免得每次都要重编译：
    //   在游戏根目录放一个 chs.gdifont（内容随意）就启用「宋体 + GB2312」。
    wchar_t flag_path[MAX_PATH] = {0};
    SidecarPath(self, L"chs.gdifont", flag_path);
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
        // 代理会被同目录里任何 exe 加载（ChooseRenderer.exe、unins000.exe……）。
        // 那些进程里只做转发，一行补丁都不能打：我们的地址表是 41BB 的 RVA，
        // 硬套到别的模块上会改坏别人的代码。
        if (!chs::IsTargetGame()) {
            if (chs::TargetNameMatched()) {
                WriteRejectNote(hModule);
            }
            break;
        }
        // 光盘校验必须在这里同步短路：初始化线程是异步的，等它被调度时
        // 主线程可能已经跑过主菜单的检查了。VirtualProtect + memcpy 不涉
        // 及加载 DLL 或等待，在 loader lock 下是安全的。
        chs::CdCheckBypass();
        {
            HANDLE t = CreateThread(nullptr, 0, InitThread, hModule, 0, nullptr);
            if (t) {
                CloseHandle(t);
            }
        }
        break;

    case DLL_PROCESS_DETACH:
        if (!chs::IsTargetGame()) {
            break;   // 非游戏进程：只转发，没有任何状态要收
        }
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
