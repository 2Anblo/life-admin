"""Summarize an evaluation directory into the metrics of the benchmark paper.

Reads every ``trial-*.json`` under ``<out>`` and writes ``summary.json``,
``summary.md`` and ``results_table.tex`` next to them.

- Success rate: the unweighted mean over tasks of each task's success rate over
  its scored trials, with a 95% bootstrap confidence interval over tasks.
- Consistency: the fraction of tasks solved in every trial.
- Goal completion: the mean fraction of goal predicates that hold (partial credit).
- Constraint violations: the fraction of trials with a failed constraint predicate.
- Rejected calls: tool calls the environment refused, over all tool calls.
- Efficiency: tool calls, API calls, input and output tokens, wall-clock seconds.
- Breakdowns by category and difficulty (from benchmark/task_index.json).
- With ``--baseline``, a paired bootstrap over tasks for each agent's difference
  in success rate from the baseline.

Trials that ended in an infrastructure or harness error are counted, not scored.
"""

import argparse
import json
import random
import statistics
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evaluation.runner import task_index  # noqa: E402

BOOTSTRAP_SAMPLES = 10000


def load_records(out_dir: Path) -> list:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(out_dir).glob("*/*/trial-*.json"))]


def _mean(values):
    values = [v for v in values if v is not None]
    return statistics.fmean(values) if values else None


def bootstrap_ci(per_task: list, samples=BOOTSTRAP_SAMPLES, seed=0):
    if len(per_task) < 2:
        return None
    rng = random.Random(seed)
    n = len(per_task)
    means = sorted(statistics.fmean(rng.choices(per_task, k=n)) for _ in range(samples))
    return [means[int(0.025 * samples)], means[int(0.975 * samples) - 1]]


def _trial_metrics(record):
    preds = record["verdict"]["predicates"].values()
    goals = [p for p in preds if p["type"] == "goal"]
    calls = record["tool_calls"]
    api = record.get("api_calls", [])
    return {
        "success": 1.0 if record["success"] else 0.0,
        "goal_completion": statistics.fmean(p["ok"] for p in goals) if goals else None,
        "constraint_violation": 1.0 if any(p["type"] == "constraint" and not p["ok"] for p in preds) else 0.0,
        "tool_calls": len(calls),
        "rejected_calls": sum(1 for c in calls if c["is_error"]),
        "api_calls": len(api),
        "input_tokens": sum(a.get("input_tokens") or 0 for a in api),
        "output_tokens": sum(a.get("output_tokens") or 0 for a in api),
        "duration_s": record.get("duration_s"),
    }


def summarize(records: list, baseline: str | None = None) -> dict:
    index = {t["id"]: t for t in task_index()}
    by_agent = defaultdict(lambda: defaultdict(list))
    unscored = defaultdict(lambda: defaultdict(int))
    for record in records:
        if record["status"] == "scored":
            by_agent[record["agent"]][record["task_id"]].append(_trial_metrics(record))
        else:
            unscored[record["agent"]][record["status"]] += 1

    agents = {}
    for agent, tasks in by_agent.items():
        per_task = {tid: statistics.fmean(m["success"] for m in trials) for tid, trials in tasks.items()}
        trials = [m for ms in tasks.values() for m in ms]
        rate = statistics.fmean(per_task.values())
        groups = {}
        for key in ("category", "difficulty"):
            buckets = defaultdict(list)
            for tid, value in per_task.items():
                buckets[index[tid][key]].append(value)
            groups[key] = {name: {"success_rate": statistics.fmean(v), "tasks": len(v)} for name, v in sorted(buckets.items())}
        total_calls = sum(m["tool_calls"] for m in trials)
        agents[agent] = {
            "tasks": len(per_task),
            "trials": len(trials),
            "unscored": dict(unscored.get(agent, {})),
            "success_rate": rate,
            "success_rate_ci95": bootstrap_ci(list(per_task.values())),
            "consistency": statistics.fmean(1.0 if all(m["success"] for m in tasks[t]) else 0.0 for t in tasks),
            "goal_completion": _mean(m["goal_completion"] for m in trials),
            "constraint_violation_rate": statistics.fmean(m["constraint_violation"] for m in trials),
            "rejected_call_rate": (sum(m["rejected_calls"] for m in trials) / total_calls) if total_calls else 0.0,
            "mean_tool_calls": statistics.fmean(m["tool_calls"] for m in trials),
            "mean_api_calls": statistics.fmean(m["api_calls"] for m in trials),
            "mean_input_tokens": statistics.fmean(m["input_tokens"] for m in trials),
            "mean_output_tokens": statistics.fmean(m["output_tokens"] for m in trials),
            "mean_duration_s": _mean(m["duration_s"] for m in trials),
            "by_category": groups["category"],
            "by_difficulty": groups["difficulty"],
            "per_task": per_task,
        }
    for agent, data in agents.items():
        for status, count in unscored.get(agent, {}).items():
            data["unscored"][status] = count
    for agent, counts in unscored.items():
        if agent not in agents:
            agents[agent] = {"tasks": 0, "trials": 0, "unscored": dict(counts), "success_rate": None, "per_task": {}}

    if baseline and baseline in agents:
        base = agents[baseline]["per_task"]
        rng = random.Random(1)
        for agent, data in agents.items():
            common = sorted(set(base) & set(data["per_task"]))
            if agent == baseline or len(common) < 2:
                continue
            diffs = [data["per_task"][t] - base[t] for t in common]
            boots = sorted(statistics.fmean(rng.choices(diffs, k=len(diffs))) for _ in range(BOOTSTRAP_SAMPLES))
            data["vs_baseline"] = {"baseline": baseline, "tasks": len(common), "difference": statistics.fmean(diffs),
                                   "ci95": [boots[int(0.025 * BOOTSTRAP_SAMPLES)], boots[int(0.975 * BOOTSTRAP_SAMPLES) - 1]]}
    return {"agents": agents, "task_order": [t["id"] for t in task_index()]}


