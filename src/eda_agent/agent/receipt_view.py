"""Review summary: the one screen a person reads before `eda-agent approve` / `reject`.

Built by plain code from the receipt alone (no model, no files opened), so it can be
re-rendered at any time and never says more than the receipt does. The summary is in
English or Chinese (`lang`); tool output, file names, findings and other quoted text stay
in their original language. Everything else in the receipt stays below the summary.

    summarize(receipt, lang) -> Summary      the data, shared by both renderers
    render_summary_md(summary) -> str        top of runs/<id>.receipt.md
    render_html(receipt, lang) -> str        runs/<id>.receipt.html, self-contained
"""
from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass, field

from ..core.envelope import EVIDENCE_LEVELS
from .human_gate import AWAITING, content_sha256

LANGS = ("en", "zh")
SEVERITIES = ("high", "medium", "low", "info")
# Thresholds for "small slack": below these a pre-PnR estimate can easily turn negative after
# placement and routing. They only raise a "look at this" item; they decide nothing.
SETUP_MARGIN = 0.10   # fraction of the clock period
HOLD_MARGIN_NS = 0.10
SHORT = 220           # characters of a finding shown in the summary; full text is in the details

# What each evidence level lets a reader conclude, and what it does not (envelope.py ladder).
LEVELS = {
    "E0": {"en": ("parses / structural facts",
                  "the design parses and elaborates, or the constraints exist as stated",
                  "that the design works, or meets timing"),
           "zh": ("能解析 / 结构事实",
                  "设计能被工具解析和展开，或约束文件里的约束确实存在",
                  "设计功能正确，或满足时序")},
    "E1": {"en": ("someone's score", "a person or a model rated it", "anything measured by a tool"),
           "zh": ("人或模型的打分", "有人或模型给出了评价", "任何由工具测量得到的结论")},
    "E2": {"en": ("these tests passed",
                  "the listed test cases ran and passed by the testbench's own checks",
                  "that the design is correct, or that the tests would catch a bug (no coverage)"),
           "zh": ("这些测试通过了",
                  "列出的测试用例运行并通过（由测试平台自己判断）",
                  "设计正确，或这些测试能发现错误（没有覆盖率数据）")},
    "E3": {"en": ("bit-exact / mutation evidence",
                  "outputs match a reference bit for bit, or the tests kill injected bugs",
                  "timing, power or anything after layout"),
           "zh": ("逐位比对 / 变异测试",
                  "输出与参考模型逐位一致，或测试能发现注入的错误",
                  "时序、功耗或版图之后的任何结论")},
    "E4": {"en": ("synthesis estimate",
                  "area and timing as estimated before placement and routing, under this SDC",
                  "function, or signoff timing after placement and routing"),
           "zh": ("综合估算",
                  "在这份 SDC 约束下、布局布线之前估算出的面积和时序",
                  "功能正确，或布局布线后的签核时序")},
    "E5": {"en": ("signoff", "post-route multi-corner timing, DRC/LVS and gate-level checks passed",
                  "anything outside the signed-off corners and modes"),
           "zh": ("签核", "布线后多角时序、DRC/LVS 和门级检查通过", "签核角和模式之外的情况")},
}

# Fixed sentences the tools write into residual_risk, in Chinese. Unknown ones stay as written.
RISK_ZH = {
    "lint says nothing about function; run regress for E2": "lint 不说明功能；功能要看仿真回归（E2）",
    "test adequacy unknown: no coverage or mutation score yet": "测试是否充分未知：还没有覆盖率或变异测试分数",
    "pass/fail is reported by the test program itself": "通过/失败由测试程序自己报告",
    "only an exit status (and output pattern) was observed: no per-check detail, so which sub-tests "
    "ran is not known": "只看到退出码（和输出匹配）：没有逐项结果，不知道具体跑了哪些子测试",
    "sub-test counts come from the testbench's own summary line, not from separate results":
        "子测试数量来自测试平台自己打印的汇总行，不是逐个独立的结果",
    "pass/fail is decided by the harness's assertions; the JUnit file is the only record of what was "
    "checked": "通过/失败由测试框架的断言决定；JUnit 文件是唯一的检查记录",
    "tool versions not recorded: the project config has no [versions] table":
        "没有记录工具版本：项目配置里没有 [versions] 表",
    "pre-PnR estimate: no wire parasitics, ideal clock tree; post-route slack is usually worse":
        "布局布线前的估算：没有连线寄生、时钟树理想；布线后余量通常更差",
    "timing is only as meaningful as the SDC: I/O delays and exceptions there are assumptions":
        "时序结论只和 SDC 一样可信：其中的 I/O 延迟和例外都是假设",
    "synthesis is not checked for equivalence with the RTL and no gate-level simulation was run":
        "没有检查综合网表与 RTL 的等价性，也没有做门级仿真",
    "one library corner only: slow-corner timing was not estimated": "只有一个工艺角：没有估算慢角时序",
    "inputs without set_input_delay are not endpoints and are not listed here":
        "没有 set_input_delay 的输入不算端点，不会被列出",
    "the SDC's values (I/O budgets, uncertainty) are not judged, only whether they exist":
        "只检查约束是否存在，不判断 SDC 数值（I/O 预算、不确定度）是否合理",
    "pattern rules only: a design can pass all of them and still be wrong": "只是模式规则：全部通过的设计仍可能是错的",
    "the reset scan reads text, not the elaborated design; unusual coding styles can hide a reset":
        "复位扫描只读文本，不读展开后的设计；少见的写法可能漏掉复位",
}

