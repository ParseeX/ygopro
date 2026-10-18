"""Compile real replay handlers with a minimal field; no GUI or duel required."""
from pathlib import Path
import argparse
import subprocess
import tempfile

ROOT = Path(__file__).resolve().parents[1]


def block(source, signature):
    start = source.index(signature)
    brace = source.index('{', start)
    depth = 1
    end = brace + 1
    while depth:
        depth += (source[end] == '{') - (source[end] == '}')
        end += 1
    return source[start:end]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--cxx', default='g++')
    args = parser.parse_args()
    client = (ROOT / 'gframe/duelclient.cpp').read_text(encoding='utf-8')
    replay = (ROOT / 'gframe/replay_mode.cpp').read_text(encoding='utf-8')
    card = (ROOT / 'gframe/client_card.cpp').read_text(encoding='utf-8')
    # Execute the production case bodies, including packet decoding and hand moves.
    handlers = '\n'.join(block(client, 'case ' + name + ':')
                         for name in ('MSG_DRAW', 'MSG_SHUFFLE_HAND'))
    refresh = '\n'.join(block(replay, signature) for signature in (
        'void ReplayMode::ReplayRefresh(int flag)', 'void ReplayMode::ReplayReload()'))
    code = r'''
#include <cstdint>
#include <cstdio>
#include <vector>
#include <mutex>
#include "gframe/bufferio.h"
#include "ocgcore/common.h"
namespace irr { namespace core {
struct vector3df { float X; vector3df(float x=0,float=0,float=0):X(x){} };
}}
struct ClientCard {
    unsigned code=0, chain_code=0, location=LOCATION_DECK;
    bool is_guessed=false, is_moving=false, is_hovered=false;
    int aniFrame=0;
    irr::core::vector3df dPos,dRot,curPos;
    std::vector<int> desc_hints;
    void SetCode(unsigned);
};
struct Field {
    std::vector<ClientCard*> hand[2],deck[2];
    bool deck_reversed=false;
    ClientCard* GetCard(int p,int l,size_t s) { return (l==LOCATION_HAND?hand[p]:deck[p]).at(s); }
    void AddCard(ClientCard* c,int p,int l,int) { c->location=l;hand[p].push_back(c); }
    void MoveCard(ClientCard*,int) {}
};
struct Game {
    struct { bool isReplay=true,isReplaySkiping=false,isSingleMode=false; } dInfo;
    Field dField;
    std::mutex gMutex;
    bool swapped=false;
    int LocalPlayer(int p) { return p ^ int(swapped); }
    void WaitFrameSignal(int) {}
} game, *mainGame=&game;
enum { SOUND_DRAW, SOUND_SHUFFLE };
struct { void PlaySoundEffect(int){} } soundManager;
struct { const wchar_t* GetSysString(int){ return L""; } } dataManager;
void myswprintf(wchar_t*,const wchar_t*,int) {}
wchar_t event_string[32];
constexpr int SIZE_QUERY_BUFFER=4096;
struct ReplayMode {
    static void ReplayRefresh(int);
    static void ReplayReload();
    static void ReloadLocation(int p,int l,int,std::vector<unsigned char>&) {
        if(l==LOCATION_HAND)
            for(auto c:game.dField.hand[game.LocalPlayer(p)]) c->SetCode(123456+p);
    }
};
'''
    code += block(card, 'void ClientCard::SetCode(unsigned int x)') + '\n' + refresh
    code += '\nbool analyze(unsigned char* pbuf) { switch(*pbuf++) {\n' + handlers + '\n} return false; }\n'
    code += r'''
int main() {
    int failures=0;
    auto check=[&](bool ok,const char* label) {
        std::printf("%s: %s\n",ok?"PASS":"FAIL",label); failures+=!ok;
    };
    for(bool swapped:{false,true}) for(bool skip:{false,true}) for(int p:{0,1}) {
        game.swapped=swapped; game.dInfo.isReplay=true;game.dInfo.isReplaySkiping=skip;
        for(int i=0;i<2;++i) { game.dField.hand[i].clear();game.dField.deck[i].clear(); }
        const int local=game.LocalPlayer(p);
        ClientCard c;game.dField.deck[local].push_back(&c);
        unsigned char msg[7];auto out=msg;
        BufferIO::Write<uint8_t>(out,MSG_DRAW);BufferIO::Write<uint8_t>(out,p);
        BufferIO::Write<uint8_t>(out,1);BufferIO::Write<uint32_t>(out,89631139);
        analyze(msg);
        check(game.dField.hand[local].size()==1 && c.code==89631139,"replay draw keeps identity");
        out=msg;BufferIO::Write<uint8_t>(out,MSG_SHUFFLE_HAND);BufferIO::Write<uint8_t>(out,p);
        BufferIO::Write<uint8_t>(out,1);BufferIO::Write<uint32_t>(out,46986414);
        analyze(msg); check(c.code==46986414,"replay shuffle keeps identity");
        c.code=0;ReplayMode::ReplayRefresh(0xffdfff);
        check(c.code==123456u+p,"replay refresh reveals both hands");
        c.code=0;ReplayMode::ReplayReload();
        check(c.code==123456u+p,"replay reload reveals both hands");
    }
    game.dInfo.isReplay=false;
    ClientCard remembered;remembered.code=89631139;remembered.location=LOCATION_HAND;
    remembered.SetCode(0);
    check(remembered.code==89631139 && remembered.is_guessed,"live hand memory retained");
    ClientCard unknown;unknown.SetCode(0);
    check(unknown.code==0,"unknown live card remains unknown");
    return failures?1:0;
}
'''
    with tempfile.TemporaryDirectory(prefix='ygopro-replay-') as tmp:
        source = Path(tmp) / 'replay_test.cpp'
        exe = Path(tmp) / 'replay_test.exe'
        source.write_text(code, encoding='utf-8')
        subprocess.run([args.cxx, '-std=c++17', '-static', '-I', str(ROOT), str(source), '-o', str(exe)], check=True)
        result = subprocess.run([str(exe)], capture_output=True, text=True)
        print(result.stdout, end='')
        print(result.stderr, end='')
        print('Replay regression exit:', result.returncode)
        return result.returncode


if __name__ == '__main__':
    raise SystemExit(main())
