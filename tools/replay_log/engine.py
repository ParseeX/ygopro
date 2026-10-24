"""隔离的无界面核心调用。仅在明确选择请求时消费一条录像响应。"""
import ctypes as C
from contextlib import closing
import hashlib
from pathlib import Path
import re
import sqlite3
from .format import ReplayError, Reader
from .protocol import split_messages


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


class CardData(C.Structure):
    _fields_ = [("code", C.c_uint32), ("alias", C.c_uint32), ("setcode", C.c_uint16 * 16)] + [
        (name, C.c_int32 if name in ("attack", "defense") else C.c_uint32)
        for name in ("type", "level", "attribute", "race", "attack", "defense", "lscale",
                     "rscale", "link_marker", "rule_code")]


class LegacyCardData(C.Structure):
    _fields_ = CardData._fields_[:-1]


def card_for_core(data, card_type):
    if card_type is CardData:
        return data
    legacy = LegacyCardData.from_buffer_copy(bytes(data)[:C.sizeof(LegacyCardData)])
    # 旧核心用 alias 同时表示异画和规则同名，新核心拆出了 rule_code。
    legacy.alias = data.rule_code
    return legacy


class Content:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.cards, self.names, self.descriptions, self.scripts = {}, {}, {}, {}
        self.databases = {}
        self.system_strings = {}
        configuration = self.root / "system.conf"
        settings = configuration.read_text(encoding="utf-8-sig") if configuration.exists() else ""
        preference = re.search(r"(?m)^prefer_expansion_script\s*=\s*(\d+)", settings)
        self.prefer_expansion = bool(preference and int(preference[1]))
        files = [self.root / "cards.cdb"] + sorted((self.root / "expansions").glob("*.cdb"))
        for path in files:
            with closing(sqlite3.connect(path.as_uri() + "?mode=ro", uri=True)) as db:
                for row in db.execute("SELECT id,alias,setcode,type,atk,def,level,race,attribute FROM datas"):
                    code, alias, setcode, typ, atk, defense, level, race, attr = row
                    d = CardData()
                    d.code, d.rule_code = code, alias
                    d.alias = alias if code != 5405695 and alias and abs(code - alias) < 20 else 0
                    parts = [(setcode & ((1 << 64) - 1)) >> (16 * i) & 0xffff for i in range(4)]
                    parts = [part for part in parts if part]
                    if code in (8512558, 55088578):
                        parts = [0x8f, 0x54, 0x59, 0x82, 0x13a]
                    for i, part in enumerate(parts):
                        d.setcode[i] = part
                    d.type, d.attack = typ, atk
                    d.defense = 0 if typ & 0x4000000 else defense
                    d.link_marker = defense if typ & 0x4000000 else 0
                    d.level, d.lscale, d.rscale = level & 255, level >> 24 & 255, level >> 16 & 255
                    d.race, d.attribute = race, attr
                    self.cards[code] = d
                for row in db.execute("SELECT id,name," + ",".join(f"str{i}" for i in range(1, 17)) + " FROM texts"):
                    self.names[row[0]], self.descriptions[row[0]] = row[1], row[2:]
            self.databases[str(path.relative_to(self.root))] = digest(path)
        self.text_sources = {}
        for strings in [self.root / "strings.conf"] + sorted((self.root / "expansions").glob("*.conf")):
            if not strings.exists():
                continue
            self.text_sources[str(strings.relative_to(self.root))] = digest(strings)
            for line in strings.read_text(encoding="utf-8-sig").splitlines():
                m = re.match(r"!system\s+(\d+)\s+(.*)", line)
                if m:
                    self.system_strings[int(m[1])] = m[2]
        # 不静默忽略客户端优先使用的压缩内容包。
        archives = [p for folder in (self.root, self.root / "expansions") for p in folder.glob("*")
                    if p.suffix.lower() in (".zip", ".ypk")]
        if archives:
            raise ReplayError("内容目录包含 ZIP 覆盖包，请先准备展开且版本一致的独立内容目录")

    def name(self, code):
        code &= 0x7fffffff
        return f"{self.names.get(code, '未知卡')}（{code}）" if code else "未知卡"

    def description(self, value):
        if value < 2048:
            return self.system_strings.get(value, f"选项 {value}")
        code, index = value >> 4, value & 15
        descriptions = self.descriptions.get(code, ())
        return (descriptions[index] if index < len(descriptions) else None) or f"效果描述 {value}"


