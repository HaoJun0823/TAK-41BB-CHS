// dict.cpp
#include "dict.h"

#include <cstdlib>
#include <cstring>

#include "log.h"

namespace chs {

namespace {

// FNV-1a：实现短、分布好、无查表开销，足够 4000 条规模
inline unsigned Fnv(const unsigned char* p, bool lower) {
    unsigned h = 2166136261u;
    for (; *p; ++p) {
        unsigned char c = *p;
        if (lower && c >= 'A' && c <= 'Z') c = static_cast<unsigned char>(c + 32);
        h ^= c;
        h *= 16777619u;
    }
    return h;
}

// 表容量：取 >= 2*n 的 2 的幂，装载因子 <= 0.5，线性探测不易聚簇
unsigned CapFor(int n) {
    unsigned cap = 16;
    while (cap < static_cast<unsigned>(n) * 2 && cap < (1u << 22)) cap <<= 1;
    return cap;
}

}  // namespace

Dict::Dict() {}
Dict::~Dict() {
    free(arena_);
    free(lower_arena_);
    free(exact_);
    free(lower_);
    free(reverse_);
}

unsigned Dict::Hash(const char* s) {
    return Fnv(reinterpret_cast<const unsigned char*>(s), false);
}

unsigned Dict::HashLower(const char* s) {
    return Fnv(reinterpret_cast<const unsigned char*>(s), true);
}

void Dict::Insert(Entry* table, unsigned mask, const char* key, const char* val) {
    unsigned i = Hash(key) & mask;   // 注意：lower_ 表也用同一个 Hash，
                                     // 因为 key 本身已经小写，两者等价
    while (table[i].key) i = (i + 1) & mask;
    table[i].key = key;
    table[i].val = val;
}

bool Dict::Load(const wchar_t* path, int* out_count) {
    if (out_count) *out_count = 0;

    HANDLE h = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, nullptr,
                           OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (h == INVALID_HANDLE_VALUE) {
        CHS_LOGE("dict: 打不开 %ls（err=%lu）", path, GetLastError());
        return false;
    }
    DWORD size = GetFileSize(h, nullptr);
    if (size == 0 || size == INVALID_FILE_SIZE || size > (64u << 20)) {
        CHS_LOGE("dict: 文件大小异常 %lu", size);
        CloseHandle(h);
        return false;
    }

    // 整块读入：多读 2 字节，方便在末尾补 "\n\0" 而不越界
    arena_ = static_cast<char*>(malloc(size + 2));
    if (!arena_) {
        CHS_LOGE("dict: 内存不足 %lu 字节", size);
        CloseHandle(h);
        return false;
    }
    DWORD got = 0;
    if (!ReadFile(h, arena_, size, &got, nullptr) || got != size) {
        CHS_LOGE("dict: 读取不完整 %lu/%lu", got, size);
        CloseHandle(h);
        free(arena_);
        arena_ = nullptr;
        return false;
    }
    CloseHandle(h);
    arena_[size] = '\n';
    arena_[size + 1] = '\0';
    arena_size_ = size + 2;

    // 先数一行数，再分配表：避免边插入边 rehash
    int lines = 0;
    for (size_t i = 0; i < size; ++i) {
        if (arena_[i] == '\n') ++lines;
    }
    if (lines <= 1) {
        CHS_LOGE("dict: 只有 %d 行，不是有效字典", lines);
        return false;
    }

    unsigned cap = CapFor(lines);
    exact_ = static_cast<Entry*>(calloc(cap, sizeof(Entry)));
    lower_ = static_cast<Entry*>(calloc(cap, sizeof(Entry)));
    reverse_ = static_cast<Entry*>(calloc(cap, sizeof(Entry)));
    lower_arena_ = static_cast<char*>(malloc(size + 1));
    if (!exact_ || !lower_ || !reverse_ || !lower_arena_) {
        CHS_LOGE("dict: 表分配失败（cap=%u）", cap);
        return false;
    }
    exact_mask_ = cap - 1;
    lower_mask_ = cap - 1;
    reverse_mask_ = cap - 1;

    char* lower_cursor = lower_arena_;
    char* line = arena_;
    char* end = arena_ + size;
    int parsed = 0;

    while (line < end) {
        char* nl = line;
        while (nl < end && *nl != '\n') ++nl;
        *nl = '\0';                       // 行尾切断（overlap 写入是安全的：
                                          // nl 处原本就是 '\n'）

        char* p = line;
        if (*p == '#' || *p == '\0') {    // 注释/空行
            line = nl + 1;
            continue;
        }
        char* tab = p;
        while (*tab && *tab != '\t') ++tab;
        if (*tab != '\t') {               // 没有 TAB 的行直接跳过，不猜
            line = nl + 1;
            continue;
        }
        *tab = '\0';
        char* key = p;
        char* val = tab + 1;
        if (!*key || !*val) {
            line = nl + 1;
            continue;
        }

        Insert(exact_, exact_mask_, key, val);
        // 反向表：键 = 中文，值 = 英文键。sub_544110 靠它把译文查回标识，
        // 存档与联机协议里才不会混进中文。
        Insert(reverse_, reverse_mask_, val, key);

        // 小写副本：引擎有 _strlwr 的调用方，大小写不能只靠精确表
        size_t klen = strlen(key);
        if (lower_cursor + klen + 1 <= lower_arena_ + size) {
            char* lk = lower_cursor;
            for (size_t i = 0; i < klen; ++i) {
                char c = key[i];
                lk[i] = (c >= 'A' && c <= 'Z') ? static_cast<char>(c + 32) : c;
            }
            lk[klen] = '\0';
            lower_cursor += klen + 1;
            Insert(lower_, lower_mask_, lk, val);
        }

        ++parsed;
        line = nl + 1;
    }

    count_ = parsed;
    loaded_ = parsed > 0;
    if (out_count) *out_count = count_;
    if (!loaded_) CHS_LOGE("dict: 解析出 0 条");
    return loaded_;
}

const char* Dict::ToChinese(const char* en) const {
    if (!loaded_ || !en || !*en) return nullptr;

    unsigned i = Hash(en) & exact_mask_;
    for (unsigned probe = 0; probe <= exact_mask_; ++probe) {
        const Entry& e = exact_[i];
        if (!e.key) return nullptr;
        if (e.key[0] == en[0] && strcmp(e.key, en) == 0) return e.val;
        i = (i + 1) & exact_mask_;
    }

    // 精确未命中再走小写表：sub_4C7740 会把串先 _strlwr 再查
    unsigned j = HashLower(en) & lower_mask_;
    for (unsigned probe = 0; probe <= lower_mask_; ++probe) {
        const Entry& e = lower_[j];
        if (!e.key) return nullptr;
        if (e.key[0] == (en[0] >= 'A' && en[0] <= 'Z'
                             ? static_cast<char>(en[0] + 32) : en[0])) {
            // 表内已是小写，比对时把入参也降一档，避免每次分配临时串
            const char* a = e.key;
            const char* b = en;
            while (*a && *b) {
                char cb = (*b >= 'A' && *b <= 'Z') ? static_cast<char>(*b + 32) : *b;
                if (*a != cb) break;
                ++a; ++b;
            }
            if (*a == '\0' && *b == '\0') return e.val;
        }
        j = (j + 1) & lower_mask_;
    }
    return nullptr;
}

const char* Dict::ToEnglish(const char* zh) const {
    if (!loaded_ || !zh || !*zh) return nullptr;
    // 键是 GBK 中文，没有大小写概念，直接精确查 reverse_ 表
    unsigned i = Hash(zh) & reverse_mask_;
    for (unsigned probe = 0; probe <= reverse_mask_; ++probe) {
        const Entry& e = reverse_[i];
        if (!e.key) return nullptr;
        if (e.key[0] == zh[0] && strcmp(e.key, zh) == 0) return e.val;
        i = (i + 1) & reverse_mask_;
    }
    return nullptr;
}

}  // namespace chs
