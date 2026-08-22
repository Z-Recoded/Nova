# nova_mini_swe_agent_harness.py
# Real head-to-head comparison harness (86bbfwbwc): runs the same vendored Exercism corpus
# (data/coding_specialist_eval/exercism_subset/) that nova_aci_harness.py tests Nova's own
# constrained ACI against, through mini-swe-agent's unconstrained raw-bash DefaultAgent instead
# -- the same local Qwen2.5-Coder-7B, scored by the exact same objective test check, to separate
# "the model is bad at this" from "our interface leaves performance on the table"
# (docs/aci-third-party-harness-comparison.md).
#
# Deliberately reuses nova_aci_harness.py's own _prepare_working_copy()/_read_task_description()/
# _run_real_tests() via import rather than reimplementing them -- both harnesses must be scored
# by literally the same check, not two independently-written ones that could silently diverge.
#
# Model wiring uses LitellmTextbasedModel, not the package's default tool-calling LitellmModel:
# Nova's own nova_aci_harness.py already found live (2026-08-15) that Ollama's `tool_calls`
# response field comes back None for qwen2.5-coder:7b even when given a real tools=[...] schema
# -- the text-based model (regex-parsed ```mswea_bash_command fences, no native tool-calling)
# sidesteps that exact wall, and is mini-swe-agent's own documented path for models without
# reliable tool-calling support.
#
# system_template/instance_template below are copied verbatim from the package's own
# config/mini_textbased.yaml -- this experiment compares interface DESIGN, not prompt tuning, so
# mini-swe-agent's prompts are used exactly as it ships, the same way nova_aci_harness.py's own
# SYSTEM_PROMPT is used as originally written. Unlike Nova's ACI harness, mini-swe-agent is NOT
# given an explicit "files in your working directory" hint -- discovering files via `ls`/`find`
# is its native mechanism, and adding that crutch would standardize away the exact interface
# difference this experiment exists to measure.
#
# Usage:
#   python nova_mini_swe_agent_harness.py <exercise-slug>   # e.g. bob

import argparse
import json
import os
import signal
import subprocess
import sys
from datetime import datetime
from pathlib import Path

import minisweagent.environments.local as _local_env
from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.local import LocalEnvironment
from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

import nova_aci_harness as aci_harness

OLLAMA_MODEL = "ollama/qwen2.5-coder:7b"
OLLAMA_API_BASE = "http://127.0.0.1:11434"

# Matches nova_aci_harness.MAX_TURNS -- neither harness gets a budget advantage in the comparison.
MAX_TURNS = 15

CORPUS_ROOT = aci_harness.CORPUS_ROOT

RESULTS_LOG_PATH = Path(__file__).parent / "logs" / "mini_swe_agent_harness_log.jsonl"

# Real trajectory JSON per run -- lets a run be inspected after the fact even though the working
# copy itself is a disposable TemporaryDirectory. logs/ is gitignored like every other real
# telemetry this project writes.
TRAJECTORY_DIR = Path(__file__).parent / "logs" / "mini_swe_agent_trajectories"

# Real gotcha found live (2026-08-17, first pilot run): litellm's default per-call timeout
# against local Ollama is apparently much longer than needed -- two separate 600-SECOND stalls
# happened in a single 15-turn pilot run. A shorter, explicit timeout makes a real stall fail
# fast instead of silently burning ten minutes per occurrence -- but the first value tried
# (120s) was itself found live to be too aggressive (2026-08-18, full-corpus run): confirmed via
# `ollama ps`/nvidia-smi mid-run that the GPU was genuinely at 95%+ utilization the whole time a
# request was "timing out," not idle/hung -- a full 4096-token-context generation on this
# laptop's GPU can legitimately take longer than 120s, and every attempt was being killed and
# retried before it could finish. nova_aci_harness.py's own ollama.Client() calls (Section 2)
# have NO client-side timeout at all, so this harness was never actually the fair baseline the
# original comment claimed -- 300s is still a real ceiling (a genuinely dead connection won't
# hang forever), just one unlikely to be hit by real generation time on this hardware.
REQUEST_TIMEOUT_SECONDS = 300

# Prevents a pager/progress-bar hang from stalling a non-interactive batch run -- copied from
# config/mini_textbased.yaml's own environment.env block, the package's own documented default
# for exactly this failure mode.
ENVIRONMENT_ENV_VARS = {
    "PAGER": "cat",
    "MANPAGER": "cat",
    "LESS": "-R",
    "PIP_PROGRESS_BAR": "off",
    "TQDM_DISABLE": "1",
}

# Copied verbatim from minisweagent/config/mini_textbased.yaml's agent.system_template.
SYSTEM_TEMPLATE = """You are a helpful assistant that can interact with a computer.

Your response must contain exactly ONE bash code block with ONE command (or commands connected with && or ||).
Include a THOUGHT section before your command where you explain your reasoning process.
Format your response as shown in <format_example>.

<format_example>
Your reasoning and analysis here. Explain why you want to perform the action.

```mswea_bash_command
your_command_here
```
</format_example>

Failure to follow these rules will cause your response to be rejected."""

