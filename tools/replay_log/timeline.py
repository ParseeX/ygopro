"""把已经切分的核心事件和玩家选择转成中文，不从卡名推测效果。"""
import struct
from .format import Reader, ReplayError

ZONES = {0: "对局外", 1: "卡组", 2: "手牌", 4: "怪兽区", 8: "魔法陷阱区",
         16: "墓地", 32: "除外区", 64: "额外卡组"}
POSITIONS = {1: "表侧攻击", 2: "里侧攻击", 4: "表侧守备", 8: "里侧守备", 5: "表侧", 10: "里侧"}
PHASES = {1: "抽卡阶段", 2: "准备阶段", 4: "主要阶段1", 8: "战斗阶段开始",
          16: "战斗步骤", 32: "伤害步骤", 64: "伤害计算", 128: "战斗阶段",
          256: "主要阶段2", 512: "结束阶段"}


class Timeline:
    def __init__(self, replay, content):
        self.replay, self.content = replay, content
        self.turn, self.phase, self.chains = 0, "初始设置", {}
        self.lp = [replay.params[0]] * 2
        self.zones = {}
        self.overlays = {}
        self.selection_hint = {}

    def player(self, index):
        if index not in (0, 1):
            raise ReplayError(f"无效玩家编号 {index}")
        name = self.replay.players[index] or "未命名"
        name = name.replace("\r", "\\r").replace("\n", "\\n")
        return f"玩家{index + 1}「{name}」"

    def sync(self, snapshot):
        self.zones = {key: [item[0] if item else None for item in items]
                      for key, items in snapshot.items()}

    def place(self, loc):
        player, zone, seq = loc[:3]
        if not zone:
            return "对局外"
        if zone & 128:
            parent_zone = zone & ~128
            return f"{self.player(player)}的{ZONES.get(parent_zone, '区域')}第{seq + 1}位卡片的超量素材"
        return f"{self.player(player)}的{ZONES.get(zone, f'区域{zone}')}第{seq + 1}位"

    def code_at(self, loc):
        player, zone, seq = loc[:3]
        if zone & 128:
            return self.overlays.get(tuple(loc), 0)
        cards = self.zones.get(f"{player}:{zone}", [])
        return (cards[seq] or 0) if seq < len(cards) else 0

    def card_at(self, loc):
        return f"{self.content.name(self.code_at(loc))}［{self.place(loc)}］"

    def remove(self, loc):
        player, zone, seq = loc[:3]
        if zone & 128:
            self.overlays.pop(tuple(loc), None)
            # 其余素材序号会前移，不能沿用旧索引猜卡名。
            self.overlays = {key: value for key, value in self.overlays.items()
                             if key[:3] != tuple(loc[:3])}
            return
        cards = self.zones.setdefault(f"{player}:{zone}", [])
        if seq < len(cards):
            if zone in (4, 8):
                cards[seq] = None
            else:
                cards.pop(seq)
        if zone == 4:
            self.overlays = {key: value for key, value in self.overlays.items()
                             if not (key[0] == player and key[2] == seq)}

    def add(self, loc, code):
        player, zone, seq = loc[:3]
        if not zone:
            return
        if zone & 128:
            self.overlays[tuple(loc)] = code
            return
        cards = self.zones.setdefault(f"{player}:{zone}", [])
        if zone in (4, 8):
            while len(cards) <= seq:
                cards.append(None)
            cards[seq] = code
        else:
            while len(cards) < seq:
                cards.append(0)
            cards.insert(seq, code)

    def names(self, codes):
        return "、".join(self.content.name(code) for code in codes) or "无"

    def message(self, m):
        k, r = m.kind, Reader(m.data, m.name)
        p = self.player
        n = self.content.name
        if m.is_request:
            return []
        if k == 40:
            self.turn += 1
            self.phase = "回合开始"
            return [f"第 {self.turn} 回合：{p(r.u8())}的回合"]
        if k == 41:
            value = r.unpack("H")[0]
            self.phase = PHASES.get(value, f"阶段 {value:#x}")
            return [f"进入{self.phase}"]
        if k == 2:
            typ, player, value = r.unpack("BBI")
            if typ == 3:
                self.selection_hint[player] = self.content.description(value)
                return []
            if typ in (1, 2):
                return [f"对局提示：{self.content.description(value)}"]
            if typ == 5:
                return [f"效果提示：{n(value)}"]
            if typ in (4, 6, 7, 8, 9, 10, 11):
                value_text = n(value) if typ in (8, 10) else (self.content.description(value) if typ == 4 else str(value))
                # HINT 的 player 经常是接收提示的人，不是行动者。
                return [f"{ {4:'选项提示',6:'种族宣言提示',7:'属性宣言提示',8:'卡片宣言提示',9:'数值提示',10:'展示卡片',11:'区域提示'}[typ]}：{value_text}"]
            return []
        if k == 90:
            player, count = r.unpack("BB")
            codes = [x & 0x7fffffff for x in r.unpack(f"{count}I")]
            deck = self.zones.setdefault(f"{player}:1", [])
            for code in codes:
                if deck:
                    deck.pop()
                self.zones.setdefault(f"{player}:2", []).append(code)
            return [f"{p(player)}抽 {count} 张卡：{self.names(codes)}"]
        if k == 50:
            code, = r.unpack("I")
            old, new = tuple(r.take(4)), tuple(r.take(4))
            reason = r.u32()
            code = code or self.code_at(old)
            materials = {key[3]: value for key, value in self.overlays.items()
                         if not old[1] & 128 and key[:3] == (old[0], old[1] | 128, old[2])}
            self.remove(old)
            self.add(new, code)
            if new[1] and not new[1] & 128:
                for sequence, value in materials.items():
                    self.overlays[(new[0], new[1] | 128, new[2], sequence)] = value
            tags = [label for flag, label in ((1, "破坏"), (2, "解放"), (8, "作为素材"),
                     (16, "召唤"), (32, "战斗"), (64, "效果"), (128, "代价"),
                     (0x1000, "召唤被无效"), (0x4000, "丢弃"), (0x40000, "融合"),
                     (0x80000, "同调"), (0x100000, "仪式"), (0x200000, "超量"),
                     (0x10000000, "连接")) if reason & flag]
            return [f"{n(code)}：{self.place(old)} → {self.place(new)}"
                    + (f"（{'、'.join(tags)}）" if tags else "")]
        if k in (54, 60, 62, 64):
            code, = r.unpack("I")
            loc = tuple(r.take(4))
            action = {54: "盖放", 60: "尝试通常召唤", 62: "尝试特殊召唤", 64: "尝试反转召唤"}[k]
            return [f"{p(loc[0])}{action} {n(code)}，{self.place(loc)}，{POSITIONS.get(loc[3], '表示形式未知')}"]
        if k in (61, 63, 65):
            return [{61: "通常召唤成功", 63: "特殊召唤成功", 65: "反转召唤成功"}[k]]
        if k == 53:
            code = r.u32()
            loc = tuple(r.take(3))
            old, new = r.unpack("BB")
            return [f"{p(loc[0])}的 {n(code)} 改变表示形式：{POSITIONS.get(old, old)} → {POSITIONS.get(new, new)}"]
        if k == 70:
            code = r.u32()
            loc = tuple(r.take(4))
            controller, zone, seq, description, number = r.unpack("BBBIB")
            actor = p(controller)
            self.chains[number] = f"{actor}的 {n(code)}"
            detail = f"［{self.content.description(description)}］" if description else ""
            return [f"连锁 {number}：{actor}{'连锁发动' if number > 1 else '发动'} {n(code)}"
                    f"{detail}，来源：{self.place((controller, zone, seq))}"]
        if k in (71, 72, 73, 75, 76):
            number = r.u8()
            if number not in self.chains:
                raise ReplayError(f"{m.name} 引用了不存在的连锁 {number}")
            action = {71: "加入连锁", 72: "开始结算", 73: "结算完成", 75: "发动被无效", 76: "效果被无效"}[k]
            return [f"连锁 {number} {action}：{self.chains[number]}"]
        if k == 74:
            self.chains.clear()
            return ["本次连锁处理结束"]
        if k in (91, 92, 94, 100):
            player, value = r.unpack("BI")
            before = self.lp[player]
            self.lp[player] = value if k == 94 else (before + value if k == 92 else max(0, before - value))
            action = {91: "受到伤害", 92: "回复 LP", 94: "LP 变为", 100: "支付 LP"}[k]
            return [f"{p(player)}{action} {value}（LP：{before} → {self.lp[player]}）"]
        if k == 110:
            attacker, defender = tuple(r.take(4)), tuple(r.take(4))
            return [f"{self.card_at(attacker)} 攻击 " + (self.card_at(defender) if defender[1] else "对方玩家（直接攻击）")]
        if k == 111:
            a = tuple(r.take(4))
            aa, ad, destroyed_a = r.unpack("iiB")
            b = tuple(r.take(4))
            ba, bd, destroyed_b = r.unpack("iiB")
            return [f"伤害计算：{self.card_at(a)} ATK {aa}/DEF {ad}，对方 "
                    + (f"{self.card_at(b)} ATK {ba}/DEF {bd}" if b[1] else "无防守怪兽")]
        if k in (112, 113, 114):
            if k in (113, 114):
                self.phase = "伤害步骤" if k == 113 else "战斗阶段"
            return [{112: "攻击被无效", 113: "伤害步骤开始", 114: "伤害步骤结束"}[k]]
        if k in (30, 31, 42):
            player = r.u8()
            if k == 31:
                r.take(1)
            count = r.u8()
            codes = []
            for _ in range(count):
                code = r.u32()
                loc = tuple(r.take(3))
                codes.append(code)
            return [f"向{p(player)}确认卡片：{self.names(codes)}"]
        if k in (32, 33, 39):
            player = r.u8()
            zone = {32: 1, 33: 2, 39: 64}[k]
            if k in (33, 39):
                codes = list(r.unpack(f"{r.u8()}I"))
                if k == 33:
                    self.zones[f"{player}:{zone}"] = codes
                else:
                    old = self.zones.get(f"{player}:64", [])
                    self.zones[f"{player}:64"] = codes + old[len(codes):]
            else:
                self.zones[f"{player}:1"] = [0] * len(self.zones.get(f"{player}:1", []))
            return [f"{p(player)}洗切{ZONES[zone]}"]
        if k == 35:
            player = r.u8()
            # 交换时还可能把额外怪兽移回额外卡组，旧列表不能继续当真。
            for zone in (1, 16, 64):
                self.zones[f"{player}:{zone}"] = []
            return [f"{p(player)}交换墓地与卡组"]
        if k == 37:
            for player in (0, 1):
                self.zones.get(f"{player}:1", []).reverse()
            return ["卡组上下翻转"]
        if k == 38:
            player, offset, code = r.unpack("BBI")
            deck = self.zones.get(f"{player}:1", [])
            if offset < len(deck):
                deck[-1 - offset] = code & 0x7fffffff
            return []
        if k == 36:
            zone, count = r.unpack("BB")
            for player in (0, 1):
                self.zones[f"{player}:{zone}"] = [0 if x is not None else None for x in self.zones.get(f"{player}:{zone}", [])]
            return [f"场上 {count} 张盖放卡的位置被打乱"]
        if k == 55:
            code_a, loc_a = r.u32(), tuple(r.take(4))
            code_b, loc_b = r.u32(), tuple(r.take(4))
            self.remove(loc_a)
            self.remove(loc_b)
            self.add(loc_a, code_b)
            self.add(loc_b, code_a)
            return [f"{n(code_a)} 与 {n(code_b)} 交换位置"]
        if k in (80, 81, 83):
            player = r.u8() if k != 83 else None
            cards = [self.card_at(tuple(r.take(4))) for _ in range(r.u8())]
            action = "成为效果对象" if k == 83 else (f"{p(player)}随机选中" if k == 81 else f"{p(player)}选中")
            return [f"{action}：{'、'.join(cards)}"]
        if k in (93, 96, 97):
            a, b = self.card_at(tuple(r.take(4))), self.card_at(tuple(r.take(4)))
            return [f"{a} { {93:'装备到',96:'建立卡片对象关系',97:'取消卡片对象关系'}[k]} {b}"]
        if k == 95:
            return [f"{self.card_at(tuple(r.take(4)))} 解除装备关系"]
        if k in (101, 102):
            counter = r.unpack("H")[0]
            card = self.card_at(tuple(r.take(3)))
            count = r.unpack("H")[0]
            return [f"{card} {'增加' if k == 101 else '移除'} {count} 个指示物（类型 {counter:#x}）"]
        if k in (130, 131):
            player, count = r.unpack("BB")
            values = r.take(count)
            result = "、".join(("正面" if v else "反面") if k == 130 else str(v) for v in values)
            return [f"{p(player)}{'掷硬币' if k == 130 else '掷骰子'}：{result}"]
        if k == 120:
            loc, code = tuple(r.take(4)), r.u32()
            return [f"{p(loc[0])}的 {n(code)} 错过发动时点"]
        if k == 5:
            winner, reason = r.unpack("BB")
            return [(f"{p(winner)}获胜" if winner < 2 else "对局平局") + f"（原因代码 {reason}）"]
        if k == 160:
            loc = tuple(r.take(4))
            typ, value = r.unpack("BI")
            detail = self.content.description(value) if typ in (6, 7) else str(value)
            action = {1: "回合计数", 2: "卡片提示", 3: "种族提示", 4: "属性提示", 5: "数值提示",
                      6: "增加效果说明", 7: "移除效果说明"}.get(typ, f"提示类型{typ}")
            return [f"{self.card_at(loc)} {action}：{detail}"]
        if k == 165:
            player, typ, value = r.unpack("BBI")
            return [f"{p(player)}{'增加' if typ == 6 else '移除'}效果说明：{self.content.description(value)}"]
        if k == 56:
            return [f"场地可用区域变更（禁用区域掩码 {r.u32():#x}）"]
        if k in (163, 164):
            length = r.unpack("H")[0]
            return [r.take(length).decode("utf-8", errors="replace")]
        if k == 170:
            return [f"决胜效果：{n(r.u32())}"]
        if k == 133:
            value = r.u8()
            hands = {1: "剪刀", 2: "石头", 3: "布"}
            return [f"猜拳结果：{p(0)}{hands.get(value & 3, '未知')}，{p(1)}{hands.get(value >> 2 & 3, '未知')}"]
        if k == 34:
            return [f"{p(r.u8())}刷新卡组"]
        raise ReplayError(f"尚未实现事件文本：{m.name}")

    def response(self, m, response):
        r = Reader(m.data, m.name)
        k = m.kind
        player = m.data[1] if k == 23 else m.data[0]
        actor = self.player(player)
        value = int.from_bytes(response[:4], "little", signed=True)

        def entries(width):
            return [r.take(width) for _ in range(r.u8())]

        def entry_name(entry):
            return self.content.name(struct.unpack_from("<I", entry)[0])

        def chosen(options, index):
            if index < 0 or index >= len(options):
                raise ReplayError(f"{m.name} 选择索引 {index} 越界（共 {len(options)} 项）")
            return options[index]

        r.take(1)
        if k in (10, 11):
            groups = [entries(w) for w in ([7] * 5 + [11] if k == 11 else [11, 8])]
            command, index = value & 0xffff, value >> 16
            actions = (["通常召唤", "特殊召唤", "改变表示", "盖放怪兽", "盖放魔法陷阱", "发动效果"]
                       if k == 11 else ["发动效果", "攻击"])
            if command < len(groups):
                return [f"{actor}选择{actions[command]}：{entry_name(chosen(groups[command], index))}"]
            phase = ({6: "战斗阶段", 7: "结束阶段", 8: "洗切手牌"} if k == 11
                     else {2: "主要阶段2", 3: "结束阶段"}).get(command)
            if phase is None:
                raise ReplayError(f"未知行动命令 {command}")
            return [f"{actor}选择：{phase}"]
        if k == 16:
            count = r.u8()
            r.take(9)
            options = [r.take(14) for _ in range(count)]
            if value == -1:
                return [f"{actor}不发动连锁"]
            entry = chosen(options, value)
            return [f"{actor}选择发动：{self.content.name(struct.unpack_from('<I', entry, 2)[0])}"]
        if k in (12, 13):
            subject = entry_name(r.take(4)) if k == 12 else self.content.description(r.u32())
            return [f"{actor}选择{'是' if value else '否'}：{subject}"]
        if k in (14, 143):
            options = [int.from_bytes(x, "little") for x in entries(4)]
            option = chosen(options, value)
            return [f"{actor}选择：{self.content.description(option) if k == 14 else option}"]
        if k in (15, 20, 26, 23):
            mandatory = []
            if k in (15, 20):
                r.take(3)
                options = entries(8)
            elif k == 26:
                r.take(4)
                options = entries(8) + entries(8)
            else:
                r.take(7)
                mandatory = entries(11)
                options = entries(11)
            if response == b"\xff\xff\xff\xff":
                return [f"{actor}结束或取消选择"]
            count = response[0]
            indices = response[1:1 + count]
            if len(indices) != count:
                raise ReplayError("选卡响应被截断")
            if mandatory:
                # SELECT_SUM 响应前 must_count 项是占位，后面的索引从可选列表计。
                indices = indices[len(mandatory):]
            cards = mandatory + [chosen(options, i) for i in indices]
            hint = self.selection_hint.pop(player, "选卡")
            return [f"{actor}选择（{hint}）：{'、'.join(entry_name(x) for x in cards) or '无'}"]
        if k in (18, 24):
            if len(response) % 3:
                raise ReplayError("选位置响应不是三字节记录的整数倍")
            return [f"{actor}选择位置：" + "、".join(self.place(response[i:i + 3]) for i in range(0, len(response), 3))]
        if k == 19:
            return [f"{actor}为 {self.content.name(r.u32())} 选择{POSITIONS.get(value, str(value))}"]
        if k == 25:
            options = entries(7)
            if response[0] == 255:
                return [f"{actor}不调整顺序"]
            if len(response) != len(options) or sorted(response) != list(range(len(options))):
                raise ReplayError("排序响应不是完整排列")
            ordered = sorted(zip(response, options))
            return [f"{actor}排序：{' → '.join(entry_name(item) for _, item in ordered)}"]
        if k == 22:
            counter, total = r.unpack("HH")
            options = entries(9)
            amounts = Reader(response).unpack(f"{len(options)}H")
            return [f"{actor}选择移除指示物：" + "、".join(f"{entry_name(item)} {count} 个" for item, count in zip(options, amounts) if count)]
        if k == 142:
            return [f"{actor}宣言卡片：{self.content.name(value)}"]
        if k in (140, 141, 132):
            return [f"{actor}{ {140:'宣言种族位掩码',141:'宣言属性位掩码',132:'猜拳选择'}[k]}：{value}"]
        raise ReplayError(f"尚未实现选择文本：{m.name}")