class Engine:
    def __init__(self, library, content):
        self.library, self.content = Path(library).resolve(), content
        self.api = C.CDLL(str(self.library))
        self.errors, self.script_buffers = [], {}
        self.duel = None
        self.api.replay_card_data_size.restype = C.c_uint32
        size = self.api.replay_card_data_size()
        card_type = {C.sizeof(CardData): CardData, C.sizeof(LegacyCardData): LegacyCardData}.get(size)
        if card_type is None:
            raise ReplayError(f"不支持的核心 card_data 大小 {size}；请使用配套源码")
        card_cb = C.CFUNCTYPE(C.c_uint32, C.c_uint32, C.POINTER(card_type))
        script_cb = C.CFUNCTYPE(C.c_void_p, C.c_char_p, C.POINTER(C.c_int32))
        log_cb = C.CFUNCTYPE(C.c_uint32, C.c_void_p, C.c_uint32)
        signatures = {
            "replay_card_data_size": (C.c_uint32, []),
            "replay_response_size": (C.c_uint32, []),
            "replay_create_yrp1": (C.c_void_p, [C.c_uint32]),
            "create_duel_v2": (C.c_void_p, [C.POINTER(C.c_uint32)]),
            "set_card_reader": (None, [card_cb]), "set_script_reader": (None, [script_cb]),
            "set_message_handler": (None, [log_cb]),
            "set_player_info": (None, [C.c_void_p] + [C.c_int32] * 4),
            "new_card": (None, [C.c_void_p, C.c_uint32] + [C.c_uint8] * 5),
            "start_duel": (None, [C.c_void_p, C.c_uint32]),
            "process": (C.c_uint32, [C.c_void_p]),
            "get_message": (C.c_int32, [C.c_void_p, C.c_void_p]),
            "get_log_message": (None, [C.c_void_p, C.c_void_p]),
            "set_responseb": (None, [C.c_void_p, C.c_void_p]),
            "end_duel": (None, [C.c_void_p]),
            "query_field_card": (C.c_int32, [C.c_void_p] + [C.c_uint8] * 2 + [C.c_uint32, C.c_void_p, C.c_int32]),
        }
        for name, (result, arguments) in signatures.items():
            func = getattr(self.api, name)
            func.restype, func.argtypes = result, arguments
        self.response_size = self.api.replay_response_size()
        if not 256 <= self.response_size <= 4096:
            raise ReplayError("核心响应缓冲区大小不受支持")

        def card_reader(code, output):
            try:
                data = content.cards.get(code)
                if data is None:
                    self.errors.append(f"卡库缺少卡片 {code}")
                    data = CardData()
                data = card_for_core(data, card_type)
                C.memmove(output, C.byref(data), C.sizeof(data))
            except Exception as exc:
                self.errors.append(f"读取卡库失败：{exc}")
            return 0

        def script_reader(raw_name, length):
            try:
                name = raw_name.decode("utf-8").removeprefix("./")
                path = (content.root / name).resolve()
                if not path.is_relative_to(content.root):
                    raise ReplayError("脚本路径越出内容目录")
                expansion = (content.root / "expansions" / name).resolve()
                if not expansion.is_relative_to(content.root):
                    raise ReplayError("扩展脚本路径越出内容目录")
                candidates = [expansion, path] if content.prefer_expansion else [path, expansion]
                path = next((p for p in candidates if p.is_file()), path)
                if not path.is_file():
                    match = re.fullmatch(r"script/c(\d+)\.lua", name)
                    card = content.cards.get(int(match[1])) if match else None
                    # 普通无效果怪兽/衍生物可以没有脚本；其他缺失须停止。
                    if card is None or not card.type & (0x10 | 0x4000) or card.type & (0x20 | 0x1000000):
                        self.errors.append(f"缺少脚本 {name}")
                    length[0] = 0
                    return None
                if name not in self.script_buffers:
                    data = path.read_bytes()
                    if len(data) >= 0x100000:
                        raise ReplayError(f"脚本过大：{name}")
                    self.script_buffers[name] = C.create_string_buffer(data)
                    content.scripts[name] = hashlib.sha256(data).hexdigest()
                buffer = self.script_buffers[name]
                length[0] = len(buffer) - 1
                return C.addressof(buffer)
            except Exception as exc:
                self.errors.append(f"读取脚本失败：{exc}")
                return None

        def log_reader(duel, kind):
            buffer = C.create_string_buffer(65536)
            self.api.get_log_message(duel, buffer)
            # 核心 type=1 为错误，type=2 为 Debug.Message。
            if kind == 1:
                self.errors.append(buffer.value.decode("utf-8", errors="replace"))
            return 0

        self.callbacks = (card_cb(card_reader), script_cb(script_reader), log_cb(log_reader))
        for setter, callback in zip((self.api.set_card_reader, self.api.set_script_reader,
                                     self.api.set_message_handler), self.callbacks):
            setter(callback)

    def check(self):
        if self.errors:
            raise ReplayError("内容包/核心错误：" + "；".join(dict.fromkeys(self.errors)))

    def start(self, replay):
        self.duel = (self.api.create_duel_v2((C.c_uint32 * 8)(*replay.seeds)) if replay.seeds
                     else self.api.replay_create_yrp1(replay.seed))
        if not self.duel:
            raise ReplayError("无法创建对局")
        self.check()
        for player, deck in enumerate(replay.decks):
            self.api.set_player_info(self.duel, player, *replay.params[:3])
            for location, cards in zip((1, 64), deck):
                for code in cards:
                    self.api.new_card(self.duel, code, player, player, location, 0, 8)
                    self.check()
        self.api.start_duel(self.duel, replay.params[3])
        self.check()

    def step(self):
        status = self.api.process(self.duel)
        self.check()
        length = status & 0x0fffffff
        if length > 16 * 1024 * 1024:
            raise ReplayError("核心返回了异常大的消息缓冲区")
        buffer = C.create_string_buffer(max(1, length))
        actual = self.api.get_message(self.duel, buffer)
        if actual != length:
            raise ReplayError(f"消息长度不符：process={length}, get_message={actual}")
        return status, buffer.raw[:length]

    def respond(self, response):
        if not 0 < len(response) <= self.response_size:
            raise ReplayError("响应长度超过核心缓冲区")
        # set_responseb 固定复制 SIZE_RETURN_VALUE 字节，不能只分配响应本身的长度。
        self.api.set_responseb(self.duel, C.create_string_buffer(response, self.response_size))

    def snapshot(self):
        result = {}
        for player in range(2):
            for location in (1, 2, 4, 8, 16, 32, 64):
                buffer = C.create_string_buffer(1024 * 1024)
                size = self.api.query_field_card(self.duel, player, location, 3, buffer, 0)
                r = Reader(buffer.raw[:size], "场面快照")
                cards = []
                while r.remaining:
                    size = r.u32()
                    if size == 4:
                        cards.append(None)
                    elif size == 16:
                        flags, code, position = r.unpack("3I")
                        if flags != 3:
                            raise ReplayError("查询返回未知字段")
                        cards.append([code, position])
                    else:
                        raise ReplayError(f"查询返回未知长度 {size}")
                result[f"{player}:{location}"] = cards
        return result

    def close(self):
        if self.duel:
            self.api.end_duel(self.duel)
            self.duel = None


