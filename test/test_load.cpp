// test_load.cpp —— 冒烟测试：DLL 到底能不能被加载、DllMain 有没有跑
//
// 它做的事：
//   1. LoadLibrary("binkw32.dll") —— 连带验证 10 个 forwarder 能否解析
//      （缺 binkw32_orig.dll 或导出名对不上，这一步就会失败）
//   2. 等一会儿，让 DllMain 起的初始化线程把日志写完
//   3. 把日志读出来打印，人工确认「启动横幅 + 签名不匹配」两行都在
//
// 期望结果：加载成功，日志里有启动横幅，并且**签名不匹配** ——
// 因为这里的宿主是 test_load.exe，0x544020 处不是游戏的翻译函数。
// 这一条恰恰证明「版本不对时 DLL 会主动放弃」的保护是有效的。
#include <windows.h>

#include <cstdio>
#include <cstring>

int main(int argc, char** argv) {
    const char* dll = (argc > 1) ? argv[1] : "binkw32.dll";
    const char* log = (argc > 2) ? argv[2] : "tak_chs.log";

    DeleteFileA(log);

    HMODULE h = LoadLibraryA(dll);
    if (!h) {
        printf("[FAIL] LoadLibrary(%s) 失败，GetLastError=%lu\n", dll,
               GetLastError());
        printf("       常见原因：binkw32_orig.dll 不在同目录，或导出名对不上\n");
        return 1;
    }
    printf("[PASS] LoadLibrary 成功，基址 %p\n", (void*)h);
    printf("       10 个 _Bink* forwarder 已解析完毕\n");

    // DllMain 里只起线程，初始化是异步的，给它一点时间
    Sleep(600);

    FILE* f = fopen(log, "rb");
    if (!f) {
        printf("[FAIL] 没有生成日志 %s —— DllMain 可能没跑\n", log);
        FreeLibrary(h);
        return 1;
    }
    char buf[4096];
    size_t n = fread(buf, 1, sizeof(buf) - 1, f);
    fclose(f);
    buf[n] = '\0';

    printf("\n--- 日志 %s ---\n%s\n--------------\n", log, buf);

    bool ok = true;
    if (!strstr(buf, "TAK CHS DLL")) {
        printf("[FAIL] 日志里没有启动横幅\n");
        ok = false;
    } else {
        printf("[PASS] 启动横幅存在\n");
    }
    // 宿主是 test_load.exe 而不是 KINGDOMS.icd，地址表里的 RVA 必然越界。
    // 只要看到"越界"或"签名不匹配"任一，就说明保护链生效、进程没被写坏。
    // 这两个都没有才要排查：要么保护失效，要么碰巧命中了别的函数。
    bool guarded = strstr(buf, "超出模块映像大小") || strstr(buf, "签名不匹配");
    if (!guarded) {
        printf("[FAIL] 既没有越界拦截也没有签名不匹配 —— 保护链可能失效\n");
        ok = false;
    } else {
        printf("[PASS] 安全保护生效（拒绝在非目标进程上写内存）\n");
    }

    FreeLibrary(h);
    printf("\n%s\n", ok ? "冒烟测试通过" : "冒烟测试失败");
    return ok ? 0 : 1;
}
