import argparse
from datetime import datetime
import json
from pathlib import Path
import sqlite3
import sys
import time

from .engine import Content, Engine, digest, replay_events
from .format import Replay, ReplayError
from .timeline import Timeline


def export(replay_path, content_root, library):
    replay = Replay.read(replay_path)
    content = Content(content_root)
    engine = Engine(library, content)
    timeline = Timeline(replay, content)
    lines, trace = [], []
    outcome = {"status": "error", "responses_used": 0}
    started = time.monotonic()
    try:
        for event in replay_events(engine, replay):
            if time.monotonic() - started > 120:
                raise ReplayError("回放超过 120 秒，请检查内容包和脚本")
            record = {k: v for k, v in event.items() if k not in ("message", "request", "response")}
            if "status" in event:
                outcome = {k: v for k, v in event.items() if k != "snapshot"}
                trace.append(record)
                break
            message = event.get("message", event.get("request"))
            record.update(kind=message.kind, name=message.name, payload=message.data.hex(), offset=message.offset)
            trace.append(record)
            if "snapshot" in event:
                timeline.sync(event["snapshot"])
            if "response" in event:
                record["response"] = event["response"].hex()
                try:
                    text = timeline.response(message, event["response"])
                except ReplayError as exc:
                    raise ReplayError(f"响应 {event['response_index']}，正文偏移 {event['source_offset']}，"
                                      f"{message.name}：{exc}") from exc
            else:
                text = timeline.message(message)
            record["text"] = text
            for sentence in text:
                # 单行转义所有来自玩家名称、卡库和脚本的换行。
                sentence = sentence.replace("\r", "\\r").replace("\n", "\\n")
                lines.append(f"{len(lines) + 1:05d} [回合{timeline.turn}·{timeline.phase}·响应{event['response_index']}] {sentence}")
            outcome["responses_used"] = event["response_index"]
    except ReplayError as exc:
        outcome["error"] = str(exc)
    finally:
        engine.close()
    metadata = {
        "schema": 1, "replay": str(replay.path.resolve()), "replay_sha256": digest(replay.path),
        "core": str(engine.library), "core_sha256": digest(engine.library),
        "content_root": str(content.root), "databases": content.databases, "scripts": content.scripts,
        "text_sources": content.text_sources,
        "prefer_expansion_script": content.prefer_expansion,
        "version": replay.version, "responses_total": len(replay.responses), **outcome,
    }
    labels = {
        "win": "已执行全部记录，并收到引擎胜负消息",
        "engine_end": "已执行全部记录，引擎结束（无胜负消息）",
        "responses_exhausted": "已执行全部录像记录；末尾等待下一次选择，录像未保存明确胜负",
        "error": "还原中断：以下为未完成日志，不可当作完整对局",
    }
    header = ["YGOPro 对局逐行记录", f"状态：{labels[outcome['status']]}",
              "注意：YRP 不保存核心/卡库/脚本版本；成功执行不等于已与原始对局逐步核验。",
              f"录像：{replay.path.name}", f"响应：{outcome['responses_used']}/{len(replay.responses)}",
              f"核心 SHA256：{metadata['core_sha256']}",
              f"对局：{timeline.player(0)} vs {timeline.player(1)}"]
    if outcome.get("error"):
        header.append("错误：" + outcome["error"])
    footer = ["", "结束状态：" + labels[outcome["status"]]]
    if outcome.get("error"):
        footer.append("错误：" + outcome["error"])
    return "\n".join(header + [""] + lines + footer) + "\n", {"metadata": metadata, "events": trace}


def main(argv=None):
    root = Path(__file__).resolve().parents[2]
    parser = argparse.ArgumentParser(description="将原生双人 YRP1/YRP2 录像导出为中文逐行日志")
    parser.add_argument("replay", type=Path)
    parser.add_argument("--content", type=Path, required=True, help="与录像匹配的客户端目录（cards.cdb、script/）")
    parser.add_argument("--core", type=Path, default=root / "build/replay-log/libreplay_core.dll",
                        help="使用 tools/replay_native 构建的配套核心库")
    parser.add_argument("-o", "--output", type=Path, help="默认在录像旁生成同名 .txt")
    parser.add_argument("--overwrite", action="store_true", help="备份已有导出文件后覆盖")
    args = parser.parse_args(argv)
    output = (args.output or args.replay.with_suffix(".txt")).resolve()
    trace_path = output.with_suffix(output.suffix + ".trace.json")
    protected = [args.replay.resolve(), args.core.resolve()]
    content_root = args.content.resolve()
    if (output in protected or trace_path in protected or output == trace_path
            or output == content_root / "cards.cdb" or trace_path == content_root / "cards.cdb"):
        parser.error("输出路径不能覆盖录像、核心或卡库")
    if output.suffix.lower() != ".txt":
        parser.error("日志输出必须使用 .txt 扩展名")
    for path in (output, trace_path):
        if path.exists() and not args.overwrite:
            parser.error(f"输出已存在：{path}；请换一个路径，或使用 --overwrite 保存备份后覆盖")
    try:
        text, trace = export(args.replay, args.content, args.core)
        output.parent.mkdir(parents=True, exist_ok=True)
        for path in (output, trace_path):
            if path.exists():
                backup = path.with_name(path.name + "." + datetime.now().strftime("%Y%m%d-%H%M%S-%f") + ".bak")
                path.rename(backup)
        output.write_text(text, encoding="utf-8-sig")
        trace_path.write_text(json.dumps(trace, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"日志：{output}\n核对数据：{trace_path}")
        metadata = trace["metadata"]
        print(f"状态：{metadata['status']}；响应 {metadata['responses_used']}/{metadata['responses_total']}")
        if metadata.get("error"):
            print("还原中断：" + metadata["error"], file=sys.stderr)
            return 2
        return 0
    except (ReplayError, OSError, AttributeError, ValueError, sqlite3.Error) as exc:
        print(f"无法导出：{exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
