// fs_override.cpp —— 资源读取重定向（任务 / GUI / 单位定义）
//
// 背景：游戏把 missions/*.txt、guis/*.gui、units/*.fbi、translate/*.tdf
// 全打包在 HPI 里，读取统一走主模块的 sub_53AFF0：
//
//     int __stdcall sub_53AFF0(const char* name, const char* mode, int noLoose)
//     {
//         if (!noLoose) { 先试 fopen(前缀+name)，再试 fopen(name) }
//         ... 从已加载的 HPI 列表里找同名记录 ...
//     }
//
// 实测 noLoose 恒为非 0（文件系统初始化时设置了 Override File Search Path），
// 所以松散文件永远被跳过；把文件铺到游戏根目录不会有任何效果。
//
// 这里直接接管该函数：请求的虚拟路径若在 <游戏目录>\chs_res\ 下有译文副本，
// 就用那份文件构造引擎自己的文件对象返回（与原生 fopen 分支同一套对象，
// 后续 vtable 读取/关闭完全兼容）；没有则原样转发。
#include <windows.h>

#include <stdio.h>
#include <string.h>

#include <string>
#include <unordered_map>

#include "fs_override.h"
#include "hook.h"
#include "log.h"

namespace chs {
namespace {

// 引擎侧签名（41BB）：打开文件。三个参数均为栈传参，callee 清栈。
typedef void* (__stdcall* FnOpen)(const char* name, const char* mode, int noLoose);
// 引擎自己的分配器（cdecl，调用方清栈）。
typedef void* (__cdecl* FnAlloc)(size_t size);
// 文件对象构造：__thiscall(this=ecx, FILE*, name)，用 __fastcall 转发即可。
typedef void* (__fastcall* FnFileCtor)(void* self, void* edx, FILE* f, const char* name);

const uintptr_t kRvaOpen = 0x13AFF0;      // sub_53AFF0
const uintptr_t kRvaAlloc = 0x0EB9E0;     // sub_4EB9E0
const uintptr_t kRvaFileCtor = 0x1398F0;  // sub_5398F0

// 引擎自己的文件系统锁。原生分支在分配文件对象时是持锁的，
// 我们的接管路径绕过原函数，必须补上同样的互斥，否则两个线程同时
// 调引擎分配器可能踩坏它的空闲链表。
const uintptr_t kRvaFsTarget = 0x26E3A4;  // LONG Target（当前持锁线程 id）
const uintptr_t kRvaFsOwner = 0x26E39C;   // LONG dword_66E39C
const uintptr_t kRvaFsEvent = 0x26E3A0;   // HANDLE hEvent
LONG* g_fs_target = nullptr;
LONG* g_fs_owner = nullptr;
HANDLE* g_fs_event = nullptr;

// push ebp; mov ebp,esp; sub esp,10Ch; push ebx; push esi; push edi
const unsigned char kSigOpen[] = {0x55, 0x8B, 0xEC, 0x81, 0xEC, 0x0C,
                                  0x01, 0x00, 0x00, 0x53, 0x56, 0x57};
const int kCopyOpen = 9;  // 前三条指令恰好 9 字节，落在指令边界

FnOpen g_orig_open = nullptr;
FnAlloc g_alloc = nullptr;
FnFileCtor g_ctor = nullptr;
InlineHook g_hook;

std::unordered_map<std::string, std::wstring> g_res;
bool g_ready = false;
volatile LONG g_opens = 0;
volatile LONG g_hits = 0;
volatile LONG g_errs = 0;

const LONG kLogLimit = 20;  // 同一个资源会被反复打开，别刷爆日志

// RVA → 运行时地址。按模块实际加载基址换算，ASLR 下也正确。
void* VaFromRva(uintptr_t rva) {
    HMODULE mod = GetModuleHandleW(nullptr);
    if (!mod) {
        return nullptr;
    }
    BYTE* base = reinterpret_cast<BYTE*>(mod);
    IMAGE_DOS_HEADER* dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) {
        return nullptr;
    }
    IMAGE_NT_HEADERS* nt = reinterpret_cast<IMAGE_NT_HEADERS*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) {
        return nullptr;
    }
    if (rva >= nt->OptionalHeader.SizeOfImage) {
        return nullptr;
    }
    return base + rva;
}

