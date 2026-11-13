// web_override.cpp —— 内嵌网页译文重定向
//
// 05 组（atlas/、Boneyards/、GC/ 下的 .htm/.html）是**散落在游戏目录里的
// 真实文件**，不在 HPI 里，所以 update 覆盖层管不到它们。为了守住「磁盘上
// 游戏文件零改动」，这里在游戏主模块的导入表上挂 CreateFileA：
//
//   游戏：CreateFileA(".\\boneyards\\Help\\Help12.htm")
//   我们：文件存在 <游戏目录>\chs_web\boneyards\Help\Help12.htm
//         → 改成打开那份，原文件一个字节不动
//
// 只碰 .htm/.html 且路径落在 atlas/ Boneyards/ GC/ 三个目录下的请求，
// 其余一律原样转发。没装 chs_web 时行为与没打这个补丁完全一致。
#include <windows.h>

#include <stdio.h>
#include <string.h>

#include "log.h"
#include "web_override.h"

namespace chs {
namespace {

typedef HANDLE(WINAPI* CreateFileAFn)(LPCSTR, DWORD, DWORD, LPSECURITY_ATTRIBUTES,
                                      DWORD, DWORD, HANDLE);

CreateFileAFn g_real = nullptr;
char g_game_dir[MAX_PATH] = {0};     // 小写、无结尾反斜杠
char g_shadow_dir[MAX_PATH] = {0};   // <游戏目录>\chs_web
volatile LONG g_hits = 0;
volatile LONG g_misses = 0;
volatile LONG g_logged = 0;
const LONG kLogLimit = 60;           // 同一个页面会被反复打开，别把日志刷爆

bool ToLowerAscii(char* s) {
    for (; *s; ++s) {
        if (*s >= 'A' && *s <= 'Z') {
            *s = static_cast<char>(*s - 'A' + 'a');
        }
    }
    return true;
}

bool EndsWithNoCase(const char* s, const char* tail) {
    size_t n = strlen(s), m = strlen(tail);
    if (n < m) {
        return false;
    }
    return _stricmp(s + n - m, tail) == 0;
}

// 请求路径 -> chs_web 下的候选路径。不是我们管的页面就返回 false。
bool MapToShadow(const char* name, char (&out)[MAX_PATH]) {
    if (!name || !*name) {
        return false;
    }
    if (!EndsWithNoCase(name, ".htm") && !EndsWithNoCase(name, ".html")) {
        return false;
    }

    char norm[MAX_PATH];
    size_t n = strlen(name);
    if (n >= sizeof(norm)) {
        return false;
    }
    memcpy(norm, name, n + 1);
    for (size_t i = 0; i < n; ++i) {
        if (norm[i] == '/') {
            norm[i] = '\\';
        }
    }

    const char* rel = norm;
    if (rel[0] == '.' && rel[1] == '\\') {
        rel += 2;
    }
    size_t base = strlen(g_game_dir);
    if (base && _strnicmp(rel, g_game_dir, base) == 0 && rel[base] == '\\') {
        rel += base + 1;
    } else if (strchr(rel, ':')) {
        return false;   // 别的盘/别的安装目录，不归我们管
    }

    char lower[MAX_PATH];
    size_t rn = strlen(rel);
    if (rn == 0 || rn >= sizeof(lower)) {
        return false;
    }
    memcpy(lower, rel, rn + 1);
    ToLowerAscii(lower);
    if (!(strncmp(lower, "atlas\\", 6) == 0 ||
          strncmp(lower, "boneyards\\", 10) == 0 ||
          strncmp(lower, "gc\\", 3) == 0)) {
        return false;
    }

    // 候选路径沿用请求里的原始大小写：Windows 文件系统不区分大小写。
    if (_snprintf_s(out, sizeof(out), _TRUNCATE, "%s\\%s", g_shadow_dir, rel) < 0) {
        return false;
    }
    DWORD attr = GetFileAttributesA(out);
    return attr != INVALID_FILE_ATTRIBUTES && !(attr & FILE_ATTRIBUTE_DIRECTORY);
}

HANDLE WINAPI HookedCreateFileA(LPCSTR name, DWORD access, DWORD share,
                                LPSECURITY_ATTRIBUTES sa, DWORD disp,
                                DWORD flags, HANDLE tmpl) {
    char shadow[MAX_PATH];
    if (MapToShadow(name, shadow)) {
        LONG n = InterlockedIncrement(&g_hits);
        if (InterlockedIncrement(&g_logged) <= kLogLimit) {
            CHS_LOG("web: %s -> chs_web", name);
        } else if (n == kLogLimit + 1) {
            CHS_LOG("web: 命中已超过 %ld 次，后续不再逐条记录", kLogLimit);
        }
        return g_real(shadow, access, share, sa, disp, flags, tmpl);
    }
    if (name && (EndsWithNoCase(name, ".htm") || EndsWithNoCase(name, ".html"))) {
        LONG n = InterlockedIncrement(&g_misses);
        if (n <= 20) {
            CHS_LOG("web: 未命中译文 %s", name);
        }
    }
    return g_real(name, access, share, sa, disp, flags, tmpl);
}

// 在主模块的导入表里找到 kernel32!CreateFileA，换成我们的。
bool PatchIat() {
    HMODULE main = GetModuleHandleA(nullptr);
    if (!main) {
        return false;
    }
    auto base = reinterpret_cast<BYTE*>(main);
    auto dos = reinterpret_cast<IMAGE_DOS_HEADER*>(base);
    if (dos->e_magic != IMAGE_DOS_SIGNATURE) {
        return false;
    }
    auto nt = reinterpret_cast<IMAGE_NT_HEADERS32*>(base + dos->e_lfanew);
    if (nt->Signature != IMAGE_NT_SIGNATURE) {
        return false;
    }
    auto& dir = nt->OptionalHeader.DataDirectory[IMAGE_DIRECTORY_ENTRY_IMPORT];
    if (!dir.VirtualAddress) {
        return false;
    }

    auto imp = reinterpret_cast<IMAGE_IMPORT_DESCRIPTOR*>(base + dir.VirtualAddress);
    for (; imp->Name; ++imp) {
        const char* dll = reinterpret_cast<const char*>(base + imp->Name);
        if (_stricmp(dll, "KERNEL32.dll") != 0 && _stricmp(dll, "kernelbase.dll") != 0) {
            continue;
        }
        auto lookup = reinterpret_cast<IMAGE_THUNK_DATA32*>(
            base + (imp->OriginalFirstThunk ? imp->OriginalFirstThunk : imp->FirstThunk));
        auto iat = reinterpret_cast<IMAGE_THUNK_DATA32*>(base + imp->FirstThunk);
        for (; lookup->u1.Function; ++lookup, ++iat) {
            if (IMAGE_SNAP_BY_ORDINAL32(lookup->u1.Ordinal)) {
                continue;
            }
            auto by_name = reinterpret_cast<IMAGE_IMPORT_BY_NAME*>(
                base + lookup->u1.AddressOfData);
            if (strcmp(reinterpret_cast<const char*>(by_name->Name), "CreateFileA") != 0) {
                continue;
            }
            if (iat->u1.Function == reinterpret_cast<ULONG_PTR>(&HookedCreateFileA)) {
                return true;   // 已经打过了
            }
            g_real = reinterpret_cast<CreateFileAFn>(iat->u1.Function);

            DWORD old = 0;
            if (!VirtualProtect(iat, sizeof(*iat), PAGE_READWRITE, &old)) {
                return false;
            }
            iat->u1.Function = reinterpret_cast<ULONG_PTR>(&HookedCreateFileA);
            VirtualProtect(iat, sizeof(*iat), old, &old);
            return true;
        }
    }
    return false;
}

}  // namespace

bool WebOverrideInit() {
    HMODULE self = nullptr;
    if (!GetModuleHandleExA(GET_MODULE_HANDLE_EX_FLAG_FROM_ADDRESS |
                                GET_MODULE_HANDLE_EX_FLAG_UNCHANGED_REFCOUNT,
                            reinterpret_cast<LPCSTR>(&WebOverrideInit), &self)) {
        return false;
    }
    char dll[MAX_PATH] = {0};
    if (!GetModuleFileNameA(self, dll, MAX_PATH)) {
        return false;
    }
    char* slash = strrchr(dll, '\\');
    if (!slash) {
        return false;
    }
    *slash = '\0';
    strncpy_s(g_game_dir, dll, _TRUNCATE);
    ToLowerAscii(g_game_dir);
    if (_snprintf_s(g_shadow_dir, sizeof(g_shadow_dir), _TRUNCATE, "%s\\chs_web", dll) < 0) {
        return false;
    }
    if (!PatchIat()) {
        CHS_LOGE("web: 导入表里找不到 CreateFileA，网页译文不会生效");
        return false;
    }
    CHS_LOG("web: CreateFileA 已接管，译文目录 %s", g_shadow_dir);
    // 自检：拿一个真实存在的帮助页走一遍匹配逻辑，免得「装上了但路径没对上」
    // 这种问题只能在游戏里靠猜。
    char probe[MAX_PATH] = {0};
    if (MapToShadow(".\\boneyards\\Help\\Help1.htm", probe)) {
        CHS_LOG("web: 自检通过，示例 %s", probe);
    } else {
        CHS_LOG("web: 自检未命中（chs_web 里没有 Boneyards/Help/Help1.htm？）");
    }
    return true;
}

void WebOverrideReport() {
    if (!g_real) {
        return;
    }
    CHS_LOG("web: 命中 %ld，未命中页面 %ld", g_hits, g_misses);
}

}  // namespace chs