T = {
    "title": {"en": "Review summary", "zh": "审核摘要"},
    "generated": {"en": "Written by plain code from the receipt data (no model). Everything else is in "
                        "the details below; quotes and tool output stay in their original language.",
                  "zh": "本摘要由程序直接根据收据数据生成（没有使用模型）。完整内容在下方“详细记录”；"
                        "引用和工具输出保留原文。"},
    "task": {"en": "Task", "zh": "任务"},
    "decide": {"en": "What you decide", "zh": "要你决定什么"},
    "status": {"en": "Status", "zh": "状态"},
    "attention": {"en": "Look at these before approving", "zh": "批准前请先看这些"},
    "attention_none": {"en": "nothing flagged by the automatic checks",
                       "zh": "自动检查没有发现需要特别注意的地方"},
    "checks": {"en": "Checks", "zh": "每项检查"},
    "col_check": {"en": "check", "zh": "检查"}, "col_result": {"en": "result", "zh": "结果"},
    "col_level": {"en": "evidence", "zh": "证据等级"}, "col_summary": {"en": "tool summary", "zh": "工具摘要"},
    "no_checks": {"en": "no check ran", "zh": "没有运行任何检查"},
    "evidence": {"en": "What the evidence allows", "zh": "证据能说明什么"},
    "highest": {"en": "Highest evidence reached: {lvl} ({words}).", "zh": "达到的最高证据：{lvl}（{words}）。"},
    "highest_fn": {"en": "On function the highest is {lvl} ({words}); E4 never speaks about function.",
                   "zh": "关于功能，最高只到 {lvl}（{words}）；E4 综合估算从不说明功能。"},
    "no_evidence": {"en": "No passing evidence: nothing may be concluded.", "zh": "没有通过的证据：不能得出任何结论。"},
    "level_line": {"en": "{lvl} ({words}): may say {may}; may NOT say {may_not}.",
                   "zh": "{lvl}（{words}）：可以说{may}；不能说{may_not}。"},
    "headline": {"en": "In one line", "zh": "一句话"},
    "h_checks": {"en": "{ok} of {n} check(s) passed", "zh": "{n} 项检查中 {ok} 项通过"},
    "h_gate": {"en": "gate {status}", "zh": "证据门{status}"},
    "h_reviews": {"en": "reviewers: {verdicts}", "zh": "评审：{verdicts}"},
    "h_attention": {"en": "{stop} serious and {warn} warning item(s) to look at",
                    "zh": "需要先看：{stop} 项严重、{warn} 项警告"},
    "not_checked": {"en": "Not checked", "zh": "没有检查的"},
    "cases_not_run": {"en": "{n} case(s) in the project's list were not run: {names}",
                      "zh": "项目用例列表中有 {n} 个没有运行：{names}"},
    "described_not_run": {"en": "read but not run: {names}", "zh": "读过说明但没有运行：{names}"},
    "no_lint": {"en": "lint was not run", "zh": "没有运行 lint"},
    "no_regress": {"en": "no simulation (regress) was run", "zh": "没有运行仿真回归（regress）"},
    "no_timing": {"en": "no synthesis or timing estimate was run", "zh": "没有运行综合或时序估算"},
    "gate": {"en": "Evidence gate", "zh": "证据门"},
    "gate_passed": {"en": "passed", "zh": "通过"}, "gate_rejected": {"en": "REJECTED", "zh": "拒绝"},
    "gate_not_run": {"en": "not run", "zh": "未运行"},
    "gate_line": {"en": "{status}; the agent's report was sent back for a rewrite {n} time(s)",
                  "zh": "{status}；智能体的报告被退回重写 {n} 次"},
    "reviews": {"en": "Reviewers", "zh": "评审"},
    "no_reviews": {"en": "no independent review was run", "zh": "没有运行独立评审"},
    "rules_who": {"en": "rule-based (no model)", "zh": "规则评审（无模型）"},
    "times": {"en": " (×{n}, identical)", "zh": "（×{n}，内容相同）"},
    "worst": {"en": "most serious", "zh": "最严重"},
    "no_findings": {"en": "no findings", "zh": "没有发现问题"},
    "approval": {"en": "Approval", "zh": "审批"},
    "commands": {"en": "Commands", "zh": "命令"},
    "details": {"en": "Details (full receipt)", "zh": "详细记录（完整收据）"},
    "and_more": {"en": "… and {n} more (see details)", "zh": "……还有 {n} 条（见详细记录）"},
    # decisions
    "d_pending": {"en": "Approve or reject step '{step}': accept the agent's final report below as the "
                        "result of this task? (requested because: {why})",
                  "zh": "批准或拒绝步骤“{step}”：是否接受下方智能体的最终报告作为这个任务的结果？"
                        "（请求原因：{why}）"},
    "d_decided": {"en": "Already decided: {status} by {by} at {at}{reason}. Decisions are final; "
                        "this summary was re-rendered afterwards.",
                  "zh": "已经决定：{by} 于 {at} {status}{reason}。决定是最终的；本摘要是之后重新生成的。"},
    "d_gate": {"en": "The evidence gate rejected the agent's report. Approving it overrides the gate "
                     "(needs --override-gate and a reason); rejecting it closes the run.",
               "zh": "证据门拒绝了智能体的报告。批准就等于推翻证据门（需要 --override-gate 和理由）；"
                     "拒绝则关闭这次运行。"},
    "d_none": {"en": "No human decision was requested for this run (status {status}); nothing to decide.",
               "zh": "这次运行没有请求人工审批（状态 {status}），无需决定。"},
    "why_gate_passed": {"en": "the evidence gate passed; a person accepts the result",
                        "zh": "证据门已通过；需要一个人接受结果"},
    "approved": {"en": "approved", "zh": "批准"}, "rejected": {"en": "rejected", "zh": "拒绝"},
    "pending": {"en": "pending", "zh": "等待决定"},
    "a_none": {"en": "no approval record", "zh": "没有审批记录"},
    "a_line": {"en": "{status}", "zh": "{status}"},
    "a_by": {"en": "{status} by {by} at {at}", "zh": "{by} 于 {at} {status}"},
    "c_final": {"en": "already decided; decisions are final (a new run fixes a rejected one)",
                "zh": "已经决定；决定是最终的（被拒绝的运行要用新的运行来修正）"},
    # attention items
    "i_override": {"en": "This run was approved over the evidence gate's rejection.",
                   "zh": "这次运行是在证据门拒绝后被强行批准的（--override-gate）。"},
    "i_changed": {"en": "The receipt content changed after the decision (sha256 at decision {old}…, now "
                        "{new}…): the decision was made on a different receipt.",
                  "zh": "决定之后收据内容变了（决定时 sha256 {old}…，现在 {new}…）：做决定时看到的不是这份收据。"},
    "i_gate_rejected": {"en": "The evidence gate rejected the final report.",
                        "zh": "证据门拒绝了最终报告。"},
    "i_rewrites": {"en": "The gate sent the agent's report back {n} time(s) before it passed; reason: {why}",
                   "zh": "证据门在通过前退回报告 {n} 次；原因：{why}"},
    "i_stop": {"en": "The run did not end normally (stop reason: {why}).",
               "zh": "运行没有正常结束（停止原因：{why}）。"},
    "i_check_failed": {"en": "{tool} ended with status {status}: {summary}",
                       "zh": "{tool} 的状态是 {status}：{summary}"},
    "i_failed_cases": {"en": "{tool}: {n} case(s) failed", "zh": "{tool}：{n} 个用例失败"},
    "i_skipped_cases": {"en": "{tool}: {n} case(s) skipped", "zh": "{tool}：{n} 个用例被跳过"},
    "i_subtests": {"en": "{tool}: {cases} case(s) passed, which the testbench counts as {passed} of {total} "
                         "sub-tests ({failed} failed); these numbers are the testbench's own summary line.",
                   "zh": "{tool}：{cases} 个用例通过，测试平台自己报告为 {total} 个子测试中通过 {passed} 个"
                         "（失败 {failed} 个）；这些数字来自测试平台的汇总行。"},
    "i_exitcode": {"en": "{tool}: pass/fail is one exit status per case; which sub-tests ran is not known.",
                   "zh": "{tool}：每个用例只有一个退出码；不知道里面具体跑了哪些子测试。"},
    "i_timing_failed": {"en": "{tool}: timing NOT met (setup WNS {wns} ns, hold WNS {hold} ns).",
                        "zh": "{tool}：时序不满足（建立 WNS {wns} ns，保持 WNS {hold} ns）。"},
    "i_timing_void": {"en": "{tool}: this timing result is void, the SDC changed later in the run.",
                      "zh": "{tool}：这个时序结果作废，运行中后来换了 SDC。"},
    "i_setup_small": {"en": "{tool}: small setup slack, {wns} ns = {pct:.0f}% of the {period} ns clock "
                            "(pre-PnR; usually worse after routing).",
                      "zh": "{tool}：建立时间余量小，{wns} ns，只占 {period} ns 时钟的 {pct:.0f}%"
                            "（布局布线前估算，布线后通常更差）。"},
    "i_hold_small": {"en": "{tool}: small hold slack, {hold} ns (corner {corner}); real clock skew after "
                           "layout can eat it.",
                     "zh": "{tool}：保持时间余量小，{hold} ns；版图后真实时钟偏斜可能吃掉它。"},
    "i_versions": {"en": "No tool versions recorded for: {tools}. The result cannot be tied to a tool release.",
                   "zh": "以下检查没有记录工具版本：{tools}。无法确定结果对应哪个工具版本。"},
    "i_tool_errors": {"en": "{n} tool call(s) ended in an error during the run.",
                      "zh": "运行中有 {n} 次工具调用出错。"},
    "i_not_run": {"en": "Only part of the regression ran: {n} listed case(s) were not run.",
                  "zh": "只跑了部分回归：列表中有 {n} 个用例没有运行。"},
    "i_described": {"en": "Read but not run: {names}.", "zh": "读过说明但没有运行：{names}。"},
    "i_no_lint": {"en": "lint was not run.", "zh": "没有运行 lint。"},
    "i_sdc_origin": {"en": "Where the SDC came from: {origin}", "zh": "SDC 的来源：{origin}"},
    "i_review_reject": {"en": "Reviewer {who} said REJECT.", "zh": "评审“{who}”的结论是“拒绝”。"},
    "i_finding": {"en": "[{sev}] from {who}: {text}", "zh": "[{sev}] 来自“{who}”：{text}"},
    "i_medium": {"en": "{n} more medium finding(s) from the reviewers (see Reviewers and the details).",
                 "zh": "评审还有 {n} 条中等严重度的发现（见“评审”和详细记录）。"},
    "i_review_error": {"en": "A review failed to run: {err}", "zh": "有一个评审没能运行：{err}"},
    "i_no_review": {"en": "No independent review was run: only the gate checked the report.",
                    "zh": "没有独立评审：只有证据门检查过报告。"},
    "i_format": {"en": "Reviewer {who} did not follow the required format (checklist first).",
                 "zh": "评审“{who}”没有按规定格式写（先写检查清单）。"},
    "i_note": {"en": "Note on reviewer {who}: {note}", "zh": "关于评审“{who}”的说明：{note}"},
}