def _pct(value):
    return "-" if value is None else f"{100 * value:.1f}"


def to_markdown(summary: dict) -> str:
    agents = summary["agents"]
    lines = ["# Evaluation summary", "", "## Overall", "",
             "| Agent | Tasks | Trials | Success % (95% CI) | Consistency % | Goal completion % | Constraint viol. % | Rejected calls % | Tool calls | Tokens in/out | Unscored |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for name, a in sorted(agents.items(), key=lambda kv: -(kv[1]["success_rate"] or -1)):
        if a["success_rate"] is None:
            lines.append(f"| {name} | 0 | 0 | - | - | - | - | - | - | - | {a['unscored']} |")
            continue
        ci = a["success_rate_ci95"]
        ci_text = f" ({_pct(ci[0])}-{_pct(ci[1])})" if ci else ""
        lines.append(
            f"| {name} | {a['tasks']} | {a['trials']} | {_pct(a['success_rate'])}{ci_text} | {_pct(a['consistency'])} | "
            f"{_pct(a['goal_completion'])} | {_pct(a['constraint_violation_rate'])} | {_pct(a['rejected_call_rate'])} | "
            f"{a['mean_tool_calls']:.1f} | {a['mean_input_tokens']:.0f}/{a['mean_output_tokens']:.0f} | {a['unscored'] or ''} |")
    paired = [(n, a["vs_baseline"]) for n, a in agents.items() if a.get("vs_baseline")]
    if paired:
        lines += ["", "## Paired difference from baseline", "", "| Agent | Baseline | Tasks | Δ success (pp) | 95% CI |", "|---|---|---|---|---|"]
        for name, v in paired:
            lines.append(f"| {name} | {v['baseline']} | {v['tasks']} | {100 * v['difference']:+.1f} | "
                         f"{100 * v['ci95'][0]:+.1f} to {100 * v['ci95'][1]:+.1f} |")
    for key, title in (("by_category", "By category"), ("by_difficulty", "By difficulty")):
        names = sorted({g for a in agents.values() for g in a.get(key, {})})
        if not names:
            continue
        lines += ["", f"## {title} (success %)", "", "| Agent | " + " | ".join(names) + " |", "|---" * (len(names) + 1) + "|"]
        for agent, a in sorted(agents.items()):
            row = [_pct(a.get(key, {}).get(g, {}).get("success_rate")) for g in names]
            lines.append(f"| {agent} | " + " | ".join(row) + " |")
    order = summary["task_order"]
    lines += ["", "## Per task (success %)", "", "| Task | " + " | ".join(sorted(agents)) + " |", "|---" * (len(agents) + 1) + "|"]
    for tid in order:
        lines.append(f"| {tid} | " + " | ".join(_pct(agents[a]["per_task"].get(tid)) for a in sorted(agents)) + " |")
    return "\n".join(lines) + "\n"


def to_latex(summary: dict) -> str:
    rows = []
    for name, a in sorted(summary["agents"].items(), key=lambda kv: -(kv[1]["success_rate"] or -1)):
        if a["success_rate"] is None:
            continue
        ci = a["success_rate_ci95"]
        ci_text = f" \\scriptsize[{_pct(ci[0])}, {_pct(ci[1])}]" if ci else ""
        rows.append(f"{name.replace('_', chr(92) + '_')} & {_pct(a['success_rate'])}{ci_text} & {_pct(a['consistency'])} & "
                    f"{_pct(a['goal_completion'])} & {_pct(a['constraint_violation_rate'])} & {a['mean_tool_calls']:.1f} & "
                    f"{(a['mean_input_tokens'] + a['mean_output_tokens']) / 1000:.1f}k \\\\")
    return ("\\begin{tabular}{@{}lrrrrrr@{}}\n\\toprule\n"
            "Agent & Success (\\%) & Consistent (\\%) & Goals (\\%) & Viol. (\\%) & Calls & Tokens \\\\\n\\midrule\n"
            + "\n".join(rows) + "\n\\bottomrule\n\\end{tabular}\n")


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("out", help="evaluation directory written by run_eval.py")
    parser.add_argument("--baseline", help="agent to compare the others against (paired bootstrap)")
    options = parser.parse_args()
    out = Path(options.out)
    summary = summarize(load_records(out), options.baseline)
    (out / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    (out / "summary.md").write_text(to_markdown(summary), encoding="utf-8")
    (out / "results_table.tex").write_text(to_latex(summary), encoding="utf-8")
    print(to_markdown(summary))
    print(f"Wrote {out / 'summary.json'}, summary.md and results_table.tex")


if __name__ == "__main__":
    main()