// 统一键：小写、'\\' → '/'、去掉开头的 "./"。
std::string NormalizeKey(const char* s) {
    if (!s) {
        return std::string();
    }
    while (s[0] == '.' && (s[1] == '\\' || s[1] == '/')) {
        s += 2;
    }
    std::string out;
    out.reserve(strlen(s));
    for (; *s; ++s) {
        char c = *s;
        if (c == '\\') {
            c = '/';
        } else if (c >= 'A' && c <= 'Z') {
            c = static_cast<char>(c - 'A' + 'a');
        }
        out.push_back(c);
    }
    return out;
}

std::string NormalizeKeyW(const std::wstring& w) {
    std::string out;
    out.reserve(w.size());
    for (wchar_t wc : w) {
        char c = (wc >= 0 && wc < 0x80) ? static_cast<char>(wc) : '?';
        if (c == '\\') {
            c = '/';
        } else if (c >= 'A' && c <= 'Z') {
            c = static_cast<char>(c - 'A' + 'a');
        }
        out.push_back(c);
    }
    return out;
}

void ScanDir(const std::wstring& dir, const std::wstring& rel) {
    WIN32_FIND_DATAW fd;
    std::wstring pattern = dir + L"\\*";
    HANDLE h = FindFirstFileW(pattern.c_str(), &fd);
    if (h == INVALID_HANDLE_VALUE) {
        return;
    }
    do {
        if (wcscmp(fd.cFileName, L".") == 0 || wcscmp(fd.cFileName, L"..") == 0) {
            continue;
        }
        std::wstring full = dir + L"\\" + fd.cFileName;
        std::wstring child = rel.empty() ? std::wstring(fd.cFileName)
                                         : rel + L"\\" + fd.cFileName;
        if (fd.dwFileAttributes & FILE_ATTRIBUTE_DIRECTORY) {
            ScanDir(full, child);
        } else {
            g_res[NormalizeKeyW(child)] = full;
        }
    } while (FindNextFileW(h, &fd));
    FindClose(h);
}

// 与 sub_53AFF0 相同的递归锁语义：同一线程重复进入时不算新持有。
bool FsLock() {
    if (!g_fs_target || !g_fs_owner || !g_fs_event) {
        return false;
    }
    // 事件对象还没建好时不能等，否则会永久挂起（初始化早期 hEvent 为 NULL）。
    if (!*g_fs_event) {
        return false;
    }
    const LONG tid = static_cast<LONG>(GetCurrentThreadId());
    for (;;) {
        LONG prev = InterlockedExchange(g_fs_target, tid);
        if (!prev) {
            *g_fs_owner = tid;
            return true;
        }
        if (*g_fs_owner == tid) {
            return false;   // 本线程已持有，交回时不要重复释放
        }
        WaitForSingleObject(*g_fs_event, INFINITE);
    }
}

void FsUnlock(bool acquired) {
    if (!acquired || !g_fs_target || !g_fs_owner || !g_fs_event || !*g_fs_event) {
        return;
    }
    *g_fs_owner = 0;
    InterlockedExchange(g_fs_target, 0);
    SetEvent(*g_fs_event);
}

