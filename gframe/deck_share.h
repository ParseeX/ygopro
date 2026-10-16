#ifndef DECK_SHARE_H
#define DECK_SHARE_H

#include <string>

namespace ygo {
struct DeckArray;
std::string ExportDeckLink(const DeckArray& deck, const std::string& name);
}

#endif // DECK_SHARE_H