# Copied verbatim from minisweagent/config/mini_textbased.yaml's agent.instance_template.
INSTANCE_TEMPLATE = """Please solve this issue: {{task}}

You can execute bash commands and edit files to implement the necessary changes.

## Recommended Workflow

This workflow should be done step-by-step so that you can iterate on your changes and any possible problems.

1. Analyze the codebase by finding and reading relevant files
2. Create a script to reproduce the issue
3. Edit the source code to resolve the issue
4. Verify your fix works by running your script again
5. Test edge cases to ensure your fix is robust
6. Submit your changes and finish your work by issuing the following command: `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.
   Do not combine it with any other command. <important>After this command, you cannot continue working on this task.</important>

## Important Rules

1. Every response must contain exactly one action
2. The action must be enclosed in triple backticks
3. Directory or environment variable changes are not persistent. Every action is executed in a new subshell.
   However, you can prefix any action with `MY_ENV_VAR=MY_VALUE cd /path/to/working/dir && ...` or write/load environment variables from files

<system_information>
{{system}} {{release}} {{version}} {{machine}}
</system_information>

## Formatting your response

Here is an example of a correct response:

<example_response>
THOUGHT: I need to understand the structure of the repository first. Let me check what files are in the current directory to get a better understanding of the codebase.

```mswea_bash_command
ls -la
```
</example_response>

## Useful command examples

### Create a new file:

```mswea_bash_command
cat <<'EOF' > newfile.py
import numpy as np
hello = "world"
print(hello)
EOF
```

### Edit files with sed:

```mswea_bash_command
# Replace all occurrences
sed -i 's/old_string/new_string/g' filename.py

# Replace only first occurrence
sed -i 's/old_string/new_string/' filename.py

# Replace first occurrence on line 1
sed -i '1s/old_string/new_string/' filename.py

# Replace all occurrences in lines 1-10
sed -i '1,10s/old_string/new_string/g' filename.py
```

### View file content:

```mswea_bash_command
# View specific lines with numbers
nl -ba filename.py | sed -n '10,20p'
```

### Any other command you want to run

```mswea_bash_command
anything
```"""


# Real bug found live (2026-08-17, first pilot run on `bob`): LocalEnvironment.execute()
# calls minisweagent.environments.local's module-level _run() directly (not self._run), and
# _run() does subprocess.Popen(command, shell=True, ...) -- on Windows that always invokes
# cmd.exe, never bash, no matter how LocalEnvironment itself is subclassed. mini-swe-agent's
# own prompts (SYSTEM_TEMPLATE/INSTANCE_TEMPLATE above) instruct the model to write bash
# syntax unconditionally, so the model correctly wrote real bash (`#!/bin/bash`, `[[ ]]`) and
# every command failed with "'#' is not recognized...", with no guard against the resulting
# repeat-failure loop. This is a Windows/bash environment mismatch, not a real characteristic
# of mini-swe-agent -- its real deployments run on Linux/Docker where shell=True naturally
# invokes a POSIX shell -- so patching the module-level _run() to go through bash explicitly
# is a fairness fix, not a change to what this experiment is comparing. Everything else
# (process-group timeout kill, UTF-8 decoding) matches the original _run() exactly.
def _run_via_bash(command: str, cwd: str, env: dict[str, str], timeout: int) -> subprocess.CompletedProcess:
    process = subprocess.Popen(
        ["bash", "-c", command],
        shell=False,
        text=True,
        cwd=cwd,
        env=env,
        encoding="utf-8",
        errors="replace",
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        start_new_session=os.name == "posix",
    )
    try:
        stdout, _ = process.communicate(timeout=timeout)
    except subprocess.TimeoutExpired as e:
        os.killpg(process.pid, signal.SIGKILL) if os.name == "posix" else process.kill()
        stdout, _ = process.communicate()
        raise subprocess.TimeoutExpired(command, timeout, output=stdout) from e
    return subprocess.CompletedProcess(command, process.returncode, stdout=stdout)


_local_env._run = _run_via_bash


