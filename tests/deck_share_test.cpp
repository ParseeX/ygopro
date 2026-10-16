#include "../gframe/deck_share.h"
#include "../gframe/deck_manager.h"
#include <cassert>
#include <iostream>

int main() {
	ygo::DeckArray deck{{89631139, 89631139, 55144522}, {23995346, 84013237}, {53129443}};
	const auto link = ygo::ExportDeckLink(deck, u8"青眼 &测试#1");
	// Payload verified against OURYGO's published v1Code JavaScript encoder.
	assert(link == "http://deck.ourygo.top/?ygotype=deck&v=1&d=AiGqvU0a0lwSktxHpNAfC1WVWHGA"
		"&name=%E9%9D%92%E7%9C%BC%20%26%E6%B5%8B%E8%AF%95%231");
	assert(ygo::ExportDeckLink({}, "") == "http://deck.ourygo.top/?ygotype=deck&v=1&d=AAA");
	deck.main.push_back(89631139);
	assert(ygo::ExportDeckLink(deck, "").find("&v=1&d=") != std::string::npos);
	deck.main.push_back(89631139);
	assert(ygo::ExportDeckLink(deck, "") == "http://deck.ourygo.top/?ygotype=deck&v=0"
		"&main=89631139*4_55144522&extra=23995346_84013237&side=53129443");
	deck = {};
	deck.main = {1u << 27};
	assert(ygo::ExportDeckLink(deck, "").find("&v=0&main=134217728") != std::string::npos);
	deck = {};
	for (uint32_t i = 1; i <= 16; ++i)
		deck.side.push_back(i);
	assert(ygo::ExportDeckLink(deck, "").find("&v=0&main=&extra=&side=1_2_3") != std::string::npos);
	deck.side.pop_back();
	assert(ygo::ExportDeckLink(deck, "").find("&v=1&d=") != std::string::npos);
	// Emit a range of bit lengths and duplicate layouts for an independent decoder.
	for (uint32_t count = 0; count <= 60; ++count) {
		deck = {};
		for (uint32_t i = 0; i < count; ++i)
			deck.main.push_back(10000000 + i / 3);
		for (uint32_t i = 0; i < count % 16; ++i) {
			deck.extra.push_back(20000000 + i);
			deck.side.push_back(30000000 + i);
		}
		std::cout << ygo::ExportDeckLink(deck, "") << '\n';
	}
}
