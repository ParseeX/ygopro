"""逐条切分 ocgcore 消息；长度参照 ReplayMode::ReplayAnalyze 和核心写入端。"""
from dataclasses import dataclass
from .format import Reader, ReplayError

NAMES = {
    1: "RETRY", 2: "HINT", 5: "WIN", 10: "SELECT_BATTLECMD", 11: "SELECT_IDLECMD",
    12: "SELECT_EFFECTYN", 13: "SELECT_YESNO", 14: "SELECT_OPTION", 15: "SELECT_CARD",
    16: "SELECT_CHAIN", 18: "SELECT_PLACE", 19: "SELECT_POSITION", 20: "SELECT_TRIBUTE",
    22: "SELECT_COUNTER", 23: "SELECT_SUM", 24: "SELECT_DISFIELD", 25: "SORT_CARD",
    26: "SELECT_UNSELECT_CARD", 30: "CONFIRM_DECKTOP", 31: "CONFIRM_CARDS",
    32: "SHUFFLE_DECK", 33: "SHUFFLE_HAND", 34: "REFRESH_DECK", 35: "SWAP_GRAVE_DECK",
    36: "SHUFFLE_SET_CARD", 37: "REVERSE_DECK", 38: "DECK_TOP", 39: "SHUFFLE_EXTRA",
    40: "NEW_TURN", 41: "NEW_PHASE", 42: "CONFIRM_EXTRATOP", 50: "MOVE",
    53: "POS_CHANGE", 54: "SET", 55: "SWAP", 56: "FIELD_DISABLED", 60: "SUMMONING",
    61: "SUMMONED", 62: "SPSUMMONING", 63: "SPSUMMONED", 64: "FLIPSUMMONING",
    65: "FLIPSUMMONED", 70: "CHAINING", 71: "CHAINED", 72: "CHAIN_SOLVING",
    73: "CHAIN_SOLVED", 74: "CHAIN_END", 75: "CHAIN_NEGATED", 76: "CHAIN_DISABLED",
    80: "CARD_SELECTED", 81: "RANDOM_SELECTED", 83: "BECOME_TARGET", 90: "DRAW",
    91: "DAMAGE", 92: "RECOVER", 93: "EQUIP", 94: "LPUPDATE", 95: "UNEQUIP",
    96: "CARD_TARGET", 97: "CANCEL_TARGET", 100: "PAY_LPCOST", 101: "ADD_COUNTER",
    102: "REMOVE_COUNTER", 110: "ATTACK", 111: "BATTLE", 112: "ATTACK_DISABLED",
    113: "DAMAGE_STEP_START", 114: "DAMAGE_STEP_END", 120: "MISSED_EFFECT",
    130: "TOSS_COIN", 131: "TOSS_DICE", 132: "ROCK_PAPER_SCISSORS", 133: "HAND_RES",
    140: "ANNOUNCE_RACE", 141: "ANNOUNCE_ATTRIB", 142: "ANNOUNCE_CARD",
    143: "ANNOUNCE_NUMBER", 160: "CARD_HINT", 161: "TAG_SWAP", 162: "RELOAD_FIELD",
    163: "AI_NAME", 164: "SHOW_HINT", 165: "PLAYER_HINT", 170: "MATCH_KILL",
}
REQUESTS = frozenset([10, 11, 12, 13, 14, 15, 16, 18, 19, 20, 22, 23, 24, 25, 26,
                      132, 140, 141, 142, 143])
FIXED = {
    1: 0, 2: 6, 5: 2, 12: 13, 13: 5, 18: 6, 19: 6, 24: 6,
    32: 1, 34: 1, 35: 1, 37: 0, 38: 6, 40: 1, 41: 2,
    50: 16, 53: 9, 54: 8, 55: 16, 56: 4, 60: 8, 61: 0, 62: 8, 63: 0,
    64: 8, 65: 0, 70: 16, 71: 1, 72: 1, 73: 1, 74: 0, 75: 1, 76: 1,
    91: 5, 92: 5, 93: 8, 94: 5, 95: 4, 96: 8, 97: 8, 100: 5,
    101: 7, 102: 7, 110: 8, 111: 26, 112: 0, 113: 0, 114: 0, 120: 8,
    132: 1, 133: 1, 140: 6, 141: 6, 160: 9, 165: 6, 170: 4,
}


@dataclass
class Message:
    kind: int
    data: bytes
    offset: int

    @property
    def name(self):
        return NAMES[self.kind]

    @property
    def is_request(self):
        return self.kind in REQUESTS


def split_messages(data):
    r = Reader(data, "引擎消息")

    def array(width):
        r.take(r.u8() * width)

    while r.remaining:
        offset = r.pos
        kind = r.u8()
        if kind in FIXED:
            r.take(FIXED[kind])
        elif kind == 10:
            r.take(1)
            array(11)
            array(8)
            r.take(2)
        elif kind == 11:
            r.take(1)
            for _ in range(5):
                array(7)
            array(11)
            r.take(3)
        elif kind in (14, 142, 143):
            r.take(1)
            array(4)
        elif kind in (15, 20):
            r.take(4)
            array(8)
        elif kind == 16:
            r.take(1)
            count = r.u8()
            r.take(9 + count * 14)
        elif kind == 22:
            r.take(5)
            array(9)
        elif kind == 23:
            r.take(8)
            array(11)
            array(11)
        elif kind == 26:
            r.take(5)
            array(8)
            array(8)
        elif kind in (25, 30, 42):
            r.take(1)
            array(7)
        elif kind == 31:
            r.take(2)
            array(7)
        elif kind in (33, 39, 80, 81, 90):
            r.take(1)
            array(4)
        elif kind == 36:
            r.take(1)
            array(8)
        elif kind == 83:
            array(4)
        elif kind in (130, 131):
            r.take(1)
            array(1)
        elif kind in (163, 164):
            r.take(r.unpack("H")[0] + 1)
        else:
            # 单人/TAG 已在容器层拒绝；未知消息不能“跳过一字节继续”。
            raise ReplayError(f"不支持的引擎消息 {kind}，缓冲区偏移 {offset}")
        yield Message(kind, r.data[offset + 1:r.pos], offset)