VERDICT = {"accept": {"en": "accept", "zh": "接受"},
           "accept_with_findings": {"en": "accept with findings", "zh": "接受，但有问题"},
           "reject": {"en": "REJECT", "zh": "拒绝"},
           "": {"en": "(no verdict)", "zh": "（没有结论）"}}
STATUS = {"pass": {"en": "pass", "zh": "通过"}, "fail": {"en": "FAIL", "zh": "失败"},
          "error": {"en": "ERROR", "zh": "出错"}, "timeout": {"en": "TIMEOUT", "zh": "超时"}}
RUN_STATUS = {"awaiting_approval": {"en": "awaiting approval", "zh": "等待人工审批"},
              "approved": {"en": "approved", "zh": "已批准"}, "rejected": {"en": "rejected", "zh": "已拒绝"},
              "done": {"en": "done (no approval requested)", "zh": "完成（未请求审批）"},
              "pending": {"en": "pending (gate rejected)", "zh": "待定（证据门拒绝）"}}


def t(lang: str, key: str, **kw) -> str:
    return T[key][lang].format(**kw)


def _word(table: dict, key: str, lang: str) -> str:
    return table.get(key, {}).get(lang, key)


def risk_text(risk: str, lang: str) -> str:
    return RISK_ZH.get(risk, risk) if lang == "zh" else risk


def _short(text: str, n: int = SHORT) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1].rstrip() + "…"


@dataclass
class Item:
    """One "look at this" line. level: stop (blocks a plain approve, or contradicts it), warn, info.
    quote: verbatim text from the receipt, kept in its original language."""
    level: str
    text: str
    quote: str = ""


@dataclass
class Summary:
    lang: str
    run_id: str
    task: str
    run_line: str
    status: str
    decision: str
    attention: list[Item] = field(default_factory=list)
    checks: list[dict] = field(default_factory=list)
    evidence: list[str] = field(default_factory=list)
    not_checked: list[str] = field(default_factory=list)
    gate: str = ""
    gate_reasons: list[str] = field(default_factory=list)
    reviews: list[dict] = field(default_factory=list)
    approval: str = ""
    commands: list[str] = field(default_factory=list)
    commands_note: str = ""
    headline: str = ""


def _passing_levels(r: dict) -> list[str]:
    levels = {c["evidence_level"] for c in r["checks_run"] if c["status"] == "pass"}
    return [lvl for lvl in reversed(EVIDENCE_LEVELS) if lvl in levels]


def _counts(summary: str) -> dict:
    """Case and sub-test counts from a regress summary line ("1 passed, 0 failed, 0 skipped;
    testbench sub-tests: 90 passed, 0 failed of 90"); older receipts keep only this text."""
    out = {}
    m = re.search(r"(\d+) passed, (\d+) failed(?:, (\d+) skipped)?", summary)
    if m:
        out |= {"passed": int(m[1]), "failed": int(m[2]), "skipped": int(m[3] or 0)}
    m = re.search(r"sub-tests: (\d+) passed, (\d+) failed of (\d+)", summary)
    if m:
        out |= {"sub_passed": int(m[1]), "sub_failed": int(m[2]), "sub_total": int(m[3])}
    return out


