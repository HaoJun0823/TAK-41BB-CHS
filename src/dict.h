// dict.h —— 运行时翻译字典（英文 ⇄ 中文）
//
// 数据来自 CI 生成的 chs.dict：
//     第一行  # TAK CHS dict v1
//     其后每行  <英文键>\t<GBK 译文>
// 文件本身是 GBK 编码，DLL 只做字节比较，不做任何编码转换 ——
// 引擎拿到的也必须是指向常驻内存的 GBK 字节串，中途转码反而会引入
// 生命周期问题（返回值绝不能在栈上）。
#pragma once

#include <windows.h>

namespace chs {

class Dict {
public:
    Dict();
    ~Dict();

    // 加载字典。成功返回 true，条数写入 out_count。
    bool Load(const wchar_t* path, int* out_count);

    // 英文原文 → 中文译文；未命中返回 nullptr。
    // 先精确匹配（与引擎 strcmp 行为一致），再退回不区分大小写。
    const char* ToChinese(const char* en) const;

    // 中文译文 → 英文键；供 sub_544110 反查。
    // 一对多时返回第一条（同一英文在跨组里已由 unify_translations 统一）。
    const char* ToEnglish(const char* zh) const;

    bool loaded() const { return loaded_; }
    int count() const { return count_; }

private:
    // 开放寻址哈希表：容量取 2 的幂，装载因子 < 0.7，查找平均 O(1)。
    // 表项存的是 arena 内的指针，arena 整块释放，无需逐条 delete。
    struct Entry {
        const char* key;
        const char* val;
    };

    static unsigned Hash(const char* s);
    static unsigned HashLower(const char* s);   // 不分配，边算边转小写

    void Insert(Entry* table, unsigned mask, const char* key, const char* val);

    char* arena_ = nullptr;
    size_t arena_size_ = 0;

    Entry* exact_ = nullptr;    // 区分大小写
    Entry* lower_ = nullptr;    // 键已小写
    Entry* reverse_ = nullptr;  // 键 = 中文译文，值 = 英文键
    char* lower_arena_ = nullptr;

    unsigned exact_mask_ = 0;
    unsigned lower_mask_ = 0;
    unsigned reverse_mask_ = 0;
    int count_ = 0;
    bool loaded_ = false;
};

}  // namespace chs
