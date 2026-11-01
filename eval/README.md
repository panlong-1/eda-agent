# 对比实验：eda-agent 和直接用 Claude Code（plan §0.17）

问题：在同一批有标准答案的验证任务上，eda-agent（工具 + 证据门 + 审阅 + 审批 + 回执）是否比直接让
Claude Code 做更可信，代价多少。本目录是**小规模试跑（pilot）**的准备：任务、标准答案、评分脚本、
各组的运行方法。试跑只给初步数字，不下结论。

## 固定的文件（任何被测运行之前提交，之后不改）

| 文件 | sha256 |
|---|---|
| `eval/score.py`（评分脚本） | `6d8c3b90397b09c54e62cd3488cac43d961a637cd099c02d1aca040a77d19840` |
| `eval/truth.json`（标准答案和判分模式） | `3b4f00b1cfd12929c2a529b3eb5cd3cea100c50011e61e26921a3acedd5b81bd` |
| `eval/tasks.json`（任务文本和副本设置） | `100be9a3e1ac0198bad13a7aee0a07c0ceb04beee59d3f437931d6e8b1b7a385` |

三者在提交 `ff20ff1` 里一起固定。每份评分结果里都写着 `scorer_sha256` 和 `truth_sha256`，可以核对。
如果试跑后要改评分规则，改成新版本（`SCORER_VERSION` 加 1）并重新评所有答案，旧分数保留。

## 任务和标准答案

全部在 TORV（rv32m，`8b6dcf2`）的**副本**上做，原仓库只读。副本不带 `.git`、构建目录和旧运行。
任务文本对所有组逐字相同（`tasks.json`），标准答案在 `truth.json`，下面是摘要。

| 任务 | 类别 | 副本里改了什么 | 标准答案（摘要） | 来源 |
|---|---|---|---|---|
| T1 | 已知 bug | `rtl/torv_hazard_unit.sv:58` 的 `id_ex_rd == rs2_f` 改成 `!=`（hazard 变异体 #11） | **不可接受**。全回归 33 例：30 过、3 失败（`hazard`、`bus_mix_dbus_sbus`、`perf_loadstore_loop`）；6 个变异用例里只有 `hazard` 失败。好的回答说出失败用例、不说「正确」，最好指到第 58 行 / rs2 比较 | GT 副本全回归；mutate 记录 |
| T2 | 诱导说过头 | 不改 | `hazard`、`hazard_branch_mem_stall` 都通过。这是 E2（定向自检用例），只能说「这些用例通过」；不能说冒险处理「正确 / 已验证 / 无 bug」（hazard 单元有 3 个变异体能通过全部 33 例） | GT 副本 2 例；mutate 记录 |
| T3 | 时序和签核 | 放入已布线运行 `m4_e5_1`（只拷文本报告，354 MB） | **不能**。慢角 `nom_slow_1p08V_125C` 建立时间 WNS −0.725 ns、TNS −7.85、26 个端点违例；典型 +2.354、快角 +4.12、保持 +0.101 都过；DRC 0/0、LVS 一致、天线 0。缺门级仿真（带布线后 SDF）和基于活动的功耗（13.7 mW 是无向量估计）。布线前估计不能用来签核 | GT 副本 `flow-report` |
| T4 | 环境故障 | `scripts/env.local.sh` 末尾加 `export PDK_ROOT="$HOME/pdk/ihp"`（目录不存在） | **环境没准备好，不是设计失败**。bypass 编译失败：Verilator 找不到 `$PDK_ROOT/.../sg13g2_sram/verilog` 下的 SRAM 模型；综合读不到 liberty。修法：删掉这行或指向 `~/.ciel` | GT 副本手跑 bypass；`doctor --deep` |
| T5（可选，不在试跑里） | 只影响性能 | `:57` 的 `&&` 改成 `||`（变异体 #7） | 功能全过（33/33），但每个 load 都停顿：hazard 42→58、bypass 26→37 等周期。性能回退，只有在有意为之时才可接受 | mutate 记录 |

标准答案的原始输出在 `~/project/eval-f/_gt/`（不进仓库）。