def replay_events(engine, replay):
    """每条记录都带响应序号；RETRY 不会被当成下一次正常等待。"""
    used = 0
    last_request = None
    engine.start(replay)
    for batch in range(1, 1000001):
        status, raw = engine.step()
        messages = list(split_messages(raw))
        requests = [m for m in messages if m.is_request]
        if any(m.kind == 1 for m in messages):
            detail = f"{last_request.name}" if last_request else "无前一请求"
            raise ReplayError(f"引擎拒绝第 {used} 条响应（{detail}，批次 {batch}）；可能发生版本或响应失配")
        if len(requests) > 1 or (requests and messages[-1] is not requests[0]):
            raise ReplayError(f"批次 {batch} 的选择请求边界异常")
        for message in messages:
            yield {"batch": batch, "response_index": used, "message": message}
        wins = [m for m in messages if m.kind == 5]
        if wins or status & 0x20000000:
            if used != len(replay.responses):
                raise ReplayError(f"对局提前结束，尚余 {len(replay.responses) - used} 条响应")
            yield {"status": "win" if wins else "engine_end", "responses_used": used}
            return
        if requests:
            if not status & 0x10000000:
                raise ReplayError("核心发出选择请求但未进入等待状态")
            request = requests[0]
            state = engine.snapshot()
            if used == len(replay.responses):
                yield {"status": "responses_exhausted", "responses_used": used, "snapshot": state}
                return
            response = replay.responses[used]
            yield {"batch": batch, "response_index": used + 1, "request": request,
                   "response": response, "source_offset": replay.response_offsets[used], "snapshot": state}
            engine.respond(response)
            used += 1
            last_request = request
        elif status & 0x10000000:
            # PROCESSOR_WAIT、宣言结束等也会返回 WAITING；原版 ReplayAnalyze
            # 只根据 MSG_SELECT_*/MSG_ANNOUNCE_* 读取响应，绝不根据状态位读取。
            continue
        elif not raw:
            raise ReplayError("核心没有进展")
    raise ReplayError("回放超过安全步数上限")
