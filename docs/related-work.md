# Related work: what we borrowed

Ideas only, unless marked as a dependency; no code was copied.

| Project | What we took | Where we differ |
|---|---|---|
| [MCP4EDA](https://github.com/NellyW8/MCP4EDA), [mcp-verilog](https://github.com/zesun33/mcp-verilog) | EDA tools behind an MCP server; record tool versions so a result can be repeated | their tools return raw logs; ours return an envelope with an evidence level, claim scope and residual risk |
| [fpgaZeroMCP](https://github.com/lcapossio/fpgaZeroMCP) | only a fixed set of EDA commands may run | the allowlist sits under project configs, not under model input, which never reaches a command line |
| [mini-swe-agent](https://github.com/SWE-agent/mini-swe-agent) | a small hand-written loop with step and cost limits and a linear, fully logged trajectory | stop rules from the methodology: the same tool failing twice ends the run; a gate checks the final report |
| [SWE-agent](https://github.com/SWE-agent/SWE-agent) | short, stable tool feedback written for a model to read | |
| [OpenAI Agents SDK](https://github.com/openai/openai-agents-python) guardrails | an output check that can stop a run and say why | our gate is plain code over this run's tool results, and sends the reasons back for a bounded number of rewrites |
| [VerilogCoder](https://github.com/NVlabs/VerilogCoder), [MAGE](https://github.com/stable-lab/MAGE) | (planned, M3) trace a failing signal back to its drivers; debug with checkpoints | |
| [wave-mcp](https://github.com/Tencent/wave-mcp), [wavepeek](https://github.com/kleverhq/wavepeek) | (planned, M3, dependency) read waveforms | wrapped as two diagnostic tools that report values, not causes |
| [avl-core](https://github.com/projectapheleia/avl) | (dependency, `[mutation]` extra) source-level SystemVerilog mutants from the slang syntax tree; its Yosys miter for equivalent mutants | only its generator: we run the project's own regression per mutant in a copy (avl grades cocotb only), lint each mutant, and work around its byte/character offset bug |
| [MCY](https://github.com/YosysHQ/mcy) | considered for M2.5 and kept as an alternative | it mutates the synthesized netlist, so a mutant is not a readable RTL diff and the RTL regression cannot run it as is |
| [LibreLane](https://github.com/librelane/librelane) | (dependency) its container image with Yosys and OpenSTA; its ABC script, driving cell and corners for IHP SG13G2 | we run only synthesis + pre-PnR STA, and record the SDC hash and image digest in an envelope |
| [OpenROAD-MCP](https://github.com/The-OpenROAD-Project/OpenROAD-MCP), [ORFS-Agent](https://github.com/ABKGroup/ORFS-Agent) | back-end tools split into run and report; a flow iteration is a unit with its own budget | we read LibreLane's own run directory into fixed signoff rules and an envelope; parameter tuning is not done |
| [mcp-rtl-review](https://github.com/zesun33/mcp-rtl-review) | deterministic RTL review rules (blocking assignments, reset polarity, width truncation) as a reviewer without an LLM | it walks an AST; our rules come from Verilator's own warnings plus one text scan; findings feed the receipt next to a model reviewer |
| [Agent-as-a-Judge](https://github.com/metauto-ai/agent-as-a-judge) | a judge that uses tools to check the work instead of reading the author's account | the I2/I3 reviewer gets the task and raw tool results, never the author's conclusions, and re-runs the same MCP tools |
