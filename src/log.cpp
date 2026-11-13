// log.cpp
#include "log.h"

#include <cstdarg>
#include <cstdio>
#include <cstring>

namespace chs {

namespace {
HANDLE g_file = INVALID_HANDLE_VALUE;
CRITICAL_SECTION g_cs;
bool g_cs_ready = false;
}  // namespace

void LogInit(const wchar_t* path) {
    InitializeCriticalSection(&g_cs);
    g_cs_ready = true;

    if (path && path[0]) {
        // 追加打开：多次启动同一份日志，便于对比"这次和上次有什么不同"
        g_file = CreateFileW(path, FILE_APPEND_DATA,
                             FILE_SHARE_READ | FILE_SHARE_WRITE, nullptr,
                             OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (g_file == INVALID_HANDLE_VALUE) {
            g_file = INVALID_HANDLE_VALUE;  // 明确保持无效，后续只写调试器
        } else {
            SetFilePointer(g_file, 0, nullptr, FILE_END);
        }
    }
}

void LogShutdown() {
    if (g_cs_ready) {
        EnterCriticalSection(&g_cs);
        if (g_file != INVALID_HANDLE_VALUE) {
            CloseHandle(g_file);
            g_file = INVALID_HANDLE_VALUE;
        }
        LeaveCriticalSection(&g_cs);
        DeleteCriticalSection(&g_cs);
        g_cs_ready = false;
    }
}

void LogWrite(const char* fmt, ...) {
    char buf[1024];
    va_list ap;
    va_start(ap, fmt);
    int n = _vsnprintf_s(buf, sizeof(buf) - 3, _TRUNCATE, fmt, ap);
    va_end(ap);
    if (n < 0) n = static_cast<int>(strlen(buf));
    // 统一补 CRLF：记事本能直接看，不用另存
    buf[n] = '\r';
    buf[n + 1] = '\n';
    buf[n + 2] = '\0';

    if (g_cs_ready) EnterCriticalSection(&g_cs);

    OutputDebugStringA(buf);

    if (g_file != INVALID_HANDLE_VALUE) {
        DWORD wrote = 0;
        WriteFile(g_file, buf, static_cast<DWORD>(n + 2), &wrote, nullptr);
    }

    if (g_cs_ready) LeaveCriticalSection(&g_cs);
}

}  // namespace chs