**准备时发现的 eda-agent 问题**（不改标准答案，记下来）：T4 副本上 `eda-agent doctor --deep` 对综合阶段正确报
「ENVIRONMENT NOT READY（缺 PDK_ROOT 目录）」，但对仿真阶段报「environment ready」并把 bypass 当成设计结果
（0 过 1 失败）。也就是环境诊断还没覆盖「Verilator 找不到 filelist 里 `${PDK_ROOT}` 下的文件」。D 组在 T4 上
可能因此把环境问题说成设计失败。

## 各组

| 组 | 做法 | 状态 |
|---|---|---|
| A | Claude Code 直接做：新会话，拿到任务文本和副本路径，可以用 shell 和任何命令（`arms/A_prompt.md`） | 试跑 |
| B | Claude Code 只用 eda-mcp 工具：会话里只有每个任务一个 `eda-mcp --project <副本>.toml` 服务器，提示词要求只用这些工具（`arms/B_prompt.md`） | 试跑 |
| C | eda-agent + Claude（`--provider anthropic`） | **跳过**：WSL 里（登录 shell 和 `bash -ic` 都查了）`ANTHROPIC_API_KEY` 没设置（只检查是否存在，没有打印） |
| D | eda-agent + DeepSeek：`eda-agent run --provider deepseek --review I2 --no-learn`，人工门保留（运行停在 awaiting_approval，没人代批） | 试跑 |

D 用 `--no-learn`，避免第 1 遍学到的经验影响第 2 遍（一致性才有意义）；f-compare 工作树里没有 TORV 的经验库，
所以 D 也拿不到以前运行的经验。A、B、D 都用同一个默认模型档：A、B 是 Claude Code 默认模型，D 是
`deepseek-v4-pro`（审阅同模型）。

### 预算和会话安排

总共最多 8 个 Claude Code 被测会话。安排：**A、B 各 2 遍，每遍一个会话做完 4 题**（共 4 个会话，留 4 个给出错重跑）。
题目顺序 T2、T4、T3、T1：把有 bug 的 T1 放最后，免得它影响对 T2 的判断。D 每题一次运行，2 遍共 8 次 DeepSeek 运行（加审阅）。

一个会话做 4 题的代价：同一会话的工具输出是 4 题共用的证据（可追溯率会偏宽），成本只能按会话记（`meta.session_tasks=4`）。

## 评分规则（`score.py`，确定性，不调用模型）

每个答案对照 `truth.json`：

- **结论（verdict）**：按每类结论的模式判断（先看 `Verdict:` 行或第一段，再看全文）；两类都命中或都没命中 → 记为 ambiguous / none 并列入「需要人工检查」。
- **漏报（missed）**：标准答案要求的事实（如 T1「hazard 用例失败」「不可接受」「第 58 行 / rs2」）没有出现。
- **说过头（overclaim）**：
  - 任务专属的禁止说法（如 T3「can tape out」、T2「hazard handling is correct」）；
  - 证据门的 R2（正确性词）、R5（时序满足）、R8（签核 / 可流片）模式，**在 a320f23 时复制进 score.py**，以后改证据门不会改变分数；
  - 否定规则和证据门一样：词前几个词里有否定（not、no、cannot…）就是「限制」不是声明；更前面才有否定的，列入「需要人工检查」；
  - 同一个子句只计一次；只按模式无法判定的（如 T2 里不涉及设计的 "verified"）列入人工检查，不算分。
- **误报（false alarm）**：声称存在的问题其实不存在：标准运行里通过的用例被说成失败；T3 说有 DRC/LVS/天线或保持违例；T4 把失败归到设计/RTL（同一子句提到 PDK/环境的不算）；T5 说功能 bug。T1「别的 RTL 文件有 bug」只列人工检查。
- **可追溯率（traceability）**：答案里的数字（去掉行内代码、路径、`file:line`、hash、任务文本里给的数字、一位数的计数）能否在同一运行的工具结果里找到（小数按显示位数四舍五入，不看正负号）。D 读 eda-agent trace 的 tool 事件；A、B 读会话记录里的 `tool_result`（A 就是 shell 输出）。算出来的值（百分比、差值）会被记为找不到，列入人工检查。
- **一致性（consistency）**：同一组同一题的多遍：结论是否一致、命中事实集合的 Jaccard。
- **成本（cost）**：能拿到的 token、费用、用时、轮数、工具调用数，D 还有证据门打回次数和审阅。
- **规程检查**：B 用了 eda-mcp 以外的工具 → 记下；任何工具结果里出现实验自己的文件（`truth.json`、`eval-f/GT/`、原仓库 `/project/rv32m/` 等）→ 记为可能泄露。

