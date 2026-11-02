"""Export all paired case trials and their existing judge scores without filtering."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime
import hashlib
import json
import math
import os
from pathlib import Path
import statistics

from .case_analysis import sha256, trial_profile, write_json
from .core.protocol import PROFILE_NAMES, SCORE_KEYS
from .metrics.numbers import same_metric
from .metrics.usage import SESSION_USAGE_METRICS, validate_session_usage
from .swe_qa.collect import _trajectory_metrics

METRIC_LABELS = {
    "judge_total": "Judge 总分 /100",
    **{f"judge_{key}": label + " /20" for key, label in zip(SCORE_KEYS,
       ("正确性", "完整性", "相关性", "清晰度", "连贯性"), strict=True)},
    "input_tokens": "Input（含缓存）",
    "uncached_input_tokens": "Input（未缓存）",
    "cache_read_tokens": "缓存读取 token",
    "cache_write_tokens": "缓存写入 token",
    "output_tokens": "Output（含推理）",
    "reasoning_tokens": "推理 token",
    "text_output_tokens": "正文输出 token",
    "llm_calls": "任务模型调用",
    "tool_calls": "全部工具调用",
    "zg_tool_calls": "ZG 工具调用",
    "zg_search_calls": "ZG 搜索调用",
    "agent_wall_seconds_raw": "Agent 原始耗时（秒）",
    "agent_wall_seconds_standard": "Agent 标准耗时（秒）",
    "trial_wall_seconds": "整次耗时（秒）",
}


def read_json(path: Path):
    return json.loads(path.read_text())


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def elapsed(timing: dict) -> float:
    start, end = [datetime.fromisoformat(timing[key].replace("Z", "+00:00"))
                  for key in ("started_at", "finished_at")]
    value = (end - start).total_seconds()
    require(value > 0 and math.isfinite(value), "invalid execution timing")
    return value


def relative(path: Path, directory: Path) -> str:
    return Path(os.path.relpath(path, directory)).as_posix()


def index_trials(trials: list[dict], *, label: str) -> dict[str, dict]:
    result = {}
    for trial in trials:
        name = trial.get("trial_name")
        require(isinstance(name, str) and bool(name) and Path(name).name == name,
                f"{label}: missing or invalid trial_name")
        require(name not in result, f"{label}: duplicate trial_name {name}")
        result[name] = trial
    return result


def tool_sequence(path: Path, usage: dict) -> list[dict]:
    """Use the measured session tree; retain exact arguments and raw-body links."""
    sessions = {item["session_id"] for item in usage["sessions"]}
    calls = []
    part_ids = set()
    for record in read_json(path):
        data = record["data"]
        data = json.loads(data) if isinstance(data, str) else data
        if record["kind"] != "part" or data.get("session_id") not in sessions:
            continue
        content = data.get("content", {})
        if content.get("type") != "tool":
            continue
        require(data["id"] not in part_ids, "duplicate tool part in full-sessions")
        part_ids.add(data["id"])
        state = content.get("state", {})
        body = state.get("output", state.get("error", ""))
        body = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
        timing = state.get("time", {})
        start, end = timing.get("start"), timing.get("end")
        calls.append({
            "session_id": data["session_id"], "message_id": data["message_id"],
            "part_id": data["id"], "call_id": content.get("callID"),
            "tool": content.get("tool"), "status": state.get("status"),
            "input": state.get("input"), "started_ms": start, "finished_ms": end,
            "wall_seconds": (end - start) / 1000 if isinstance(start, (int, float)) and isinstance(end, (int, float)) else None,
            "output_characters": len(body), "output_sha256": hashlib.sha256(body.encode()).hexdigest(),
            "output_preview": body[:400], "output_preview_truncated": len(body) > 400,
        })
    calls.sort(key=lambda item: (item["started_ms"] or 0, item["part_id"]))
    require(len(calls) == usage["total"]["tool_calls"], "tool sequence differs from measured session tool count")
    for number, item in enumerate(calls, 1):
        item["sequence"] = number
    return calls


def stats(values: list[int | float]) -> dict:
    return {"count": len(values), "sum": sum(values), "mean": statistics.mean(values),
            "median": statistics.median(values), "min": min(values), "max": max(values),
            "std_population": statistics.pstdev(values),
            "std_sample": statistics.stdev(values) if len(values) > 1 else None}


def summarize(rows: list[dict]) -> dict:
    groups = {profile: {key: stats([row[key] for row in rows if row["profile"] == profile])
                       for key in METRIC_LABELS} for profile in PROFILE_NAMES}
    differences = {}
    for key in METRIC_LABELS:
        baseline, zg = (groups[profile][key]["mean"] for profile in PROFILE_NAMES)
        delta = zg - baseline
        differences[key] = {"baseline_mean": baseline, "zvec_grep_mean": zg,
                            "zvec_minus_baseline": delta,
                            "change_pct": delta / baseline * 100 if baseline != 0 else None}
    return {"profiles": groups, "differences": differences,
            "difference_definition": "zvec-grep mean - baseline mean",
            "percentage_definition": "(zvec-grep mean - baseline mean) / baseline mean * 100; null when baseline is zero",
            "filter_applied": False, "retained_trials": len(rows)}


def collect_rows(evidence: Path, judge: Path, output: Path) -> tuple[list[dict], dict, list[dict]]:
    pair, report = read_json(judge / "pair.json"), read_json(judge / "report.json")
    lock = read_json(evidence / "run-lock.json")
    require(pair.get("valid") is True, "pair is not valid")
    require(set(pair["profiles"]) == set(PROFILE_NAMES), "pair must contain both profiles")
    require(pair["task_id"].replace(":", "-") == pair["task_slug"], "pair task ID and slug differ")
    cases = [case for case in report["cases"] if case["task_id"].replace(":", "-") == pair["task_slug"]]
    require(len(cases) == 1, "judge report does not identify exactly one matching task")
    case = cases[0]
    expected = pair["expected_trials"]
    require(expected == pair["actual_trials"] == lock["attempts"], "run and pair trial counts differ")
    lock_case = lock.get("case", {})
    require(lock_case.get("task_slug") == pair["task_slug"], "run and pair task identities differ")
    actual_dirs = {path.parent.resolve() for path in (evidence / "jobs").glob("*/*/result.json")}
    used_dirs, all_names, all_sessions = set(), set(), set()
    rows, documents = [], []
    for profile in PROFILE_NAMES:
        pair_trials = index_trials(pair["profiles"][profile]["trials"], label=f"pair {profile}")
        judged = index_trials(case["profiles"][profile]["trials"], label=f"judge {profile}")
        require(len(pair_trials) == expected and set(pair_trials) == set(judged),
                f"{profile}: pair and judge trial identities/counts differ")
        for name, trial in sorted(pair_trials.items(), key=lambda item: item[1]["trial_index"]):
            require(name not in all_names, f"trial_name reused across profiles: {name}")
            all_names.add(name)
            require(trial["profile"] == profile, f"{name}: pair profile mismatch")
            require(Path(trial["job_name"]).name == trial["job_name"], f"{name}: invalid job name")
            directory = (evidence / "jobs" / trial["job_name"] / name).resolve()
            require(directory in actual_dirs, f"{name}: raw trial missing from this evidence run")
            require(trial_profile(directory) == profile, f"{name}: raw profile mismatch")
            used_dirs.add(directory)
            raw, grade = read_json(directory / "result.json"), judged[name]
            require(raw.get("trial_name", name) == name and raw.get("exception_info") is None,
                    f"{name}: raw identity mismatch or execution exception")
            usage = validate_session_usage(read_json(directory / "agent/session-usage.json"), require_identity=True)
            sid = usage["root_session_id"]
            require(sid not in all_sessions, f"{name}: session reused across trials")
            all_sessions.add(sid)
            for label, metrics in (("pair", trial), ("judge", grade["metrics"])):
                require(metrics["session_usage"]["root_session_id"] == sid, f"{name}: {label} session identity mismatch")
                for key in SESSION_USAGE_METRICS:
                    require(same_metric(metrics.get(key), usage["total"][key]), f"{name}: {label} metric mismatch: {key}")
            answer = _trajectory_metrics(read_json(directory / "agent/trajectory.json"))["answer"]
            require(answer == trial["answer"], f"{name}: pair answer differs from raw trajectory")
            raw_wall = elapsed(raw["agent_execution"])
            collection_wall = usage["collection_wall_seconds"]
            standard_wall = raw_wall - collection_wall
            require(standard_wall > 0, f"{name}: invalid standard agent time")
            for label, metrics in (("pair", trial), ("judge", grade["metrics"])):
                require(same_metric(metrics.get("agent_wall_seconds"), standard_wall), f"{name}: {label} agent time mismatch")
            scores = grade["judge"]["scores"]
            require(set(scores) == set(SCORE_KEYS) and all(type(value) is int and 1 <= value <= 20 for value in scores.values()),
                    f"{name}: invalid five-dimension judge scores")
            require(grade["judge"]["total"] == sum(scores.values()), f"{name}: judge total mismatch")
            calls = tool_sequence(directory / "agent/full-sessions.json", usage)
            zg_calls = sum("zvec_grep" in (call["tool"] or "") for call in calls)
            require(profile != "baseline" or zg_calls == 0, f"{name}: baseline contains ZG tool calls")
            document = output / "trials" / profile / name
            links = {"answer": relative(document / "answer.md", output),
                     "tool_sequence": relative(document / "tools.md", output),
                     "tool_sequence_json": relative(document / "tools.json", output),
                     "full_sessions": relative(directory / "agent/full-sessions.json", output),
                     "raw_trial_result": relative(directory / "result.json", output),
                     "session_usage": relative(directory / "agent/session-usage.json", output),
                     "wire": relative(directory / "agent/wire", output)}
            row = {"profile": profile, "trial_index": trial["trial_index"], "trial_name": name,
                   "root_session_id": sid, "judge_total": grade["judge"]["total"],
                   **{f"judge_{key}": scores[key] for key in SCORE_KEYS}, **usage["total"],
                   "zg_tool_calls": zg_calls,
                   "zg_search_calls": sum((call["tool"] or "").endswith("zvec_grep_search") for call in calls),
                   "agent_wall_seconds_raw": raw_wall, "agent_wall_seconds_standard": standard_wall,
                   "usage_collection_wall_seconds": collection_wall,
                   "trial_wall_seconds": elapsed(raw),
                   "wire_model_calls_including_auxiliary": len(list((directory / "agent/wire/model").glob("*.metadata.json"))),
                   "answer_sha256": hashlib.sha256(answer.encode()).hexdigest(),
                   "judge_model": grade["judge"]["model"], "links": links}
            rows.append(row)
            documents.append({"directory": document, "raw_directory": directory,
                              "row": row, "answer": answer, "calls": calls})
    require(used_dirs == actual_dirs, "raw evidence contains trials absent from pair/judge; refusing to hide them")
    metadata = {"schema_version": 1, "task_id": case["task_id"], "pair_task_id": pair["task_id"], "task_slug": pair["task_slug"],
                "trials_per_profile": expected, "run_lock": lock, "judge": report["judge"],
                "original_aggregate_filter": report.get("aggregate", {}).get("filter"),
                "source_files": [{"path": relative(path, output), "sha256": sha256(path)}
                                 for path in (evidence / "run-lock.json", judge / "pair.json", judge / "report.json")],
                "scope": "all paired trials; task session tree including descendants; excludes auxiliary title generation"}
    return rows, metadata, documents


def fmt(value, digits=2):
    return "N/A" if value is None else f"{value:,.{digits}f}"


def write_documents(documents: list[dict]) -> None:
    for item in documents:
        directory, raw, row = item["directory"], item["raw_directory"], item["row"]
        directory.mkdir(parents=True, exist_ok=True)
        heading = f"# {row['profile']} · 第 {row['trial_index']} 次 · {row['trial_name']}\n\n"
        (directory / "answer.md").write_text(heading +
            f"Judge：{row['judge_total']}/100。 [工具序列](tools.md) · "
            f"[完整会话](<{relative(raw / 'agent/full-sessions.json', directory)}>)\n\n" + item["answer"] + "\n")
        lines = [heading, "按工具开始时间排序；并发工具的行次序不表示因果依赖。",
                 f"[完整会话及工具返回正文](<{relative(raw / 'agent/full-sessions.json', directory)}>) · [结构化序列](tools.json)", ""]
        for call in item["calls"]:
            lines.extend([f"## {call['sequence']}. {call['tool']} · {call['status']}", "",
                          f"Session `{call['session_id']}` · Part `{call['part_id']}` · {fmt(call['wall_seconds'])} 秒", "",
                          "输入：", "", "````json", json.dumps(call["input"], ensure_ascii=False, indent=2), "````", "",
                          f"返回 {call['output_characters']:,} 字符；以下最多 400 字符，完整正文见会话中的 Part ID。", "",
                          "````text", call["output_preview"], "````", ""])
        (directory / "tools.md").write_text("\n".join(lines) + "\n")
        write_json(directory / "tools.json", item["calls"])


def render_readme(rows: list[dict], metadata: dict, summary: dict) -> str:
    count = metadata["trials_per_profile"]
    lines = [f"# {metadata['task_slug']} · baseline {count} 次 + ZG {count} 次", "",
             f"保留全部 **{len(rows)} 次**运行与评分；不采用标准 Aggregate 的排除规则。", "",
             "**限制：Judge 使用与答题相同的模型，可能存在共同盲点；分数不能替代源码核验，也不能将组间差异直接归因于检索。**", "",
             "[逐次数据 CSV](trials.csv) · [逐次数据 JSON](trials.json) · [统计 JSON](summary.json) · [运行与来源](metadata.json)", "",
             "## 两组比较", "",
             "差值统一为 **ZG − baseline**；百分比为差值 / baseline 均值。基数为 0 时百分比为 N/A。分数增加表示更高评分，资源指标减少表示用量或耗时下降。", "",
             "| 指标 | Baseline 均值 | ZG 均值 | 差值 | 变化 |", "|---|---:|---:|---:|---:|"]
    for key, label in METRIC_LABELS.items():
        delta = summary["differences"][key]
        lines.append(f"| {label} | {fmt(delta['baseline_mean'])} | {fmt(delta['zvec_grep_mean'])} | {fmt(delta['zvec_minus_baseline'])} | {fmt(delta['change_pct'])}{'%' if delta['change_pct'] is not None else ''} |")
    lines.extend(["", "## 每次运行", "", "输入为含缓存 token；时间为标准 Agent 秒数。完整五维分数及三种时间均保留在 CSV/JSON。", "",
                  "| 组别 | 次数 | Trial / 答案 | Score | Input | Time | Tool / ZG |", "|---|---:|---|---:|---:|---:|---:|"])
    for row in rows:
        lines.append(f"| {row['profile']} | {row['trial_index']} | [{row['trial_name']}]({row['links']['answer']}) | {row['judge_total']} | {row['input_tokens']:,} | {fmt(row['agent_wall_seconds_standard'])} | [{row['tool_calls']} / {row['zg_tool_calls']}]({row['links']['tool_sequence']}) |")
    lines.extend(["", "## 指标口径", "",
        "- 分数：正确性、完整性、相关性、清晰度、连贯性各 1–20 分，总分为五项相加。验证器 reward 不作为答案质量分数。",
        "- Token、任务模型调用及工具数：主任务会话及其子会话之和；Input 含缓存读写，Output 含推理。不包含辅助标题生成，也不包含 Judge 消耗。原始模型调用（含辅助调用）保留在各次 wire 目录，CSV 另列其数量。",
        "- Agent 原始耗时：Harbor agent_execution 起止时间差。标准耗时：原始耗时减 session usage 采集时间，与标准 pair/report 口径一致；两组均采集证据，proxy、文件哈希和完整会话导出的开销仍包含在内，因此不是纯模型执行耗时。整次耗时：trial 起止时间差，包含环境准备等。",
        "- 统计使用全部运行，记录均值、中位数、最小值、最大值、总体标准差和样本标准差。两组按组均值比较，不将同序号视为因果配对；未进行显著性检验。",
        "- tools.md 只展示输入和返回正文预览；完整模型请求、工具返回和会话正文保留于原始证据，链接可直接访问。", "",
        "<details><summary>全部运行的分布统计</summary>", "",
        "| 组别 | 指标 | 均值 | 中位数 | 最小–最大 | 标准差（总体） |", "|---|---|---:|---:|---:|---:|"])
    for profile in PROFILE_NAMES:
        for key, label in METRIC_LABELS.items():
            value = summary["profiles"][profile][key]
            lines.append(f"| {profile} | {label} | {fmt(value['mean'])} | {fmt(value['median'])} | {fmt(value['min'])}–{fmt(value['max'])} | {fmt(value['std_population'])} |")
    lines.extend(["", "</details>", "", "## 人工结论", "", "待结合源码、检索返回及完整会话复核。自动导出只展示既有评分与事实指标。", ""])
    return "\n".join(lines)


def export_report(evidence: Path, judge: Path, output: Path) -> dict:
    evidence, judge, output = evidence.resolve(), judge.resolve(), output.resolve()
    require(not output.is_relative_to(evidence) and not output.is_relative_to(judge), "output must be separate from original evidence/judge artifacts")
    generated = ("metadata.json", "trials.json", "summary.json", "trials.csv", "trials", "RESULTS.generated.zh-CN.md")
    require(not any((output / name).exists() for name in generated), "generated outputs already exist; preserve previous reports")
    rows, metadata, documents = collect_rows(evidence, judge, output)
    summary = summarize(rows)
    output.mkdir(parents=True, exist_ok=True)
    write_json(output / "metadata.json", metadata)
    write_json(output / "trials.json", rows)
    write_json(output / "summary.json", summary)
    flattened = [{**{key: value for key, value in row.items() if key != "links"},
                  **{key + "_path": value for key, value in row["links"].items()}} for row in rows]
    with (output / "trials.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(flattened[0]))
        writer.writeheader()
        writer.writerows(flattened)
    write_documents(documents)
    (output / "RESULTS.generated.zh-CN.md").write_text(render_readme(rows, metadata, summary))
    return {"trials": len(rows), "task": metadata["task_id"], "output": str(output), "filter_applied": False}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--evidence", type=Path, required=True)
    parser.add_argument("--judge", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(export_report(args.evidence, args.judge, args.output), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
