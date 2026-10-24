"""协议推进回归：python -B -m unittest discover -s tests -p replay_log_test.py。"""
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from replay_log.engine import replay_events, CardData, LegacyCardData, card_for_core
from replay_log.format import Reader, Replay, ReplayError
from replay_log.protocol import split_messages
from replay_log.timeline import Timeline


class FakeEngine:
    def __init__(self, steps):
        self.steps, self.responses = iter(steps), []

    def start(self, replay):
        pass

    def step(self):
        return next(self.steps)

    def respond(self, response):
        self.responses.append(response)

    def snapshot(self):
        return {}


class ReplayTests(unittest.TestCase):
    def run_events(self, steps, responses=(b"\x01\0\0\0",)):
        engine = FakeEngine(steps)
        replay = SimpleNamespace(responses=list(responses), response_offsets=list(range(len(responses))))
        result = list(replay_events(engine, replay))
        return result, engine

    def test_display_wait_does_not_consume_response(self):
        # ConfirmCards 后 PROCESSOR_WAIT 返回空 WAITING：旧实现会多消费一条。
        confirm = bytes([31, 0, 0, 1]) + struct.pack("<IBBB", 89631139, 0, 2, 0)
        yesno = bytes([13, 0]) + struct.pack("<I", 30)
        result, engine = self.run_events([(len(confirm), confirm), (0x10000000, b""),
                                         (0x10000006, yesno), (3, bytes([5, 0, 0]))])
        self.assertEqual(engine.responses, [b"\x01\0\0\0"])
        self.assertEqual(result[-1]["status"], "win")

    def test_retry_is_failure_not_next_response(self):
        yesno = bytes([13, 0]) + struct.pack("<I", 30)
        with self.assertRaisesRegex(ReplayError, "拒绝第 1 条响应"):
            self.run_events([(0x10000006, yesno), (0x10000001, b"\x01")])

    def test_unknown_message_never_resynchronizes(self):
        with self.assertRaisesRegex(ReplayError, "不支持的引擎消息"):
            list(split_messages(bytes([255, 40, 0])))

    def test_truncated_event(self):
        with self.assertRaises(ReplayError):
            list(split_messages(bytes([50]) + b"\0" * 15))

    def test_multiple_events_per_batch(self):
        raw = bytes([40, 1, 41, 4, 0, 90, 1, 1]) + struct.pack("<I", 89631139)
        self.assertEqual([m.kind for m in split_messages(raw)], [40, 41, 90])

    def test_end_of_responses_is_not_win(self):
        result, _ = self.run_events([(0x10000006, bytes([13, 0, 30, 0, 0, 0]))], responses=())
        self.assertEqual(result[-1]["status"], "responses_exhausted")

    def test_early_win_is_failure(self):
        with self.assertRaisesRegex(ReplayError, "尚余 1"):
            self.run_events([(3, bytes([5, 0, 0]))])

    def container(self, body_tail=b"\x04\x01\0\0\0", extended=False):
        names = b"\0" * 80
        body = names + struct.pack("<iiiI4I", 8000, 5, 1, 0, 0, 0, 0, 0) + body_tail
        header = struct.pack("<6I8s", 0x32707279 if extended else 0x31707279,
                             0x1362, 0x10, 123, len(body), 0, b"\0" * 8)
        if extended:
            header += struct.pack("<12I", *range(8), 1, 0, 0, 0)
        return header + body

    def read_container(self, data):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "test.yrp"
            path.write_bytes(data)
            return Replay.read(path)

    def test_yrp1_uses_32_byte_header(self):
        r = self.read_container(self.container())
        self.assertEqual(r.seed, 123)
        self.assertEqual(r.seeds, ())
        self.assertEqual(r.responses, [b"\x01\0\0\0"])

    def test_yrp2_uses_extended_seed(self):
        r = self.read_container(self.container(extended=True))
        self.assertEqual(r.seeds, tuple(range(8)))
        self.assertEqual(r.responses, [b"\x01\0\0\0"])

    def test_truncated_response_is_not_eof(self):
        with self.assertRaisesRegex(ReplayError, "截断"):
            self.read_container(self.container(b"\x04\x01"))

    def test_zero_length_response_is_not_eof(self):
        with self.assertRaisesRegex(ReplayError, "长度为零"):
            self.read_container(self.container(b"\0"))

    def test_wrong_magic_is_rejected(self):
        with self.assertRaises(ReplayError):
            self.read_container(b"xxxx" + self.container()[4:])

    def test_unknown_extended_header_is_rejected(self):
        data = bytearray(self.container(extended=True))
        struct.pack_into("<I", data, 64, 2)
        with self.assertRaisesRegex(ReplayError, "扩展头"):
            self.read_container(data)

    def test_legacy_zero_extended_header_is_supported(self):
        data = bytearray(self.container(extended=True))
        struct.pack_into("<I", data, 64, 0)
        replay = self.read_container(data)
        self.assertEqual(replay.seeds, tuple(range(8)))
        self.assertEqual(replay.responses, [b"\x01\0\0\0"])

    def timeline(self):
        replay = SimpleNamespace(players=["甲", "乙"], params=(8000, 5, 1, 0))
        content = SimpleNamespace(name=lambda n: f"卡{n}", description=lambda n: f"效果{n}")
        return Timeline(replay, content)

    def test_chain_attribution_and_resolution_order(self):
        t = self.timeline()
        for number, player, code in ((1, 0, 111), (2, 1, 222)):
            raw = bytes([70]) + struct.pack("<IBBBBBBBIB", code, player, 8, 0, 1,
                                           player, 8, 0, 0, number)
            text = t.message(next(split_messages(raw)))[0]
            self.assertIn(f"连锁 {number}", text)
            self.assertIn("甲" if player == 0 else "乙", text)
        result = []
        for m in split_messages(bytes([72, 2, 75, 1, 73, 2, 72, 1, 73, 1, 74])):
            result += t.message(m)
        self.assertIn("卡222", result[0])
        self.assertIn("发动被无效", result[1])
        self.assertEqual(t.chains, {})

    def test_unknown_chain_fails(self):
        with self.assertRaisesRegex(ReplayError, "不存在的连锁"):
            self.timeline().message(next(split_messages(bytes([72, 2]))))

    def test_hand_indices_shift_after_move(self):
        t = self.timeline()
        t.sync({"0:2": [[111, 0], [222, 0], [333, 0]], "0:16": []})
        raw = bytes([50]) + struct.pack("<I4B4BI", 111, 0, 2, 0, 0, 0, 16, 0, 1, 0x80)
        t.message(next(split_messages(raw)))
        self.assertEqual(t.code_at((0, 2, 0)), 222)
        self.assertEqual(t.code_at((0, 16, 0)), 111)

    def test_shuffle_replaces_hand_identity(self):
        t = self.timeline()
        t.sync({"0:2": [[111, 0], [222, 0]]})
        t.message(next(split_messages(bytes([33, 0, 2]) + struct.pack("<II", 222, 111))))
        self.assertEqual(t.code_at((0, 2, 0)), 222)

    def test_spell_positions_are_not_unknown(self):
        t = self.timeline()
        raw = bytes([53]) + struct.pack("<IBBBBB", 111, 0, 8, 0, 10, 5)
        text = t.message(next(split_messages(raw)))[0]
        self.assertIn("里侧 → 表侧", text)

    def test_hint_recipient_is_not_reported_as_actor(self):
        t = self.timeline()
        raw = bytes([2, 4, 1]) + struct.pack("<I", 100)
        text = t.message(next(split_messages(raw)))[0]
        self.assertEqual(text, "选项提示：效果100")

    def test_overlay_parent_can_still_be_in_extra_deck(self):
        t = self.timeline()
        self.assertIn("额外卡组第10位", t.place((0, 64 | 128, 9, 0)))
        self.assertNotIn("怪兽区", t.place((0, 64 | 128, 9, 0)))

    def test_sort_cancel_accepts_integer_response(self):
        t = self.timeline()
        request = next(split_messages(bytes([25, 0, 1]) + struct.pack("<IBBB", 111, 0, 1, 0)))
        self.assertIn("不调整顺序", t.response(request, b"\xff\xff\xff\xff")[0])

    def test_legacy_core_preserves_rule_alias(self):
        card = CardData()
        card.code, card.rule_code, card.alias = 5405695, 89631139, 0
        card.type, card.attack = 33, 3000
        old = card_for_core(card, LegacyCardData)
        self.assertEqual(old.alias, 89631139)
        self.assertEqual(old.attack, 3000)
        self.assertEqual(card_for_core(card, CardData).alias, 0)


if __name__ == "__main__":
    unittest.main()