def _reviewer_name(v: dict, lang: str) -> str:
    if v["reviewer"] == "rules":
        return t(lang, "rules_who")
    return f"{v['reviewer']} ({v.get('model') or '?'})"


def unique_reviews(r: dict) -> list[tuple[dict, int]]:
    """Reviews with identical content (e.g. the rule-based review written twice) shown once, counted."""
    seen: dict[str, list] = {}
    for v in r.get("reviews", []):
        key = json.dumps([v["reviewer"], v.get("model"), v.get("verdict"),
                          [(f["severity"], f["text"]) for f in v.get("findings", [])]], ensure_ascii=False)
        seen.setdefault(key, [v, 0])[1] += 1
    return [(v, n) for v, n in seen.values()]


def _sev_rank(sev: str) -> int:
    return SEVERITIES.index(sev) if sev in SEVERITIES else len(SEVERITIES)


def _review_rank(v: dict) -> tuple:
    worst = min((_sev_rank(f["severity"]) for f in v.get("findings", [])), default=len(SEVERITIES))
    return (0 if v.get("verdict") == "reject" else 1, worst)


def summarize(r: dict, lang: str = "en") -> Summary:
    if lang not in LANGS:
        raise ValueError(f"unknown language {lang!r}; use one of {LANGS}")
    a = r.get("approval") or {}
    s = Summary(lang=lang, run_id=r["run_id"], task=r["task"],
                run_line=f"{r['project']} · {r['provider']} / {r['model']}"
                         + (f" · prompt {r['prompt']}" if r.get("prompt") else "")
                         + (f" · preset {r['preset']}" if r.get("preset") else ""),
                status=_word(RUN_STATUS, r["status"], lang), decision="")
    _decision(s, r, a, lang)
    _checks(s, r, lang)
    _evidence(s, r, lang)
    _not_checked(s, r, lang)
    _gate(s, r, lang)
    _reviews(s, r, lang)
    s.attention = _attention(r, a, lang)
    s.headline = _headline(s, r, lang)
    return s


def _headline(s: Summary, r: dict, lang: str) -> str:
    sep = "；" if lang == "zh" else "; "
    parts = [t(lang, "h_checks", ok=sum(c["status"] == "pass" for c in s.checks), n=len(s.checks)),
             t(lang, "h_gate", status=t(lang, {"passed": "gate_passed", "rejected": "gate_rejected"}
                                         .get(r["gate"]["status"], "gate_not_run")))]
    if s.reviews:
        verdicts = {}
        for v in s.reviews:
            verdicts[v["verdict_text"]] = verdicts.get(v["verdict_text"], 0) + 1
        parts.append(t(lang, "h_reviews", verdicts=("、" if lang == "zh" else ", ").join(
            (f"{k} {n} 个" if lang == "zh" else f"{n}× {k}") for k, n in verdicts.items())))
    parts.append(t(lang, "h_attention", stop=sum(i.level == "stop" for i in s.attention),
                   warn=sum(i.level == "warn" for i in s.attention)))
    return sep.join(parts) + ("。" if lang == "zh" else ".")


def _decision(s: Summary, r: dict, a: dict, lang: str) -> None:
    rid = r["run_id"]
    status = a.get("status")
    if status in ("approved", "rejected"):
        reason = f" ({a['reason']})" if a.get("reason") else ""
        s.decision = t(lang, "d_decided", status=_word(T, status, lang), by=a["by"], at=a["at"], reason=reason)
        s.approval = t(lang, "a_by", status=_word(T, status, lang), by=a["by"], at=a["at"]) + reason
        s.commands_note = t(lang, "c_final")
    elif r["status"] == AWAITING:
        why = a.get("why") or ""
        why = t(lang, "why_gate_passed") if why == T["why_gate_passed"]["en"] else why
        s.decision = t(lang, "d_pending", step=a.get("step") or "final report", why=why)
        s.approval = t(lang, "a_line", status=_word(T, "pending", lang))
        s.commands = [f"eda-agent approve {rid}", f'eda-agent reject {rid} --reason "..."']
    elif r["status"] == "pending":
        s.decision = t(lang, "d_gate")
        s.approval = t(lang, "a_none")
        s.commands = [f'eda-agent reject {rid} --reason "..."',
                      f'eda-agent approve {rid} --override-gate --reason "..."']
    else:
        s.decision = t(lang, "d_none", status=r["status"])
        s.approval = t(lang, "a_none")


def _checks(s: Summary, r: dict, lang: str) -> None:
    for c in r["checks_run"]:
        lvl = c["evidence_level"]
        s.checks.append({
            "tool": c["tool"], "args": json.dumps(c["args"], ensure_ascii=False) if c["args"] else "",
            "status": c["status"], "status_text": _word(STATUS, c["status"], lang),
            "level": lvl, "level_text": LEVELS[lvl][lang][0] if lvl in LEVELS else lvl,
            "summary": c["summary"].splitlines()[0] if c["summary"] else "",
            "void": bool((c.get("timing") or {}).get("void")),
        })


def _evidence(s: Summary, r: dict, lang: str) -> None:
    levels = _passing_levels(r)
    if not levels:
        s.evidence.append(t(lang, "no_evidence"))
        return
    top = levels[0]
    s.evidence.append(t(lang, "highest", lvl=top, words=LEVELS[top][lang][0]))
    functional = [lvl for lvl in levels if lvl in ("E2", "E3", "E5")]
    if top == "E4" and functional:
        s.evidence.append(t(lang, "highest_fn", lvl=functional[0], words=LEVELS[functional[0]][lang][0]))
    for lvl in levels:
        words, may, may_not = LEVELS[lvl][lang]
        s.evidence.append(t(lang, "level_line", lvl=lvl, words=words, may=may, may_not=may_not))


def _not_checked(s: Summary, r: dict, lang: str) -> None:
    nr = r["not_run"]
    tools = {c["tool"] for c in r["checks_run"]}
    if nr["cases_not_run"]:
        names = ", ".join(nr["cases_not_run"][:8]) + (" …" if len(nr["cases_not_run"]) > 8 else "")
        s.not_checked.append(t(lang, "cases_not_run", n=len(nr["cases_not_run"]), names=names))
    if nr["described_but_not_run"]:
        s.not_checked.append(t(lang, "described_not_run", names=", ".join(nr["described_but_not_run"])))
    if not nr["lint_run"]:
        s.not_checked.append(t(lang, "no_lint"))
    if "regress" not in tools:
        s.not_checked.append(t(lang, "no_regress"))
    if not any(c.get("timing") for c in r["checks_run"]):
        s.not_checked.append(t(lang, "no_timing"))
    s.not_checked += [risk_text(x, lang) for x in r["residual_risk"]["from_tools"]]


