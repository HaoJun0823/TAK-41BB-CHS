// vtest.cpp —— 代理 DLL 加载验证（32 位）
//
// 目的：判断「应用目录里放 version.dll」到底会不会被优先加载。
// 如果这里打印的路径是游戏目录 → 代理生效，问题在代理本身；
// 如果打印 SysWOW64 → version.dll 被系统列表锁死，换宿主。
#include <windows.h>
#include <stdio.h>

int main(void) {
    HMODULE h = LoadLibraryA("version.dll");
    char path[MAX_PATH] = {0};
    if (h) GetModuleFileNameA(h, path, MAX_PATH);
    printf("LoadLibraryA(version.dll) = %p\n  -> %s\n", (void*)h, path);

    HMODULE h2 = GetModuleHandleA("version.dll");
    char path2[MAX_PATH] = {0};
    if (h2) GetModuleFileNameA(h2, path2, MAX_PATH);
    printf("GetModuleHandleA         = %p\n  -> %s\n", (void*)h2, path2);

    FARPROC p = GetProcAddress(h, "VerQueryValueA");
    printf("VerQueryValueA           = %p\n", (void*)p);

    DWORD dummy = 0;
    DWORD sz = GetFileVersionInfoSizeA("C:\\Windows\\notepad.exe", &dummy);
    printf("GetFileVersionInfoSizeA(notepad.exe) = %lu (err=%lu)\n", sz,
           GetLastError());
    return 0;
}
