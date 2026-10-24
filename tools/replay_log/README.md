# 录像转逐行对局记录

输入原生 YGOPro `.yrp`，输出 UTF-8 中文 `.txt`，同时生成 `.txt.trace.json` 供核对。
日志包含玩家选择、抽卡、召唤尝试和成功、盖放、连锁建立/逆序结算/无效、效果对象、
卡片移动和代价、战斗、LP、随机结果及引擎胜负消息。每行带回合、阶段和响应序号。

## 使用

需要 Python 3.10+，仅使用标准库。原生桥接库使用仓库已有的 ocgcore 和 Lua，
不下载依赖，不修改游戏客户端。先构建一次（Windows 已安装 MinGW、CMake、Ninja）：

```powershell
cmake -S tools/replay_native -B build/replay-log -G Ninja -DCMAKE_BUILD_TYPE=Release
cmake --build build/replay-log -j 4
python tools/replay_to_text.py "D:/Games/YGOPro/replay/example.yrp" --content "D:/Games/YGOPro" -o "build/replay-log/example.txt"
```

`--content` 必须是与录像匹配的客户端内容目录，包含 `cards.cdb`、`script/`，可包含
`expansions/*.cdb`、`expansions/script/`。遵循 `system.conf` 中的
`prefer_expansion_script`。有 ZIP/YPK 覆盖包时会拒绝，以免静默漏加载；请先准备独立、
已展开的内容目录。不同卡库覆盖顺序按文件名排序，需与使用的客户端版本一致。

不指定 `-o` 时在录像旁生成同名 `.txt`。不会默认覆盖现有导出；需要覆盖时显式传入
`--overwrite`，原文件会改名为带时间戳的 `.bak`。原始录像、卡库、脚本只读。

不同核心可以独立构建，不能直接传任意版本的 `ocgcore.dll`：

```powershell
cmake -S tools/replay_native -B build/replay-log-history -G Ninja -DCMAKE_BUILD_TYPE=Release -DCORE_SOURCE="D:/history/ygopro-core" -DLUA_SOURCE="D:/history/lua/src"
cmake --build build/replay-log-history -j 4
python tools/replay_to_text.py "D:/replays/example.yrp" --content "D:/history/content" --core "build/replay-log-history/libreplay_core.dll" -o "build/replay-log-history/example.txt"
```

历史核心必须使用兼容的消息布局和 YRP 格式。支持仓库的 76 字节旧版及 80 字节新版
`card_data`，按版本保留各自的规则同名处理。桥接库会核验结构大小，
使用该核心实际的响应缓冲区大小。Linux/macOS 构建后请用 `--core` 指定生成的动态库。

## 如何看结果

- `win`：执行全部响应且收到了明确胜负消息。
- `responses_exhausted`：执行全部记录，末尾仍等待下一次选择。投降、断线或中途保存的
  原生录像可能没有引擎胜负事件；不会猜测谁获胜。
- `engine_end`：核心结束，但没有胜负消息。
- `error`：还原中断，退出码 2；已生成的文本首尾明确标记为未完成。格式/初始化失败时
  可能尚未生成文件。成功处理记录的退出码为 0。

这类录像保存初始配置、种子和玩家响应，不保存完整动作流，也不保存核心/内容包版本。
“全部响应执行成功”仅表示给定环境下可重建，不能单独证明与历史实战完全一致。
需要严格历史核对时保留当时的核心、卡库和脚本，比较原客户端的关键动作/状态。
trace 文件包含核心、录像、卡库和实际加载脚本的 SHA256、原始消息、响应正文偏移、
每次真实输入前的场面快照，以及终止原因。trace 含完整手牌和玩家名，应与原录像同样保管。

工具展示上帝视角下可恢复的双方手牌。无法确认的卡片保留位置并标为未知，
不会凭卡名或效果文本补造动作。部分罕见提示以原始类型/数值表示。
目前支持本仓库的普通双人 YRP1/YRP2；TAG、残局和其他客户端的同名格式明确拒绝。

## 防止中途错位

1. 按原版 `ReplayMode::ReplayAnalyze` 的选择消息消费响应，不能只看 `PROCESSOR_WAITING`。
   `PROCESSOR_WAIT` 和宣言收尾也返回这个标志，却不需要响应。样例中旧方式在第 39 条响应后
   误吃一条输入，第 42 条收到 `MSG_RETRY`。
2. `MSG_RETRY` 必须是失败，不是可以继续消费下一条响应的正常等待。
3. 一个 `process()` 缓冲区可能有多条消息。逐条切分并验证长度，未知消息立即停止。
4. `set_responseb()` 按核心固定大小复制，必须提供补零后的完整缓冲区。
5. YRP1 按原客户端先执行一次 `std::mt19937(seed)()`；YRP2 使用完整八项种子。
6. 历史脚本修改可能改变选择次数和场面，即使核心版本号相同也不能混用。真实旧样例使用
   当前内容在第 314 条响应失配，改用当时的卡库与历史脚本后可消费全部 346 条记录。

## 验证

```powershell
python -B -m unittest discover -s tests -p replay_log_test.py
python -B tests/replay_log_integration.py "D:/Games/YGOPro/replay/example.yrp" --content "D:/Games/YGOPro"
```

集成测试直接提取并编译仓库原版 `ReplayAnalyze`（只增加记录、替换 GUI 为桩），
比较全部消息的类型、偏移和长度；比较两次执行的完整事件/状态/文本；在真实录像中段注入
非法选择，确认准确停止。测试使用本地私有录像，不将录像或生成日志纳入 Git。
此差分验证的是消息/响应边界，不等于原客户端 UI 的逐帧视觉验收。