def _gate(s: Summary, r: dict, lang: str) -> None:
    g = r["gate"]
    status = t(lang, {"passed": "gate_passed", "rejected": "gate_rejected"}.get(g["status"], "gate_not_run"))
    s.gate = t(lang, "gate_line", status=status, n=g["rewrites"])
    s.gate_reasons = [reason for d in g["decisions"] if not d["passed"] for reason in d["reasons"]]


def _reviews(s: Summary, r: dict, lang: str) -> None:
    for v, n in sorted(unique_reviews(r), key=lambda x: _review_rank(x[0])):
        findings = sorted(v.get("findings", []), key=lambda f: _sev_rank(f["severity"]))
        counts = {sev: sum(f["severity"] == sev for f in findings) for sev in SEVERITIES}
        s.reviews.append({
            "who": _reviewer_name(v, lang) + (t(lang, "times", n=n) if n > 1 else ""),
            "verdict": v.get("verdict") or "", "verdict_text": _word(VERDICT, v.get("verdict") or "", lang),
            "counts": {k: c for k, c in counts.items() if c},
            "worst": findings[0] if findings else None,
        })


def _attention(r: dict, a: dict, lang: str) -> list[Item]:
    items: list[Item] = []
    add = lambda level, key, quote="", **kw: items.append(Item(level, t(lang, key, **kw), quote))  # noqa: E731

    # The decision itself
    if a.get("overrode_gate"):
        add("stop", "i_override")
    if a.get("receipt_sha256") and a["receipt_sha256"] != content_sha256(r):
        add("stop", "i_changed", old=a["receipt_sha256"][:12], new=content_sha256(r)[:12])
    if r["gate"]["status"] == "rejected":
        add("stop", "i_gate_rejected")
    if r["stop_reason"] not in ("end", "gate_rejected"):
        add("warn", "i_stop", why=r["stop_reason"])

    # Reviewers: a reject first, then every high finding (each once), then a count of medium ones
    reviews = sorted(unique_reviews(r), key=lambda x: _review_rank(x[0]))
    for v, _ in reviews:
        if v.get("verdict") == "reject":
            add("stop", "i_review_reject", who=_reviewer_name(v, lang))
    seen, medium = set(), 0
    for v, _ in reviews:
        for f in v.get("findings", []):
            key = " ".join(f["text"].split())
            if key in seen:
                continue
            seen.add(key)
            if f["severity"] == "high":
                items.append(Item("warn", t(lang, "i_finding", sev=f["severity"], who=_reviewer_name(v, lang),
                                             text=""), quote=f["text"]))
            elif f["severity"] == "medium":
                medium += 1
    if medium:
        add("info", "i_medium", n=medium)

    # The checks themselves
    for c in r["checks_run"]:
        if c["status"] != "pass":
            add("stop", "i_check_failed", tool=c["tool"], status=c["status"], summary="",
                quote=c["summary"].splitlines()[0] if c["summary"] else "")
        if c["tool"] == "regress":
            n = _counts(c["summary"])
            if n.get("failed"):
                add("stop", "i_failed_cases", tool=c["tool"], n=n["failed"])
            if n.get("skipped"):
                add("warn", "i_skipped_cases", tool=c["tool"], n=n["skipped"])
            if "sub_total" in n:
                add("info", "i_subtests", tool=c["tool"], cases=n.get("passed", "?"), passed=n["sub_passed"],
                    failed=n["sub_failed"], total=n["sub_total"])
            elif n.get("passed") and c["args"].get("cases") == ["all"]:
                add("info", "i_exitcode", tool=c["tool"])
        tm = c.get("timing")
        if tm:
            if tm.get("void"):
                add("warn", "i_timing_void", tool=c["tool"])
            if tm.get("timing_met") is False:
                add("stop", "i_timing_failed", tool=c["tool"], wns=tm.get("wns"), hold=tm.get("hold_wns"))
            else:
                period, wns, hold = tm.get("clock_period"), tm.get("wns"), tm.get("hold_wns")
                if period and wns is not None and wns < SETUP_MARGIN * period:
                    add("warn", "i_setup_small", tool=c["tool"], wns=f"{wns:+.3f}", period=period,
                        pct=100 * wns / period)
                if hold is not None and hold < HOLD_MARGIN_NS:
                    add("warn", "i_hold_small", tool=c["tool"], hold=f"{hold:+.3f}", corner=tm.get("worst_corner"))
    missing = [c["tool"] for c in r["checks_run"] if not c.get("tool_versions")]
    if missing:
        add("warn", "i_versions", tools=", ".join(missing))
    if r.get("tool_errors"):
        add("warn", "i_tool_errors", n=r["tool_errors"])
    if r.get("sdc_origin"):
        add("warn", "i_sdc_origin", origin="", quote=r["sdc_origin"])

    # What was not run
    nr = r["not_run"]
    if nr["described_but_not_run"]:
        add("warn", "i_described", names=", ".join(nr["described_but_not_run"]))
    if nr["cases_not_run"]:
        add("info", "i_not_run", n=len(nr["cases_not_run"]))
    if not nr["lint_run"]:
        add("info", "i_no_lint")

    # Process: gate rewrites, review problems
    if r["gate"]["rewrites"]:
        first = next((d["reasons"][0] for d in r["gate"]["decisions"] if not d["passed"] and d["reasons"]), "")
        add("warn", "i_rewrites", n=r["gate"]["rewrites"], why="", quote=first)
    if r.get("review_error"):
        add("warn", "i_review_error", err="", quote=r["review_error"])
    if not r.get("reviews") and not r.get("review_error"):
        add("info", "i_no_review")
    for v, _ in reviews:
        if not v.get("format_ok", True):
            add("info", "i_format", who=_reviewer_name(v, lang))
        if v.get("note"):
            add("info", "i_note", who=_reviewer_name(v, lang), note="", quote=v["note"])
    order = {"stop": 0, "warn": 1, "info": 2}
    return sorted(items, key=lambda i: order[i.level])


# ---------------------------------------------------------------- Markdown

MARK = {"stop": "🛑", "warn": "⚠️", "info": "ℹ️"}
BADGE_MD = {"pass": "✅", "fail": "❌", "error": "❌", "timeout": "❌"}


def _md(text: str) -> str:
    """One line, safe inside a table cell."""
    return " ".join(str(text).split()).replace("|", "\\|")


def _item_md(i: Item, colon: str) -> str:
    text = i.text.rstrip(":： ")
    return f"- {MARK[i.level]} {text}" + (f"{colon}{_md(_short(i.quote))}" if i.quote else "")


