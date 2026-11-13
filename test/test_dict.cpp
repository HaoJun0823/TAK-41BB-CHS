// test_dict.cpp —— 离线验证字典加载与三条查找路径
//
// 不需要游戏、不需要注入，直接编译成 exe 跑：
//     cl /nologo /MT /O2 /utf-8 /EHsc test\test_dict.cpp src\dict.cpp src\log.cpp
// 输出是 GBK，控制台看到乱码时用 `chcp 936` 或重定向到文件再看。
//
// 覆盖的用例对应引擎的真实调用方式：
//   1. 精确命中      —— sub_544020 内部就是 strcmp
//   2. 大小写命中    —— sub_4C7740 会先 _strlwr 再查
//   3. 反查          —— sub_544110 用 value 找 key
//   4. 未命中返回空  —— 必须让调用方继续用原文，而不是拿到野指针
#include <cstdio>
#include <cstring>

#include "../src/dict.h"

static int g_fail = 0;

static void ExpectStr(const char* what, const char* got, const char* want) {
    bool ok = (got && want) ? strcmp(got, want) == 0 : (got == want);
    printf("[%s] %-34s ", ok ? "PASS" : "FAIL", what);
    if (got) {
        printf("-> %s", got);
    } else {
        printf("-> (null)");
    }
    printf("\n");
    if (!ok) ++g_fail;
}

static void ExpectNull(const char* what, const char* got) {
    bool ok = (got == nullptr);
    printf("[%s] %-34s -> %s\n", ok ? "PASS" : "FAIL", what,
           got ? got : "(null)");
    if (!ok) ++g_fail;
}

int main(int argc, char** argv) {
    const wchar_t* path = L"build_out\\chs.dict";
    if (argc > 1) {
        static wchar_t buf[512];
        size_t n = strlen(argv[1]);
        if (n >= 510) return 2;
        for (size_t i = 0; i <= n; ++i) buf[i] = (wchar_t)(unsigned char)argv[1][i];
        path = buf;
    }

    chs::Dict d;
    int count = 0;
    if (!d.Load(path, &count)) {
        printf("FAIL: 字典加载失败\n");
        return 1;
    }
    printf("载入 %d 条\n\n", count);

    // 1) 精确命中
    ExpectStr("精确: Close Ranks", d.ToChinese("Close Ranks"), "收拢阵型");

    // 2) 大小写：引擎有 _strlwr 的路径，必须也能查到
    const char* lower = d.ToChinese("close ranks");
    ExpectStr("小写: close ranks", lower, "收拢阵型");

    // 3) 反查：中文 → 英文键
    const char* back = d.ToEnglish("收拢阵型");
    ExpectStr("反查: 收拢阵型", back, "Close Ranks");

    // 4) 未命中必须返回 nullptr，让调用方保留原文
    ExpectNull("未命中: 不存在的串", d.ToChinese("This String Does Not Exist"));
    ExpectNull("未命中: 空串", d.ToChinese(""));
    ExpectNull("未命中: nullptr", d.ToChinese(nullptr));

    // 5) 键就是引擎传入的串。对 TDF 词条来说 = 块名（不是 MESG_* 那种 ID），
    //    所以随便取一条英文原文来验证「键与译文都取自同一份表」。
    const char* by_key = d.ToChinese("Executioner Corpse");
    printf("[%s] %-34s -> %s\n", by_key ? "PASS" : "FAIL", "块名键: Executioner Corpse",
           by_key ? by_key : "(null)");
    if (!by_key) ++g_fail;

    // 6) 引擎传入的内部 ID 不在字典里 —— 属正常，不应崩、不应乱返回
    ExpectNull("内部 ID 不在表: MESG_BOX_OK", d.ToChinese("MESG_BOX_OK"));

    printf("\n%s（%d 项失败）\n", g_fail ? "测试未通过" : "全部通过", g_fail);
    return g_fail ? 1 : 0;
}
