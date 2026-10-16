#include "../gframe/data_manager.h"
#include <cassert>
#include <type_traits>

template<typename T>
auto CheckCoreCompatibility(T core, int) -> decltype(core.rule_code, void()) {
	assert(core.rule_code == 0);
	ygo::PrepareCardDataForCore(core);
	assert(core.get_original_code() == 89631139);
	assert(core.get_duel_code() == 89631139);

	core.code = 76812113;
	core.alias = 6983839;
	ygo::PrepareCardDataForCore(core);
	assert(core.get_original_code() == 76812113);
	assert(core.get_duel_code() == 6983839);
	core.code = 5405695;
	core.alias = 5405694;
	ygo::PrepareCardDataForCore(core);
	assert(core.get_original_code() == 5405695);
	assert(core.get_duel_code() == 5405694);
	core.code = 89631139;
	core.alias = 0;
	ygo::PrepareCardDataForCore(core);
	assert(core.rule_code == 0);
	assert(core.get_original_code() == 89631139);
	assert(core.get_duel_code() == 89631139);
}

template<typename T>
void CheckCoreCompatibility(T core, long) {
	ygo::PrepareCardDataForCore(core);
	assert(core.alias == 89631139);
	assert(core.get_original_code() == 89631139);
	core.code = 76812113;
	core.alias = 6983839;
	ygo::PrepareCardDataForCore(core);
	assert(core.alias == 6983839);
	assert(core.get_original_code() == 76812113);
}

int main() {
	using namespace ygo;
	static_assert(std::is_base_of<CardData, CardDataC>::value,
		"Client card data must share the core's current layout");
	assert(ygo::is_alternative(89631140, 89631139));
	assert(ygo::is_alternative(89631139, 89631140));
	assert(ygo::is_alternative(100, 119));
	assert(!ygo::is_alternative(100, 120));
	assert(!ygo::is_alternative(120, 100));
	assert(!ygo::is_alternative(89631139, 0));
	assert(!ygo::is_alternative(5405695, 5405694));

	CardDataC client;
	client.code = 89631140;
	client.alias = 89631139;
	client.type = 1;
	client.attack = 3000;
	client.defense = 2500;
	client.setcode[0] = 0xdd;
	client.ot = 3;
	client.category = 0xffffffffu;
	CardData core = static_cast<const CardData&>(client);
	assert(core.code == client.code && core.alias == client.alias);
	assert(core.attack == 3000 && core.defense == 2500);
	assert(core.setcode[0] == 0xdd);
	CheckCoreCompatibility(core, 0);
	assert(client.alias == 89631139 && client.ot == 3);

	struct LegacyCardData { uint32_t code; uint32_t alias; } legacy{76812113, 6983839};
	detail::PrepareCardRuleCode(legacy, 0);
	assert(legacy.code == 76812113 && legacy.alias == 6983839);
}
