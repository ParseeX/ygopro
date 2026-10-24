"""YGOPro 原生 YRP1/YRP2 容器；不猜测未知格式，不容忍截断。"""
from dataclasses import dataclass
import lzma
from pathlib import Path
import struct


class ReplayError(ValueError):
    pass


class Reader:
    def __init__(self, data, label="数据"):
        self.data, self.pos, self.label = bytes(data), 0, label

    def take(self, n):
        if n < 0 or self.pos + n > len(self.data):
            raise ReplayError(f"{self.label} 在偏移 {self.pos} 截断：需要 {n} 字节")
        value = self.data[self.pos:self.pos + n]
        self.pos += n
        return value

    def unpack(self, fmt):
        return struct.unpack("<" + fmt, self.take(struct.calcsize("<" + fmt)))

    def u8(self):
        return self.take(1)[0]

    def u32(self):
        return self.unpack("I")[0]

    @property
    def remaining(self):
        return len(self.data) - self.pos


@dataclass
class Replay:
    path: Path
    version: int
    flags: int
    seed: int
    seeds: tuple
    players: list
    params: tuple
    decks: list
    responses: list
    response_offsets: list

    @classmethod
    def read(cls, path):
        path = Path(path)
        if path.stat().st_size > 2 * 1024 * 1024:
            raise ReplayError("录像超过原生格式的大小限制")
        r = Reader(path.read_bytes(), "录像头")
        magic, version, flags, seed, size, _time, props = r.unpack("6I8s")
        if magic not in (0x31707279, 0x32707279):
            raise ReplayError("仅支持本仓库的原生 YRP1/YRP2，不能按扩展名猜测协议")
        if version < 0x12d0 or (version >= 0x1353 and not flags & 0x10):
            raise ReplayError(f"不支持的录像布局：version={version:#x}, flags={flags:#x}")
        if flags & (2 | 8):
            raise ReplayError("当前仅支持普通双人对局，TAG/残局请使用原客户端")
        if flags & ~0x11:
            raise ReplayError(f"不支持的录像标志：{flags:#x}")
        seeds = ()
        if magic == 0x32707279:
            seeds = r.unpack("8I")
            extension = r.unpack("4I")
            if extension != (1, 0, 0, 0):
                raise ReplayError(f"不支持的 YRP2 扩展头：{extension}")
        body = r.take(r.remaining)
        if flags & 1:
            if not 0 < size <= 0x80000:
                raise ReplayError("解压长度超出原生录像限制")
            lc, lp, pb = props[0] % 9, props[0] // 9 % 5, props[0] // 45
            dictionary = int.from_bytes(props[1:5], "little")
            if pb > 4 or lc + lp > 4 or dictionary > 64 * 1024 * 1024:
                raise ReplayError("不支持的 LZMA 参数")
            try:
                decoder = lzma.LZMADecompressor(format=lzma.FORMAT_RAW, filters=[{
                    "id": lzma.FILTER_LZMA1, "lc": lc, "lp": lp, "pb": pb,
                    "dict_size": max(dictionary, 4096)}])
                # 原生 LZMA 流允许无结束标记，按头部长度验证。
                body = decoder.decompress(body, max_length=size + 1)
            except lzma.LZMAError as exc:
                raise ReplayError(f"LZMA 解压失败：{exc}") from exc
            if len(body) != size or decoder.unused_data:
                raise ReplayError("解压长度与头部不一致，或压缩流后有额外数据")
        elif len(body) > 0x80000:
            raise ReplayError("录像正文超过大小限制")
        r = Reader(body, "录像正文")
        try:
            players = [r.take(40).decode("utf-16le").split("\0", 1)[0] for _ in range(2)]
        except UnicodeError as exc:
            raise ReplayError("玩家名称不是有效的 UTF-16LE") from exc
        params = r.unpack("iiiI")
        if params[3] & 0x20:
            raise ReplayError("录像头与对局 TAG 标志不一致")
        if params[0] <= 0 or not 0 <= params[1] <= 100 or not 0 <= params[2] <= 100:
            raise ReplayError("无效的初始 LP/手牌/抽卡数量")
        decks = []
        for _ in range(2):
            deck = []
            for _ in range(2):
                count = r.u32()
                if count > 100:
                    raise ReplayError("牌组数量超出限制")
                deck.append(list(r.unpack(f"{count}I")))
            decks.append(deck)
        responses, offsets = [], []
        while r.remaining:
            offsets.append(r.pos)
            length = r.u8()
            if not length:
                raise ReplayError(f"响应 {len(responses) + 1} 长度为零")
            responses.append(r.take(length))
        return cls(path, version, flags, seed, seeds, players, params, decks, responses, offsets)