def render_summary_md(s: Summary) -> str:
    L = s.lang
    col = "：" if L == "zh" else ": "
    out = [f"# {t(L, 'title')} · {s.run_id}", "", f"> {t(L, 'generated')}", "",
           f"**{t(L, 'headline')}{col.strip()}** {s.headline}", "",
           f"- **{t(L, 'task')}**{col}{s.task}",
           f"- **{t(L, 'decide')}**{col}{s.decision}",
           f"- **{t(L, 'status')}**{col}{s.status} · {s.run_line}", ""]
    if s.commands:
        out += ["```bash", *s.commands, "```", ""]
    elif s.commands_note:
        out += [f"*{t(L, 'commands')}{col}{s.commands_note}*", ""]
    out += [f"## {t(L, 'attention')}", ""]
    out += [_item_md(i, col) for i in s.attention] or [f"- {t(L, 'attention_none')}"]
    out += ["", f"## {t(L, 'checks')}", ""]
    if s.checks:
        out += [f"| {t(L, 'col_check')} | {t(L, 'col_result')} | {t(L, 'col_level')} | {t(L, 'col_summary')} |",
                "|---|---|---|---|"]
        for c in s.checks:
            name = c["tool"] + (f" `{_md(c['args'])}`" if c["args"] else "")
            out.append(f"| {name} | {BADGE_MD.get(c['status'], '❔')} {c['status_text']} "
                       f"| {c['level']} {c['level_text']} | {_md(c['summary'])} |")
    else:
        out.append(f"- {t(L, 'no_checks')}")
    out += ["", f"## {t(L, 'evidence')}", ""] + [f"- {x}" for x in s.evidence]
    out += ["", f"## {t(L, 'not_checked')}", ""] + [f"- {x}" for x in s.not_checked]
    out += ["", f"## {t(L, 'gate')}", "", f"- {s.gate}"] + [f"  - {_md(x)}" for x in s.gate_reasons]
    out += ["", f"## {t(L, 'reviews')}", ""]
    for v in s.reviews:
        counts = " · ".join(f"{k} {n}" for k, n in v["counts"].items()) or t(L, "no_findings")
        line = f"- **{v['who']}**{col}{v['verdict_text']} ({counts})"
        if v["worst"] and v["worst"]["severity"] in ("high", "medium"):
            line += f"; {t(L, 'worst')}{col}[{v['worst']['severity']}] {_md(_short(v['worst']['text'], 160))}"
        out.append(line)
    if not s.reviews:
        out.append(f"- {t(L, 'no_reviews')}")
    out += ["", f"## {t(L, 'approval')}", "", f"- {s.approval}"]
    return "\n".join(out) + "\n"


# ---------------------------------------------------------------- HTML

CSS = """
:root{--bg:#fbfaf7;--fg:#1d1d1f;--muted:#5f6368;--card:#fff;--line:#e3e1dc;--code:#f3f1ec;
--ok:#1e7a3c;--ok-bg:#e3f4e8;--bad:#b3261e;--bad-bg:#fbe5e3;--warn:#8a5a00;--warn-bg:#fdf1d6;
--info:#245a9c;--info-bg:#e5eefa;--accent:#3d5a80}
@media (prefers-color-scheme:dark){:root{--bg:#17181a;--fg:#e8e6e3;--muted:#a3a7ad;--card:#202225;
--line:#34373b;--code:#2a2c30;--ok:#7fd49a;--ok-bg:#1d3324;--bad:#ff9b92;--bad-bg:#3d1f1d;
--warn:#f2c46b;--warn-bg:#3a2f17;--info:#9cc1f2;--info-bg:#1c2a3d;--accent:#9db6d8}}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:16px/1.6 -apple-system,"Segoe UI","PingFang SC",
"Microsoft YaHei","Noto Sans CJK SC",system-ui,sans-serif}
main{max-width:980px;margin:0 auto;padding:24px 16px 64px}
h1{font-size:1.5rem;margin:0 0 4px}h2{font-size:1.15rem;margin:28px 0 8px;color:var(--accent)}
h3{font-size:1rem;margin:16px 0 6px}
.sub{color:var(--muted);font-size:.9rem;margin:0 0 16px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:16px 20px;margin:16px 0}
.decide{border-left:5px solid var(--accent)}
.k{font-weight:600;color:var(--muted);font-size:.85rem;text-transform:uppercase;letter-spacing:.03em}
.badge{display:inline-block;padding:1px 9px;border-radius:999px;font-size:.82rem;font-weight:600;
white-space:nowrap;border:1px solid transparent}
.ok{color:var(--ok);background:var(--ok-bg)}.bad{color:var(--bad);background:var(--bad-bg)}
.warn{color:var(--warn);background:var(--warn-bg)}.info{color:var(--info);background:var(--info-bg)}
.lvl{color:var(--accent);background:var(--code)}
ul.att{list-style:none;padding:0;margin:0}
ul.att li{padding:8px 12px;border-radius:8px;margin:6px 0}
ul.att li.stop{background:var(--bad-bg)}ul.att li.warn{background:var(--warn-bg)}
ul.att li.info{background:var(--info-bg)}
.q{font-family:ui-monospace,Consolas,monospace;font-size:.86rem;overflow-wrap:anywhere}
table{border-collapse:collapse;width:100%;font-size:.92rem}
th,td{text-align:left;vertical-align:top;padding:6px 8px;border-bottom:1px solid var(--line)}
th{color:var(--muted);font-weight:600}
code,pre{font-family:ui-monospace,Consolas,monospace;font-size:.86rem;background:var(--code);border-radius:5px}
code{padding:1px 5px;overflow-wrap:anywhere}
pre{padding:12px;white-space:pre-wrap;overflow-wrap:anywhere;margin:8px 0}
pre.cmd{border-left:4px solid var(--accent)}
details{background:var(--card);border:1px solid var(--line);border-radius:10px;margin:10px 0;padding:0 16px}
details>summary{cursor:pointer;padding:12px 0;font-weight:600}
details[open]>summary{border-bottom:1px solid var(--line);margin-bottom:8px}
.muted{color:var(--muted)}
.headline{font-size:1.05rem;margin:0 0 12px;padding-bottom:10px;border-bottom:1px solid var(--line)}
.tbl{overflow-x:auto}
"""

ITEM_CLASS = {"stop": "stop", "warn": "warn", "info": "info"}
STATUS_CLASS = {"pass": "ok", "fail": "bad", "error": "bad", "timeout": "bad"}
VERDICT_CLASS = {"accept": "ok", "accept_with_findings": "warn", "reject": "bad"}
SEV_CLASS = {"high": "bad", "medium": "warn", "low": "info", "info": "lvl"}
RUN_CLASS = {"approved": "ok", "rejected": "bad", "awaiting_approval": "warn", "pending": "bad", "done": "lvl"}
ICON = {"stop": "■", "warn": "▲", "info": "●"}


