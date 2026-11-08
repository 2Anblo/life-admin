"""Run agents on the benchmark and save one record per trial.

    # scripted bounds: the reference plan and all floor policies, every task (no API key needed)
    python evaluation/run_eval.py --agents scripted --out results/eval/scripted

    # LLM baselines, 3 trials per task (needs TINKER_API_KEY, from the environment or .env)
    python evaluation/run_eval.py --agents plain_loop oneshot --trials 3 \\
        --model thinkingmachines/Inkling-Small --out results/eval/inkling-small

    # a quick pilot on two tasks
    python evaluation/run_eval.py --agents plain_loop --tasks avoid_late_fees split_tender_refund \\
        --model thinkingmachines/Inkling-Small --out results/eval/pilot

Rerunning the same command resumes: finished trials are kept. Settings are
frozen in <out>/config.json, and a rerun with different settings is refused.
Summarize with: python evaluation/summarize.py <out> [--baseline plain_loop]
"""

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT))

from evaluation.runner import DEFAULT_SETTINGS, all_task_ids, expand_agents, is_llm, run_eval  # noqa: E402

TINKER_BASE_URL = "https://tinker.thinkingmachines.dev/services/tinker-prod/anthropic/api"


def load_dotenv(path: Path = ROOT / ".env") -> None:
    """Read KEY=value lines from the repository's .env. Variables already set in the environment win."""
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        os.environ.setdefault(key, value)


def main():
    load_dotenv()
    parser = argparse.ArgumentParser(description="Run agents on LifeAdmin tasks.")
    parser.add_argument("--agents", nargs="+", required=True,
                        help="plain_loop, oneshot, reference, P0_noop ... P7_act_on_everything, floors, scripted")
    parser.add_argument("--tasks", nargs="+", default=["all"], help="task ids, or 'all' (default)")
    parser.add_argument("--trials", type=int, default=3, help="trials per task for LLM agents (default 3)")
    parser.add_argument("--out", required=True, help="output directory, e.g. results/eval/<name>")
    parser.add_argument("--model", default=os.getenv("TINKER_MODEL"), help="model id (default: $TINKER_MODEL)")
    parser.add_argument("--temperature", type=float, default=DEFAULT_SETTINGS["temperature"])
    parser.add_argument("--max-steps", type=int, default=DEFAULT_SETTINGS["max_steps"])
    parser.add_argument("--max-tokens", type=int, default=DEFAULT_SETTINGS["max_tokens"])
    parser.add_argument("--max-tool-calls", type=int, default=DEFAULT_SETTINGS["max_tool_calls"])
    parser.add_argument("--workers", type=int, default=1, help="trials to run in parallel (default 1)")
    options = parser.parse_args()

    agents = expand_agents(options.agents)
    known = all_task_ids()
    task_ids = known if options.tasks == ["all"] else options.tasks
    unknown = [t for t in task_ids if t not in known]
    if unknown:
        parser.error(f"unknown task ids: {', '.join(unknown)}")

    client = None
    if any(is_llm(a) for a in agents):
        if not options.model:
            parser.error("LLM agents need --model or $TINKER_MODEL")
        if not os.getenv("TINKER_API_KEY"):
            parser.error("LLM agents need $TINKER_API_KEY")
        from anthropic import Anthropic
        client = Anthropic(api_key=os.environ["TINKER_API_KEY"], base_url=TINKER_BASE_URL, max_retries=0)

    settings = {"model": options.model, "temperature": options.temperature, "max_steps": options.max_steps,
                "max_tokens": options.max_tokens, "max_tool_calls": options.max_tool_calls}
    records = run_eval(ROOT / options.out if not Path(options.out).is_absolute() else options.out,
                       agents, task_ids, options.trials, settings, client=client, workers=options.workers)
    scored = [r for r in records if r["status"] == "scored"]
    problems = [r for r in records if r["status"] != "scored"]
    print(f"\n{len(scored)} trials scored, {sum(r['success'] for r in scored)} passed; {len(problems)} not scored.")
    for r in problems:
        print(f"  {r['agent']} {r['task_id']} trial {r['trial']}: {r['status']}")
    print(f"Summarize with: python evaluation/summarize.py {options.out}")


if __name__ == "__main__":
    main()