// 命中就用我们的文件构造一个引擎文件对象。
void* TryServe(const char* name, const char* mode) {
    if (!g_ready || !name || !name[0] || !mode) {
        return nullptr;
    }
    // 只接管读。写模式的请求一定是游戏自己的存档/配置，绝不能改。
    if (mode[0] != 'r' || strchr(mode, '+')) {
        return nullptr;
    }
    std::string key = NormalizeKey(name);
    auto it = g_res.find(key);
    if (it == g_res.end()) {
        return nullptr;
    }
    FILE* f = _wfopen(it->second.c_str(), L"rb");
    if (!f) {
        if (InterlockedIncrement(&g_errs) <= kLogLimit) {
            CHS_LOGE("res: 打不开 %ls", it->second.c_str());
        }
        return nullptr;
    }
    // 0x108 是引擎文件对象的固定大小（原生分支就是这么分配的）。
    // 分配与构造都在引擎自己的文件系统锁里完成，与原生路径一致。
    void* obj = nullptr;
    const bool locked = FsLock();
    if (g_alloc && g_ctor) {
        obj = g_alloc(0x108);
        if (obj) {
            g_ctor(obj, nullptr, f, name);
        }
    }
    FsUnlock(locked);
    if (!obj) {
        fclose(f);
        return nullptr;
    }
    if (InterlockedIncrement(&g_hits) <= kLogLimit) {
        CHS_LOG("res: 接管 %s", name);
    }
    return obj;
}

void* __stdcall DetourOpen(const char* name, const char* mode, int noLoose) {
    InterlockedIncrement(&g_opens);
    if (void* obj = TryServe(name, mode)) {
        return obj;
    }
    return g_orig_open(name, mode, noLoose);
}

}  // namespace

bool FsOverrideInit(HMODULE self) {
    wchar_t dll_path[MAX_PATH] = {0};
    if (!GetModuleFileNameW(self, dll_path, MAX_PATH)) {
        CHS_LOGE("res: 取自身路径失败");
        return false;
    }
    wchar_t* slash = wcsrchr(dll_path, L'\\');
    if (!slash) {
        return false;
    }
    *(slash + 1) = L'\0';
    std::wstring game_dir(dll_path);
    std::wstring res_dir = game_dir + L"chs_res";

    if (GetFileAttributesW(res_dir.c_str()) == INVALID_FILE_ATTRIBUTES) {
        CHS_LOGE("res: 没有 %ls，任务与 GUI 译文不会生效", res_dir.c_str());
        return false;
    }

    ScanDir(res_dir, L"");
    if (g_res.empty()) {
        CHS_LOGE("res: %ls 是空的", res_dir.c_str());
        return false;
    }
    g_ready = true;
    CHS_LOG("res: 索引 %d 个资源文件", static_cast<int>(g_res.size()));

    void* p_open = VaFromRva(kRvaOpen);
    void* p_alloc = VaFromRva(kRvaAlloc);
    void* p_ctor = VaFromRva(kRvaFileCtor);
    if (!p_open || !p_alloc || !p_ctor) {
        CHS_LOGE("res: 地址解析失败 open=%p alloc=%p ctor=%p", p_open, p_alloc, p_ctor);
        return false;
    }
    g_alloc = reinterpret_cast<FnAlloc>(p_alloc);
    g_ctor = reinterpret_cast<FnFileCtor>(p_ctor);

    g_fs_target = reinterpret_cast<LONG*>(VaFromRva(kRvaFsTarget));
    g_fs_owner = reinterpret_cast<LONG*>(VaFromRva(kRvaFsOwner));
    g_fs_event = reinterpret_cast<HANDLE*>(VaFromRva(kRvaFsEvent));
    if (!g_fs_target || !g_fs_owner || !g_fs_event) {
        CHS_LOGE("res: 文件系统锁地址解析失败");
        return false;
    }

    void* tramp = nullptr;
    if (!g_hook.Install(p_open, reinterpret_cast<void*>(&DetourOpen), kSigOpen,
                        static_cast<int>(sizeof(kSigOpen)), kCopyOpen, &tramp)) {
        CHS_LOGE("res: 文件打开 hook 安装失败");
        return false;
    }
    g_orig_open = reinterpret_cast<FnOpen>(tramp);
    CHS_LOG("res: 文件打开 hook 已就绪 @%p", p_open);
    return true;
}

void FsOverrideReport() {
    if (!g_hook.installed()) {
        return;
    }
    CHS_LOG("res: 命中 %ld 次 / 打开 %ld 次，失败 %ld 次", g_hits, g_opens, g_errs);
}

}  // namespace chs
