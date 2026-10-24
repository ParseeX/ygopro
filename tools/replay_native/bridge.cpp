#include <random>
#include "ocgapi.h"
#include "card_data.h"

// 让 Python 在调用前核验 ABI，避免混用布局不同的 DLL。
// 新版核心的 OCGCORE_API 只声明导出属性，桥接函数需显式保留 C ABI。
extern "C" {
OCGCORE_API uint32_t replay_card_data_size() { return sizeof(card_data); }
OCGCORE_API uint32_t replay_response_size() { return SIZE_RETURN_VALUE; }
OCGCORE_API intptr_t replay_create_yrp1(uint32_t seed) {
    // 与 ReplayMode::StartDuel 完全相同，不能直接把头部 seed 交给核心。
    std::mt19937 random(seed);
    return create_duel(random());
}
}
