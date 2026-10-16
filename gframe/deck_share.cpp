#include "deck_share.h"
#include "deck_manager.h"
#include <array>

namespace ygo {
namespace {
using CardCounts = std::vector<std::pair<uint32_t, size_t>>;

CardCounts CountCards(const std::vector<uint32_t>& cards) {
	CardCounts counts;
	std::unordered_map<uint32_t, size_t> indices;
	for (auto code : cards) {
		auto inserted = indices.emplace(code, counts.size());
		if (inserted.second)
			counts.emplace_back(code, 1);
		else
			++counts[inserted.first->second].second;
	}
	return counts;
}

std::string EncodeQueryValue(const std::string& value) {
	const char* hex = "0123456789ABCDEF";
	std::string encoded;
	for (unsigned char c : value) {
		if ((c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z')
			|| (c >= '0' && c <= '9') || c == '-' || c == '_' || c == '.' || c == '~')
			encoded += c;
		else {
			encoded += '%';
			encoded += hex[c >> 4];
			encoded += hex[c & 15];
		}
	}
	return encoded;
}
}

std::string ExportDeckLink(const DeckArray& deck, const std::string& name) {
	const std::array<CardCounts, 3> sections{{CountCards(deck.main), CountCards(deck.extra), CountCards(deck.side)}};
	bool compact = sections[0].size() <= 255 && sections[1].size() <= 15 && sections[2].size() <= 15;
	for (const auto& section : sections) {
		for (const auto& card : section)
			compact = compact && card.first < (1u << 27) && card.second <= 3;
	}
	// OURYGO's viewer supports v1 (8/4/4-bit counts, 2+27-bit cards) and v0.
	// http://deck.ourygo.top/static/js/ygoUtil.js?v=1
	std::string url = "http://deck.ourygo.top/?ygotype=deck";
	if (compact) {
		std::string bits;
		auto append = [&bits](uint32_t value, int width) {
			for (int i = width - 1; i >= 0; --i)
				bits += ((value >> i) & 1) ? '1' : '0';
		};
		append(static_cast<uint32_t>(sections[0].size()), 8);
		append(static_cast<uint32_t>(sections[1].size()), 4);
		append(static_cast<uint32_t>(sections[2].size()), 4);
		for (const auto& section : sections) {
			for (const auto& card : section) {
				append(static_cast<uint32_t>(card.second), 2);
				append(card.first, 27);
			}
		}
		bits.append((8 - bits.size() % 8) % 8, '0');
		const char* alphabet = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_";
		url += "&v=1&d=";
		for (size_t i = 0; i < bits.size(); i += 6) {
			unsigned int value = 0;
			for (size_t j = 0; j < 6; ++j)
				value = (value << 1) | (i + j < bits.size() && bits[i + j] == '1');
			url += alphabet[value];
		}
	} else {
		url += "&v=0";
		const char* names[] = {"main", "extra", "side"};
		for (size_t i = 0; i < sections.size(); ++i) {
			url += std::string("&") + names[i] + "=";
			bool first = true;
			for (const auto& card : sections[i]) {
				if (!first)
					url += '_';
				first = false;
				url += std::to_string(card.first);
				if (card.second > 1)
					url += '*' + std::to_string(card.second);
			}
		}
	}
	if (!name.empty())
		url += "&name=" + EncodeQueryValue(name);
	return url;
}
}