def e(text) -> str:
    return html.escape(str(text), quote=True)


def _badge(text: str, cls: str) -> str:
    return f'<span class="badge {cls}">{e(text)}</span>'


def render_html(r: dict, lang: str = "en") -> str:
    s = summarize(r, lang)
    L = lang
    h = [f'<!doctype html><html lang="{"zh-CN" if L == "zh" else "en"}"><head><meta charset="utf-8">',
         '<meta name="viewport" content="width=device-width,initial-scale=1">',
         f"<title>{e(t(L, 'title'))} {e(r['run_id'])}</title><style>{CSS}</style></head><body><main>"]
    h.append(f"<h1>{e(t(L, 'title'))} · {e(r['run_id'])} "
             f"{_badge(s.status, RUN_CLASS.get(r['status'], 'lvl'))}</h1>")
    h.append(f'<p class="sub">{e(t(L, "generated"))}</p>')

    colon = "：" if L == "zh" else ": "
    # Decision card
    h.append('<div class="card decide">')
    h.append(f'<p class="headline"><b>{e(t(L, "headline"))}{colon.strip()}</b> {e(s.headline)}</p>')
    h.append(f'<div class="k">{e(t(L, "task"))}</div><p>{e(r["task"])}</p>')
    h.append(f'<div class="k">{e(t(L, "decide"))}</div><p>{e(s.decision)}</p>')
    if s.commands:
        h.append(f'<div class="k">{e(t(L, "commands"))}</div><pre class="cmd">{e(chr(10).join(s.commands))}</pre>')
    elif s.commands_note:
        h.append(f'<p class="muted">{e(t(L, "commands"))}: {e(s.commands_note)}</p>')
    h.append(f'<p class="muted">{e(s.run_line)}</p></div>')

    # Attention
    h.append(f"<h2>{e(t(L, 'attention'))}</h2>")
    if s.attention:
        h.append('<ul class="att">')
        for i in s.attention:
            quote = f'<span class="q" title="{e(i.quote)}">{e(_short(i.quote))}</span>' if i.quote else ""
            h.append(f'<li class="{ITEM_CLASS[i.level]}">{ICON[i.level]} {e(i.text.rstrip(":： "))}'
                     f'{colon if i.quote else ""}{quote}</li>')
        h.append("</ul>")
    else:
        h.append(f'<p class="muted">{e(t(L, "attention_none"))}</p>')

    # Checks
    h.append(f"<h2>{e(t(L, 'checks'))}</h2>")
    if s.checks:
        h.append(f'<div class="tbl"><table><tr><th>{e(t(L, "col_check"))}</th><th>{e(t(L, "col_result"))}</th>'
                 f'<th>{e(t(L, "col_level"))}</th><th>{e(t(L, "col_summary"))}</th></tr>')
        for c in s.checks:
            args = f" <code>{e(c['args'])}</code>" if c["args"] else ""
            h.append(f"<tr><td><b>{e(c['tool'])}</b>{args}</td>"
                     f"<td>{_badge(c['status_text'], STATUS_CLASS.get(c['status'], 'warn'))}</td>"
                     f"<td>{_badge(c['level'], 'lvl')} {e(c['level_text'])}</td><td>{e(c['summary'])}</td></tr>")
        h.append("</table></div>")
    else:
        h.append(f'<p class="muted">{e(t(L, "no_checks"))}</p>')

    h.append(f"<h2>{e(t(L, 'evidence'))}</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in s.evidence) + "</ul>")
    h.append(f"<h2>{e(t(L, 'not_checked'))}</h2><ul>" + "".join(f"<li>{e(x)}</li>" for x in s.not_checked)
             + "</ul>")
    gate_cls = {"passed": "ok", "rejected": "bad"}.get(r["gate"]["status"], "warn")
    gate_word = t(L, {"passed": "gate_passed", "rejected": "gate_rejected"}.get(r["gate"]["status"], "gate_not_run"))
    h.append(f"<h2>{e(t(L, 'gate'))}</h2><p>{_badge(gate_word, gate_cls)} {e(s.gate)}</p>")
    if s.gate_reasons:
        h.append("<ul>" + "".join(f'<li class="q">{e(x)}</li>' for x in s.gate_reasons) + "</ul>")

    h.append(f"<h2>{e(t(L, 'reviews'))}</h2>")
    if s.reviews:
        h.append("<ul>")
        for v in s.reviews:
            counts = " ".join(_badge(f"{k} {n}", SEV_CLASS[k]) for k, n in v["counts"].items()) \
                or e(t(L, "no_findings"))
            line = (f"<li><b>{e(v['who'])}</b>: {_badge(v['verdict_text'], VERDICT_CLASS.get(v['verdict'], 'warn'))}"
                    f" {counts}")
            if v["worst"] and v["worst"]["severity"] in ("high", "medium"):
                line += (f'<br><span class="muted">{e(t(L, "worst"))}:</span> '
                         f'<span class="q">[{e(v["worst"]["severity"])}] {e(_short(v["worst"]["text"]))}</span>')
            h.append(line + "</li>")
        h.append("</ul>")
    else:
        h.append(f'<p class="muted">{e(t(L, "no_reviews"))}</p>')
    h.append(f"<h2>{e(t(L, 'approval'))}</h2><p>{e(s.approval)}</p>")

    h.append(f"<h2>{e(t(L, 'details'))}</h2>")
    h += _details_html(r)
    h.append("</main></body></html>")
    return "\n".join(h) + "\n"


def _details_html(r: dict) -> list[str]:
    """Everything in the receipt, in English (it is mostly tool output and quotes), collapsed."""
    def sec(title: str, body: str, open_: bool = False) -> str:
        return f"<details{' open' if open_ else ''}><summary>{e(title)}</summary>{body}</details>"

    def ul(items) -> str:
        items = list(items)
        return "<ul>" + "".join(f"<li>{x}</li>" for x in items) + "</ul>" if items else '<p class="muted">(none)</p>'

    gate = r["gate"]
    usage = r.get("usage") or {}
    out = [sec("Run", ul([
        f"<b>Task:</b> {e(r['task'])}",
        f"<b>Project:</b> {e(r['project'])} · <b>Model:</b> {e(r['provider'])} / {e(r['model'])} · "
        f"<b>Prompt:</b> {e(r.get('prompt') or 'n/a')}"
        + (f" · <b>Preset:</b> {e(r['preset'])}" if r.get("preset") else ""),
        f"<b>Stop:</b> {e(r['stop_reason'])} · <b>Status:</b> {e(r['status'])}"
        + (f" (agent: {e(r['agent_status'])})" if r.get("agent_status") else "")
        + f" · <b>Gate:</b> {e(gate['status'])} ({gate['rewrites']} rewrite(s))",
        f"<b>Cost:</b> {r['turns']} turns, {r['tool_calls']} tool calls ({r['tool_errors']} errors), "
        f"{usage.get('input_tokens', 0)} in / {usage.get('output_tokens', 0)} out tokens, {r['wall_s']} s",
    ] + ([f"<b>SDC origin:</b> {e(r['sdc_origin'])}"] if r.get("sdc_origin") else [])))]

    checks = []
    for c in r["checks_run"]:
        rows = [f"<b>status</b> {e(c['status'])} · <b>evidence</b> {e(c['evidence_level'])}",
                f"<b>summary</b> {e(c['summary'])}", f"<b>claim scope</b> {e(c['claim_scope'])}"]
        rows += [f"artifact <code>{e(a['path'])}</code> sha256 <code>{e(a['sha256'])}</code>" for a in c["artifacts"]]
        rows += [f"{e(k)}: <code>{e(v)}</code>" for k, v in c.get("tool_versions", {}).items()]
        if c.get("sdc_sha256"):
            rows.append(f"SDC <code>{e(c.get('sdc'))}</code> sha256 <code>{e(c['sdc_sha256'])}</code>")
        checks.append(f"<h3>{e(c['tool'])} <code>{e(json.dumps(c['args'], ensure_ascii=False))}</code></h3>{ul(rows)}")
    if r.get("other_tool_calls"):
        checks.append("<h3>Other tool calls</h3>" + ul(
            f"{e(o['tool'])} <code>{e(json.dumps(o['args'], ensure_ascii=False))}</code> {e(o['status'])}"
            for o in r["other_tool_calls"]))
    out.append(sec("Checks run: claim scope, artifacts, tool versions",
                   "".join(checks) or '<p class="muted">(none)</p>'))

    timed = [c for c in r["checks_run"] if c.get("timing")]
    if timed:
        rows = "".join(
            f"<tr><td>{e(c['tool'])}</td><td>{e(tm['clock_period'])} ns</td><td>{e(tm['wns'])}</td>"
            f"<td>{e(tm['tns'])}</td><td>{e(tm['hold_wns'])}</td><td>{e(tm['worst_corner'])}</td>"
            f"<td>{'yes' if tm['timing_met'] else 'no'}</td><td>{e(tm.get('cells'))}</td><td>{e(tm.get('area'))}</td>"
            f"<td>{e(tm.get('latches'))}</td><td><code>{e(c['sdc_sha256'][:16])}…</code></td>"
            f"<td>{'VOID (SDC changed later in the run)' if tm.get('void') else 'no'}</td></tr>"
            for c in timed for tm in [c["timing"]])
        out.append(sec("Timing estimates (E4, pre-PnR)",
                       '<div class="tbl"><table><tr><th>tool</th><th>clock</th><th>setup WNS</th><th>TNS</th>'
                       "<th>hold WNS</th><th>worst corner</th><th>met</th><th>cells</th><th>area</th><th>latches</th>"
                       f"<th>SDC sha256</th><th>void</th></tr>{rows}</table></div>"))

    nr = r["not_run"]
    out.append(sec("Not run", ul([
        f"lint run: {'yes' if nr['lint_run'] else 'no'}",
        f"described but not run: {e(', '.join(nr['described_but_not_run']) or 'none')}",
        f"cases in the list not run ({len(nr['cases_not_run'])}): {e(', '.join(nr['cases_not_run']) or 'none')}"])))

    if gate["decisions"]:
        out.append(sec("Gate decisions", "<ol>" + "".join(
            f"<li>{'pass' if d['passed'] else 'reject'} (best passing evidence {e(d['max_level'] or 'none')})"
            + (ul(f'<span class="q">{e(x)}</span>' for x in d["reasons"]) if d["reasons"] else "") + "</li>"
            for d in gate["decisions"]) + "</ol>"))

    out.append(sec("Final claims (the agent's report, verbatim)", f"<pre>{e(r['final_claims'] or '(none)')}</pre>"))
    out.append(sec("Residual risk", f"<pre>{e(r['residual_risk']['report'] or '(none stated in the report)')}</pre>"
                   + "<p><b>From the tools:</b></p>" + ul(e(x) for x in r["residual_risk"]["from_tools"])))

    for v in r.get("reviews", []):
        who = ("rule-based (no model)" if v["reviewer"] == "rules"
               else f"{v['reviewer']}, {v['provider']} / {v['model']}")
        rows = [f"<b>Saw:</b> {e(v['context'])}",
                f"<b>Verdict:</b> {_badge(v['verdict'] or '(none given)', VERDICT_CLASS.get(v['verdict'], 'warn'))}"
                + ("" if v.get("format_ok", True) else " · format not followed (checklist first, findings, verdict)")]
        if v.get("note"):
            rows.append(f"<b>Note:</b> {e(v['note'])}")
        if v.get("tool_calls"):
            rows.append("<b>Re-ran:</b> " + "; ".join(
                f"{e(c['tool'])} <code>{e(json.dumps(c['args'], ensure_ascii=False))}</code> → {e(c['status'])} "
                f"{e(c['evidence_level'])}" for c in v["tool_calls"]))
        body = ul(rows) + "<h3>Checklist (written before the findings)</h3>" + ul(e(x) for x in v["checklist"])
        body += "<h3>Findings</h3>" + ul(f"{_badge(f['severity'], SEV_CLASS.get(f['severity'], 'lvl'))} "
                                         f'<span class="q">{e(f["text"])}</span>' for f in v["findings"])
        out.append(sec(f"Review: {who} — {v['verdict'] or '(none given)'}", body))
    if r.get("review_error"):
        out.append(sec("Review error", f'<p class="q">{e(r["review_error"])}</p>'))

    a = r.get("approval")
    if a:
        rows = [f"<b>Step:</b> {e(a.get('step'))} · <b>Status:</b> {e(a['status'])}",
                f"requested: {e(a.get('requested_at'))} ({e(a.get('why'))})"]
        if a["status"] != "pending":
            rows += [f"decided by <b>{e(a['by'])}</b> at {e(a['at'])}"
                     + (f": {e(a['reason'])}" if a.get("reason") else ""),
                     f"receipt content sha256 at decision: <code>{e(a['receipt_sha256'])}</code>"]
            if a.get("overrode_gate"):
                rows.append("this approval overrode the evidence gate's rejection")
        out.append(sec("Human approval", ul(rows)))
    if r.get("trace"):
        out.append(sec("Trace", f"<p><code>{e(r['trace'])}</code></p>"))
    return out