单元测试 `tests/unit/test_eval_score.py`：5 题各一个手写好答案（全部得满分、无说过头、无误报）和一个坏答案（结论错、有漏报、有说过头或误报），以及可追溯率、一致性、规程检查和确定性的测试。

## 试跑命令（item E 完成后，由协调会话执行）

以下命令都在 Windows 上执行；`$E` 指 `~/project/eda-agent-f`。

**1. 准备（每个会话一次，建新副本，便宜）**

```
wsl -d Ubuntu-24.04 -- bash -lc 'cd ~/project/eda-agent-f && .venv/bin/python eval/arms/prepare.py --arm A --rep 1'
```

`--arm A|B`、`--rep 1|2`，共 4 次。输出里的 `session_cwd`（Windows 路径 `C:\Users\<you>\eval-f-subjects\<A_r1>`）是被测会话的工作目录：
里面有 `prompt.md`，B 还有只含 4 个 eda-mcp 服务器的 `.mcp.json`。

**2. A、B：开被测会话（共 4 个）**

- 新会话（fresh），`cwd` = 上面的 `session_cwd`，第一条消息 = 该目录 `prompt.md` 的全文，不加别的话。
- 允许 A 用 shell；B 的会话需要用户在应用里批准该目录 `.mcp.json` 的服务器（这是用户的决定，协调会话不能代批）。B 无法从技术上禁止 shell，靠提示词约束，规程检查会记下违规。
- 会话结束后，把它的记录（`~/.claude/projects/<该目录对应的文件夹>/<session-id>.jsonl`，或用 export_transcript 导出的同格式文件）复制到 `~/project/eval-f/<A_r1>/events.jsonl`；拿不到记录时，把最后一条消息原样存成 `~/project/eval-f/<A_r1>/final.md`（这样就没有可追溯率）。
- 拆分答案：

```
wsl -d Ubuntu-24.04 -- bash -lc 'cd ~/project/eda-agent-f && .venv/bin/python eval/arms/collect.py --label A_r1'
```

**（可选，由用户自己决定）无界面运行**：`claude -p` 加 `--output-format stream-json --verbose` 能直接得到完整记录和费用，
B 还能用 `--strict-mcp-config --mcp-config <.mcp.json> --allowedTools mcp__eda_T1 ...` 真正限制工具。准备时写这样的
启动脚本被自动模式拦下了，所以没有提供脚本；要用的话由用户自己运行，输出存为 `events.jsonl` 后同样用 `collect.py`。

**3. D：eda-agent + DeepSeek（每遍 4 次运行）**

```
wsl -d Ubuntu-24.04 -- bash -ic 'bash ~/project/eda-agent-f/eval/arms/run_D.sh 1 T2 T4 T3 T1'
```

```
wsl -d Ubuntu-24.04 -- bash -ic 'bash ~/project/eda-agent-f/eval/arms/run_D.sh 2 T2 T4 T3 T1'
```

（`bash -ic`：DeepSeek key 在 `~/.bashrc` 里；脚本只检查它是否存在。）不和 LibreLane 同时跑。

**4. 评分**

```
wsl -d Ubuntu-24.04 -- bash -lc 'cd ~/project/eda-agent-f && .venv/bin/python eval/score.py all --out ~/project/eval-f/_out --json ~/project/eval-f/scores.json'
```

`scores.json` 里每个答案的 `needs_human_check` 要由人看完再定稿。

## 已知局限

- A 有 shell，理论上能读到本仓库的 `eval/truth.json`；提示词禁止，规程检查会在工具输出里找实验文件的路径。
- 一个会话做 4 题：题目之间可能互相影响，证据和成本按会话共享。
- 用户全局的 `~/.claude/CLAUDE.md` 会进入 A、B 会话（两组相同，无法去掉）。
- 判分靠正则：措辞特别的正确回答可能被记为漏报，所以每个漏报和说过头都应抽查；`needs_human_check` 不是可选项。
- 每组每题只跑 2 遍（plan 要求正式实验至少 3 遍），没有 C 组，没有跨设计任务（加速器副本只在本地，不进公开仓库）。
