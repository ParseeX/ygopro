"""真实录像回归及原版 ReplayAnalyze 的消息边界差分；私有录像不写入 Git。"""
import argparse
import json
from pathlib import Path
import struct
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
sys.dont_write_bytecode = True
from replay_log.__main__ import export
from replay_log.engine import Content, Engine, replay_events
from replay_log.format import Replay, ReplayError


def original_analyzer():
    source = (ROOT / "gframe/replay_mode.cpp").read_text(encoding="utf-8")
    start = source.index("bool ReplayMode::ReplayAnalyze(")
    end = source.index("inline void ReplayMode::ReloadLocation", start)
    source = source[start:end]
    # 只增加边界记录；保留原版 switch、指针推进及响应触发条件。
    source = source.replace("return ReadReplayResponse();", "record(offset, pbuf - offset); return ReadReplayResponse();")
    source = source.replace("if(pauseable) {", "record(offset, pbuf - offset); if(pauseable) {")
    # WIN/RETRY 提前返回，不会到达循环底部。
    source = source.replace("case MSG_WIN: {", "case MSG_WIN: { record(offset, 3);")
    source = source.replace("case MSG_RETRY: {", "case MSG_RETRY: { record(offset, 1);")
    return r'''
#include <cstdint>
#include <iostream>
#include <vector>
#include <mutex>
#ifdef _WIN32
#include <io.h>
#include <fcntl.h>
#endif
#include "gframe/bufferio.h"
#include "ocgcore/common.h"
// 客户端仍支持的保留消息。
constexpr int MSG_REFRESH_DECK=34, MSG_CARD_SELECTED=80, MSG_UNEQUIP=95;
bool is_closing=false,is_restarting=false,is_swaping=false,is_pausing=false,is_paused=false;
int skip_turn=0,current_step=0,skip_step=0;
struct Signal { void Reset(){} void Wait(){} };
struct Label { void setText(const wchar_t*){} } label;
struct Field { void ReplaySwap(){} void RefreshAllCards(){} };
struct Game {
    struct { bool isReplaySkiping=false,isStarted=false,isFinished=false; int curMsg=0; } dInfo;
    Field dField; std::mutex gMutex; Signal actionSignal; Label* stMessage=&label; Label* wMessage=&label;
    void PopupElement(Label*){}
} game,*mainGame=&game;
struct DuelClient { static void ClientAnalyze(unsigned char*,int){} };
unsigned char* base;
void record(unsigned char* data, int length) {
    std::cout << int(data[0]) << ":" << data-base << ":" << length << " ";
}
struct ReplayMode {
    static bool ReplayAnalyze(unsigned char*,unsigned int);
    static bool ReadReplayResponse(){return true;}
    static void ReplayRefresh(){}
    static void ReplayRefreshDeck(int){}
    static void ReplayRefreshExtra(int){}
    static void ReplayRefreshGrave(int){}
    static void ReplayRefreshSingle(int,int,int){}
    static void ReplayReload(){}
    static void Pause(bool,bool){}
};
''' + source + r'''
int main() {
#ifdef _WIN32
    _setmode(_fileno(stdin), _O_BINARY);
#endif
    uint32_t length;
    while(std::cin.read(reinterpret_cast<char*>(&length),4)) {
        if(length>1024*1024) return 2;
        std::vector<unsigned char> data(length);
        if(!std::cin.read(reinterpret_cast<char*>(data.data()),length))return 3;
        base=data.data();
        ReplayMode::ReplayAnalyze(base,length);
        std::cout << "\n";
    }
}
'''


def main():
    p = argparse.ArgumentParser()
    p.add_argument("replay", type=Path, nargs="+")
    p.add_argument("--content", type=Path, required=True)
    p.add_argument("--core", type=Path, default=ROOT / "build/replay-log/libreplay_core.dll")
    p.add_argument("--cxx", default="g++")
    args = p.parse_args()
    with tempfile.TemporaryDirectory(prefix="replay-oracle-") as temp:
        source, exe = Path(temp) / "oracle.cpp", Path(temp) / "oracle.exe"
        source.write_text(original_analyzer(), encoding="utf-8")
        subprocess.run([args.cxx, "-std=c++17", "-static", "-I", str(ROOT), str(source), "-o", str(exe)], check=True)
        for path in args.replay:
            text, trace = export(path, args.content, args.core)
            metadata = trace["metadata"]
            if metadata["status"] == "error":
                raise AssertionError(f"{path.name}: {metadata['error']}")
            batches = {}
            expected = {}
            for event in trace["events"]:
                if "kind" not in event or "response" in event:
                    continue
                packet = bytes([event["kind"]]) + bytes.fromhex(event["payload"])
                batch = event["batch"]
                batches.setdefault(batch, bytearray()).extend(packet)
                expected.setdefault(batch, []).append(f"{event['kind']}:{event['offset']}:{len(packet)}")
            data = b"".join(struct.pack("<I", len(raw)) + raw for raw in batches.values())
            native = subprocess.run([str(exe)], input=data, capture_output=True, check=True).stdout.decode().splitlines()
            self_expected = [" ".join(items) for items in expected.values()]
            assert [line.strip() for line in native] == self_expected, "与原版 ReplayAnalyze 的逐消息边界不一致"
            # 再跑一次检验所有消息、响应、状态及文本；不能仅比较最终计数。
            text_again, again = export(path, args.content, args.core)
            assert text == text_again and trace == again, "重复执行的完整事件流不同"
            # 在中途注入非法响应，必须失败，不能消费后续响应直到末尾。
            replay = Replay.read(path)
            midpoint = next(e for e in trace["events"] if "response" in e
                            and e["response_index"] > len(replay.responses) // 2 and e["kind"] in (10, 11, 14, 15, 16))
            index = midpoint["response_index"] - 1
            replay.responses[index] = b"\x7f\x7f\x7f\x7f"
            engine = Engine(args.core, Content(args.content))
            rejected = False
            try:
                list(replay_events(engine, replay))
            except ReplayError as exc:
                rejected = f"拒绝第 {index + 1} 条响应" in str(exc)
            finally:
                engine.close()
            assert rejected, "非法响应未被准确拒绝"
            print(f"PASS {path.name}: {metadata['responses_total']} responses, {len(batches)} batches, "
                  f"native boundaries + full deterministic trace + corruption at {index + 1}; {metadata['status']}", flush=True)


if __name__ == "__main__":
    main()