def run_exercise(slug: str, verbose: bool = False) -> dict:
    """
    Runs one real vendored exercise through Qwen2.5-Coder-7B via mini-swe-agent's raw-bash
    DefaultAgent, end to end. Reuses nova_aci_harness.py's own working-copy prep and real test
    check, so this is scored identically to Nova's own ACI corpus runs. Returns
    {"slug", "turns_used", "exit_status", "test_passed", "test_output"}.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        working_copy = aci_harness._prepare_working_copy(slug, Path(tmp))
        root = str(working_copy)
        task_description = aci_harness._read_task_description(working_copy)

        model = LitellmTextbasedModel(
            model_name=OLLAMA_MODEL,
            model_kwargs={
                "api_base": OLLAMA_API_BASE,
                "drop_params": True,
                "timeout": REQUEST_TIMEOUT_SECONDS,
            },
            cost_tracking="ignore_errors",
        )
        env = LocalEnvironment(cwd=root, env=ENVIRONMENT_ENV_VARS)
        TRAJECTORY_DIR.mkdir(parents=True, exist_ok=True)
        trajectory_path = TRAJECTORY_DIR / f"{slug}_{datetime.now().strftime('%Y%m%dT%H%M%S%f')}.json"
        agent = DefaultAgent(
            model,
            env,
            system_template=SYSTEM_TEMPLATE,
            instance_template=INSTANCE_TEMPLATE,
            step_limit=MAX_TURNS,
            cost_limit=0,
            output_path=trajectory_path,
        )

        if verbose:
            import logging

            logging.basicConfig(level=logging.INFO)

        try:
            exit_info = agent.run(task_description)
            exit_status = exit_info.get("exit_status", "")
        except Exception as e:
            # A genuinely unexpected failure (not a normal stop condition -- those are all
            # InterruptAgentFlow subclasses DefaultAgent.run() already catches internally) --
            # recorded as its own status rather than crashing the whole batch run, since this
            # is a brand-new integration, not Nova's own proven-stable ACI loop.
            exit_status = f"harness_error: {type(e).__name__}: {e}"

        turns_used = agent.n_calls

        test_passed, test_output = aci_harness._run_real_tests(working_copy, slug)

        result = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "slug": slug,
            "turns_used": turns_used,
            "exit_status": exit_status,
            "test_passed": test_passed,
            "test_output": test_output,
            "trajectory_path": str(trajectory_path),
        }
        _log_result(result)
        return result


def _log_result(result: dict) -> None:
    """Appends one real run's result to RESULTS_LOG_PATH. test_output is dropped -- verbose, not needed for analysis."""
    RESULTS_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    entry = {k: v for k, v in result.items() if k != "test_output"}
    with open(RESULTS_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def run_all_exercises(verbose: bool = False, repeats: int = 1) -> list[dict]:
    """Runs every real vendored exercise under CORPUS_ROOT through run_exercise(), `repeats` times each, slug-sorted."""
    slugs = sorted(p.name for p in CORPUS_ROOT.iterdir() if p.is_dir())
    results = []
    total_runs = len(slugs) * repeats
    run_number = 0
    for slug in slugs:
        for rep in range(1, repeats + 1):
            run_number += 1
            print(f"\n[{run_number}/{total_runs}] Running {slug} (rep {rep}/{repeats})...")
            result = run_exercise(slug, verbose=verbose)
            status = "PASS" if result["test_passed"] else "FAIL"
            print(f"  -> {status} ({result['exit_status']}, {result['turns_used']} turn(s))")
            results.append(result)
    return results


def _print_summary(results: list[dict]) -> None:
    """Real aggregate numbers from a run_all_exercises() batch -- per-exercise pass rate plus overall totals."""
    total = len(results)
    passed = sum(1 for r in results if r["test_passed"])
    print(f"\n=== Summary: {passed}/{total} runs passed ===")

    by_slug: dict[str, list[dict]] = {}
    for r in results:
        by_slug.setdefault(r["slug"], []).append(r)
    for slug in sorted(by_slug):
        runs = by_slug[slug]
        slug_passed = sum(1 for r in runs if r["test_passed"])
        print(f"  {slug:<24} {slug_passed}/{len(runs)} passed")

    status_totals: dict[str, int] = {}
    for r in results:
        status_totals[r["exit_status"]] = status_totals.get(r["exit_status"], 0) + 1
    print("\nExit status breakdown:")
    for status in sorted(status_totals, key=lambda s: -status_totals[s]):
        print(f"  {status:<30} {status_totals[status]}")


if __name__ == "__main__":
    if sys.platform == "win32":
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Run one or all vendored exercises through Qwen2.5-Coder-7B via mini-swe-agent."
    )
    parser.add_argument(
        "slug",
        nargs="?",
        help="Exercise slug, e.g. 'bob' (must exist under the vendored exercise corpus). Omit with --all.",
    )
    parser.add_argument("--all", action="store_true", help="Run every vendored exercise, not just one.")
    parser.add_argument(
        "--repeat",
        type=int,
        default=1,
        metavar="N",
        help="With --all: run each exercise N times, for a real pass rate instead of one noisy sample.",
    )
    parser.add_argument("--verbose", action="store_true", help="Print mini-swe-agent's own step-by-step logging.")
    args = parser.parse_args()

    if args.all:
        results = run_all_exercises(verbose=args.verbose, repeats=args.repeat)
        _print_summary(results)
    elif args.slug:
        result = run_exercise(args.slug, verbose=args.verbose)
        print(f"\n=== {result['slug']} ===")
        print(f"Exit status: {result['exit_status']} ({result['turns_used']} turn(s) used)")
        print(f"Tests passed: {result['test_passed']}")
        print(f"\n--- Test output ---\n{result['test_output']}")
    else:
        parser.error("Provide a slug or use --all.")
