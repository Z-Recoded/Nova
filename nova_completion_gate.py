# nova_completion_gate.py
# Ground-truth completion gate (86bb71x39) -- a harness-level, model-
# independent check that runs on a coding task's final diff before it's
# trusted as "completed", instead of relying only on the model's own
# self-report or a downstream LLM review (nova_orchestrator._review_coding_
# diff()). Built in direct response to a real false-completion incident
# (2026-08-01 held-out eval, Task 3: the model stopped after reading 2 files
# with zero tool calls and a plain-text summary, and the turn loop's own
# "no more tool calls means done" logic reported "completed" against a
# genuinely empty diff) and a real research finding cited on the sibling
# self-verification task (86bb71x2a): LLM judges reading a trajectory/diff
# are unreliable at catching false completion (AUROC <= 0.65 across 5
# judges/5 prompting strategies). This gate deliberately never judges the
# diff's correctness -- only mechanical, structural facts about it.
#
# extract_task_requirements() is the one piece that DOES call Claude -- but
# only to parse the task's own spec text into structure, before any work has
# started. It never sees the diff or the agent's trajectory, so it is not
# the "LLM judge" pattern the research above warns about -- it's the same
# shape as nova_task_queue.propose_tier()'s existing non-agentic triage
# call, not a correctness judgment.

import ast
import builtins
import json
import os
import re
import subprocess
from pathlib import Path

import anthropic
from dotenv import load_dotenv

# Loaded here (not just relied on from nova_orchestrator.py's own
# load_dotenv() call) so this module works correctly when imported or run
# standalone, same discipline as nova_orchestrator.py/nova_remote_inference.py.
load_dotenv(dotenv_path=Path(__file__).parent / ".env")

# ── Config ─────────────────────────────────────────────────────

# Matches nova_orchestrator.NOVA_AGENT_MODEL -- duplicated here rather than
# imported, since nova_orchestrator.py is the one that imports this module
# (importing it back would be circular).
EXTRACTION_MODEL = "claude-sonnet-5"

# Generous relative to the extraction task's small expected output (a JSON
# object with a few short string lists) -- but extended thinking can eat an
# entire small token budget before any real text is emitted, regardless of
# how simple the final output is. Exact lesson nova_orchestrator.
# _review_coding_diff() learned the hard way at max_tokens=600 on a real
# diff-review call (5/6 reviews came back with no usable text block).
# Sized well above that risk, not just the expected output length.
EXTRACTION_MAX_TOKENS = 1024

EXTRACTION_SYSTEM_PROMPT = (
    "You extract structured requirements from a software task description. "
    "You are not being asked whether any work is correct or complete -- only "
    "to identify what the task text itself explicitly names, before any work "
    "has started. Respond with ONLY a JSON object, no other text, in exactly "
    "this shape:\n\n"
    '{"required_files": ["<path or filename explicitly named as something to '
    'create or modify>", ...], '
    '"forbidden_files": ["<path or filename the task explicitly says NOT to '
    'touch or change, or says to preserve/leave unchanged>", ...], '
    '"narrow_scope_files": ["<path or filename the task explicitly says to '
    "change only a small, targeted amount -- phrasing like 'only add X', "
    "'just change Y', 'preserve all existing behavior otherwise' -- as "
    "opposed to a rewrite, refactor, or broad restructuring. Do not include a "
    "file here unless the task text draws a real contrast between a small "
    'change and a bigger one it does NOT want>", ...], '
    '"deliverables": ["<specific named function, route, class, or constant '
    'the task explicitly asks to exist when finished>", ...]}\n\n'
    "Only include items the task text explicitly names. Do not guess or "
    "infer files/functions that seem related but aren't actually named. "
    "Empty lists are correct and expected when the task doesn't name "
    "anything in a category."
)


def extract_task_requirements(task_description: str) -> dict:
    """
    One-time, non-agentic Claude call that parses a task's own spec text
    into structured requirements, before any work starts -- see this
    module's header comment for why this is not the "LLM judge" pattern
    the false-completion research warns against (it never sees a diff or
    trajectory, only the original task text).

    Mirrors nova_task_queue.propose_tier()'s established pattern: a plain
    client.messages.create() call, no tool use, no second turn. Same
    ThinkingBlock gotcha already found live in propose_tier()/
    request_correction()/_review_coding_diff() (86bb53hmk) -- find the
    first block with type == "text" explicitly, never assume content[0]
    is the text block.

    Returns {"required_files": [...], "forbidden_files": [...],
    "narrow_scope_files": [...], "deliverables": [...]}. Fails toward an
    all-empty result (every downstream check becomes a no-op, not a false
    failure) on a missing API key or any parse/API failure -- this is an
    enhancement over a bare nonzero-diff check, not the sole source of
    truth, so failing open is the right default here. Unlike propose_tier()'s
    fail-toward-restrictive (an under-confident tier is the safe direction
    there), an under-populated requirement list is the safe direction here
    -- it silently skips a check rather than raising a false alarm off a
    malformed extraction.
    """
    empty_result = {
        "required_files": [],
        "forbidden_files": [],
        "narrow_scope_files": [],
        "deliverables": [],
    }

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        return empty_result

    try:
        client = anthropic.Anthropic(api_key=api_key)
        message = client.messages.create(
            model=EXTRACTION_MODEL,
            max_tokens=EXTRACTION_MAX_TOKENS,
            system=EXTRACTION_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": task_description}],
        )
        text_blocks = [block.text for block in message.content if block.type == "text"]
        if not text_blocks:
            return empty_result
        raw = text_blocks[0].strip()
        # Same markdown-fence gotcha nova_task_queue.propose_tier() found
        # live 2026-07-19 -- Claude sometimes wraps the JSON in ```json
        # despite being told not to.
        unfenced = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.IGNORECASE).strip()
        parsed = json.loads(unfenced)
        return {
            "required_files": list(parsed.get("required_files", [])),
            "forbidden_files": list(parsed.get("forbidden_files", [])),
            "narrow_scope_files": list(parsed.get("narrow_scope_files", [])),
            "deliverables": list(parsed.get("deliverables", [])),
        }
    except (anthropic.APIError, json.JSONDecodeError, KeyError, TypeError, AttributeError):
        return empty_result


# ── Diff parsing helpers ──────────────────────────────────────

_DIFF_FILE_HEADER_RE = re.compile(r"^diff --git a/(.+?) b/(.+?)$", re.MULTILINE)


def _touched_files(diff: str) -> list[str]:
    """
    Real file paths (post-edit side) touched by a unified git diff, parsed
    from each `diff --git a/X b/Y` header line. Uses the b/ (destination)
    path -- correct for additions, modifications, and renames alike; a
    straight deletion's b/ path won't exist on disk anymore, which the
    syntax check below already handles by skipping unreadable paths.
    """
    return [match.group(2) for match in _DIFF_FILE_HEADER_RE.finditer(diff)]


def _added_lines_text(diff: str) -> str:
    """
    Concatenated text of every added line (`+...`, excluding the `+++`
    file-header line) across the whole diff -- the substring space the
    deliverable-presence check searches against.
    """
    lines = []
    for line in diff.splitlines():
        if line.startswith("+") and not line.startswith("+++"):
            lines.append(line[1:])
    return "\n".join(lines)


# ── Checks ─────────────────────────────────────────────────────


def _check_nonzero_diff(diff: str) -> str | None:
    """Hard-fail reason if the diff is empty/whitespace-only, else None."""
    if not diff.strip():
        return "The diff is empty -- no changes were made, but the task did not report an incomplete/halted status."
    return None


def _check_required_files_touched(diff: str, required_files: list[str]) -> list[str]:
    """
    Hard-fail reasons, one per file the task's own spec explicitly named as
    something to create or modify (extract_task_requirements()'s
    required_files) that the diff never touched at all. Matches by
    filename, not exact repo-relative path -- required_files entries are
    free-text extractions that may not carry the exact path the diff
    header uses.
    """
    if not required_files:
        return []
    touched_names = {os.path.basename(path) for path in _touched_files(diff)}
    reasons = []
    for required in required_files:
        required_name = os.path.basename(required.strip())
        if required_name and required_name not in touched_names:
            reasons.append(f"'{required}' was named as a file the task requires touching, but it was never touched.")
    return reasons


def _check_syntax_valid(diff: str, root: str) -> list[str]:
    """
    Hard-fail reasons, one per touched .py file that doesn't parse as valid
    Python in its current (post-edit) worktree state. Skips files the diff
    touched but that no longer exist on disk (a straight deletion) and
    non-.py files entirely -- this check is Python-source-only, same scope
    as nova_orchestrator_runpod._find_duplicate_functions().
    """
    reasons = []
    for path in _touched_files(diff):
        if not path.endswith(".py"):
            continue
        full_path = os.path.join(root, path)
        if not os.path.isfile(full_path):
            continue
        try:
            source = open(full_path, encoding="utf-8").read()
        except OSError:
            continue
        try:
            ast.parse(source)
        except SyntaxError as e:
            reasons.append(f"'{path}' does not parse as valid Python: {e}")
    return reasons


def _assignment_target_names(target: ast.expr) -> set[str]:
    """Real names one assignment/for/with target binds -- attribute/subscript targets don't bind a new name."""
    if isinstance(target, ast.Name):
        return {target.id}
    if isinstance(target, (ast.Tuple, ast.List)):
        names: set[str] = set()
        for elt in target.elts:
            names |= _assignment_target_names(elt)
        return names
    if isinstance(target, ast.Starred):
        return _assignment_target_names(target.value)
    return set()


def _names_bound_by_statement(stmt: ast.stmt) -> set[str]:
    """
    Names a single top-level statement adds to the module namespace once it
    finishes executing. Deliberately conservative: statement kinds not
    explicitly handled here (if/try/while/for-else, etc.) contribute no
    names -- same accepted-gap philosophy as every other best-effort check
    in this file (e.g. nova_tools._cd_targets_outside_root's own doc on
    what it doesn't try to model). A name legitimately bound only inside a
    top-level `if` block would be treated as unbound afterward by this
    checker, a known, accepted false-positive risk -- this codebase doesn't
    lean on that pattern today (verified against every real top-level .py
    file in the repo before this check was wired in).
    """
    if isinstance(stmt, ast.Import):
        return {alias.asname or alias.name.split(".")[0] for alias in stmt.names}
    if isinstance(stmt, ast.ImportFrom):
        return {alias.asname or alias.name for alias in stmt.names if alias.name != "*"}
    if isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
        return {stmt.name}
    if isinstance(stmt, ast.Assign):
        names: set[str] = set()
        for target in stmt.targets:
            names |= _assignment_target_names(target)
        return names
    if isinstance(stmt, ast.AnnAssign) and stmt.target is not None:
        return _assignment_target_names(stmt.target)
    if isinstance(stmt, (ast.For, ast.AsyncFor)):
        return _assignment_target_names(stmt.target)
    if isinstance(stmt, (ast.With, ast.AsyncWith)):
        names = set()
        for item in stmt.items:
            if item.optional_vars is not None:
                names |= _assignment_target_names(item.optional_vars)
        return names
    return set()


class _TopLevelLoadNameCollector(ast.NodeVisitor):
    """
    Collects every ast.Name(ctx=Load) referenced within one top-level
    statement's OWN immediate execution -- explicitly not descending into
    nested function/lambda bodies, since those run later, at call time, by
    which point the whole module has finished loading and a forward
    reference to a name defined further down the file is completely
    legitimate (the normal, common case, not a bug). A class body, unlike a
    function body, DOES execute immediately when the ClassDef statement
    runs, so it's walked normally; methods defined inside that class body
    are themselves function bodies and are skipped the same way as any
    other nested function.

    Known, accepted scope limit: a function's own decorator expressions and
    default-argument values technically evaluate at def-time too, but
    aren't walked here -- narrowing this to the two real, observed bug
    shapes (a module-level dict/expression statement referencing a name not
    yet bound) rather than a fully exhaustive checker, matching this file's
    established best-effort philosophy elsewhere.
    """

    def __init__(self) -> None:
        self.names: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Load):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass  # deferred execution -- don't descend

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_Lambda(self, node: ast.Lambda) -> None:
        pass

    def _visit_comprehension(self, node) -> None:
        """
        List/set/dict comprehensions and generator expressions create their
        own scope in Python 3 -- the loop variable(s) they bind (e.g. `src`
        in `[src["path"] for src in SOURCES]`) are valid only within the
        comprehension itself and never need to already exist outside it.
        Real false positives found and fixed before this check was wired
        in: 6 of this repo's own real files hit exactly this shape, most
        commonly `[x[...] for x in SOME_LIST]` at module level.

        The first generator's iterable is the one exception -- it evaluates
        in the ENCLOSING scope (there'd be nothing to iterate otherwise),
        so it's visited normally against the outer collector's own bound
        names. Everything else (the element expression, any `if` filters,
        and any later generators in a multi-`for` comprehension) is
        collected separately and only flagged if it references something
        neither comprehension-local nor already bound outside.
        """
        comp_bound: set[str] = set()
        for index, generator in enumerate(node.generators):
            if index == 0:
                self.visit(generator.iter)
            else:
                nested = _TopLevelLoadNameCollector()
                nested.visit(generator.iter)
                self.names |= nested.names - comp_bound
            comp_bound |= _assignment_target_names(generator.target)
            for if_clause in generator.ifs:
                nested = _TopLevelLoadNameCollector()
                nested.visit(if_clause)
                self.names |= nested.names - comp_bound

        elt_nodes = [node.key, node.value] if isinstance(node, ast.DictComp) else [node.elt]
        for elt in elt_nodes:
            nested = _TopLevelLoadNameCollector()
            nested.visit(elt)
            self.names |= nested.names - comp_bound

    visit_ListComp = _visit_comprehension
    visit_SetComp = _visit_comprehension
    visit_DictComp = _visit_comprehension
    visit_GeneratorExp = _visit_comprehension


# Known process-exit calls -- used only by _handler_terminates() below to
# recognize the one real, common try/except shape found in this repo:
# `except ...: print(...); sys.exit(1)`. A bare name (exit/quit, the
# REPL/interactive builtins) or a `module.func` attribute call matching one
# of these pairs is treated as "this path never falls through."
_EXIT_CALL_NAMES = {"exit", "quit"}
_EXIT_CALL_ATTRS = {("sys", "exit"), ("os", "_exit"), ("os", "abort")}


def _statement_terminates_control_flow(stmt: ast.stmt) -> bool:
    """
    True if `stmt` unconditionally ends control flow (raises, returns,
    breaks/continues, or calls a known process-exit function) rather than
    falling through to whatever comes after it. Deliberately narrow -- not
    a general reachability analyzer, just enough to recognize the one real
    pattern this check needs (see _handler_terminates()'s own docstring).
    """
    if isinstance(stmt, (ast.Raise, ast.Return, ast.Break, ast.Continue)):
        return True
    if isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call):
        func = stmt.value.func
        if isinstance(func, ast.Name) and func.id in _EXIT_CALL_NAMES:
            return True
        if isinstance(func, ast.Attribute) and isinstance(func.value, ast.Name):
            if (func.value.id, func.attr) in _EXIT_CALL_ATTRS:
                return True
    return False


def _handler_terminates(handler: ast.ExceptHandler) -> bool:
    """
    True if an except handler's last statement always ends control flow
    instead of falling through. Real, common pattern found and fixed
    before this check was wired in: `try: capacity_report = f() except
    (...): print(...); sys.exit(1)` -- the ONLY way code after the
    try/except runs is via the try body succeeding, since the handler
    always exits the process, so `capacity_report` is safe to treat as
    bound afterward even though it was only assigned inside the try body.
    """
    if not handler.body:
        return False
    return _statement_terminates_control_flow(handler.body[-1])


# Names always available in a module's namespace without an explicit
# import/assignment -- Python builtins plus the standard module dunders
# present in every module by default.
_ALWAYS_BOUND_NAMES = frozenset(dir(builtins)) | {
    "__name__",
    "__file__",
    "__doc__",
    "__builtins__",
    "__package__",
    "__spec__",
    "__loader__",
}


def _check_statement_sequence(statements: list[ast.stmt], bound: set[str], path: str) -> list[str]:
    """
    Checks one ordered sequence of statements (a module body, or the nested
    body/orelse/handler/finalbody of a compound statement) against `bound`,
    mutating it in place as each statement's own bindings land -- so a
    later statement in the SAME sequence correctly sees names bound by an
    earlier one.

    Recurses into if/while/for/with/try so a very common real pattern --
    `if __name__ == "__main__": parser = argparse.ArgumentParser(); args =
    parser.parse_args()` -- is tracked correctly in order (an earlier line
    inside the block legitimately binds a name a later line inside the SAME
    block then uses). Real bug found and fixed before this check was ever
    wired into the gate: treating a compound statement's whole body as one
    opaque, unordered blob (rather than recursing into it as its own
    sequence) produced 43 false positives across this repo's own real
    files, every single one this exact if-__main__-block shape -- would
    have made the gate cry wolf constantly, worse than not having the
    check at all.

    Nested bindings are checked against a COPY of `bound`, never merged
    back into the caller's set afterward -- a conditional block might not
    run at all, so anything it binds should not be assumed available to
    code after the block ends. Conservative, matches Python's real "maybe
    bound" semantics; this codebase doesn't lean on top-level conditional
    imports today (verified in the same false-positive sweep above).
    """
    reasons: list[str] = []
    for stmt in statements:
        own_names = _TopLevelLoadNameCollector()
        if isinstance(stmt, ast.If):
            own_names.visit(stmt.test)
        elif isinstance(stmt, ast.While):
            own_names.visit(stmt.test)
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            own_names.visit(stmt.iter)
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            for item in stmt.items:
                own_names.visit(item.context_expr)
        elif isinstance(stmt, ast.Try):
            pass  # nothing of its own to check before descending into body/handlers
        elif isinstance(stmt, ast.ClassDef):
            for base in stmt.bases:
                own_names.visit(base)
            for keyword in stmt.keywords:
                own_names.visit(keyword.value)
        else:
            own_names.visit(stmt)

        unbound = own_names.names - bound - _ALWAYS_BOUND_NAMES
        for name in sorted(unbound):
            reasons.append(
                f"'{path}' line {stmt.lineno}: '{name}' is referenced before anything binds it in this "
                f"file (or it's never bound at all) -- this would raise NameError the instant the module "
                f"is imported, not just a style nit."
            )

        if isinstance(stmt, ast.If):
            # Unlike every other compound statement here, an if/else where
            # BOTH branches bind the same name is unconditionally safe to
            # propagate outward -- every real execution path binds it. Real,
            # common pattern found and fixed before this check was wired
            # in: `if x: report = a() else: report = b()` then `print(
            # report)` -- flagged as a false positive until this
            # intersection logic was added. A bare `if` with no `else`
            # stays fully conservative (nothing propagates), same as
            # every other compound statement below -- the "didn't enter
            # the block" path really might leave a name unbound.
            body_bound = set(bound)
            orelse_bound = set(bound)
            reasons.extend(_check_statement_sequence(stmt.body, body_bound, path))
            reasons.extend(_check_statement_sequence(stmt.orelse, orelse_bound, path))
            if stmt.orelse:
                guaranteed = (body_bound - bound) & (orelse_bound - bound)
                bound |= guaranteed
        elif isinstance(stmt, ast.While):
            reasons.extend(_check_statement_sequence(stmt.body, set(bound), path))
            reasons.extend(_check_statement_sequence(stmt.orelse, set(bound), path))
        elif isinstance(stmt, (ast.For, ast.AsyncFor)):
            nested_bound = set(bound) | _assignment_target_names(stmt.target)
            reasons.extend(_check_statement_sequence(stmt.body, nested_bound, path))
            reasons.extend(_check_statement_sequence(stmt.orelse, set(bound), path))
        elif isinstance(stmt, (ast.With, ast.AsyncWith)):
            nested_bound = set(bound)
            for item in stmt.items:
                if item.optional_vars is not None:
                    nested_bound |= _assignment_target_names(item.optional_vars)
            reasons.extend(_check_statement_sequence(stmt.body, nested_bound, path))
        elif isinstance(stmt, ast.Try):
            try_bound = set(bound)
            reasons.extend(_check_statement_sequence(stmt.body, try_bound, path))
            all_handlers_terminate = bool(stmt.handlers) and all(_handler_terminates(h) for h in stmt.handlers)
            for handler in stmt.handlers:
                handler_bound = set(bound)
                if handler.name:
                    handler_bound.add(handler.name)
                reasons.extend(_check_statement_sequence(handler.body, handler_bound, path))
            reasons.extend(_check_statement_sequence(stmt.orelse, set(bound), path))
            reasons.extend(_check_statement_sequence(stmt.finalbody, set(bound), path))
            if all_handlers_terminate:
                bound |= try_bound - bound
        elif isinstance(stmt, ast.ClassDef):
            # A class body executes immediately, top to bottom, just like a
            # module -- a method def'd earlier in the same class body is
            # legitimately bound for a later statement in that same body
            # (e.g. `visit_ListComp = _visit_comprehension` right after
            # `def _visit_comprehension(...):`). Real false positive found
            # in this very file before this branch was added. Only the
            # class NAME itself (handled by _names_bound_by_statement)
            # propagates outward -- attributes/methods stay class-local.
            reasons.extend(_check_statement_sequence(stmt.body, set(bound), path))

        bound |= _names_bound_by_statement(stmt)
    return reasons


def _check_module_level_name_order(diff: str, root: str) -> list[str]:
    """
    Hard-fail reasons, one per real NameError-class bug: a name referenced
    before anything has bound it yet, purely from source order. Python
    executes a module's top-level statements top-to-bottom -- referencing a
    name before its binding statement has run is a real, 100%-reproducible
    NameError the instant the module is imported, even though
    ast.parse()/_check_syntax_valid() sees it as perfectly valid syntax
    (neither of those execute or resolve names, only parse grammar).

    Built after this exact failure shape was independently reproduced
    TWICE in one real held-out eval run (2026-08-01): a dict value calling
    a function imported on a later line, and a real `import time` line
    mangled into a bare `time` expression statement referencing a name
    never bound anywhere in the file at all. Neither is a syntax error, so
    neither was ever caught before this check existed.

    Deliberately static, never a real `import <module>` subprocess call:
    several of this repo's own modules (nova_query.py, ingest.py,
    graph_builder.py) construct a live Chroma HttpClient at module scope --
    actually importing an arbitrary touched file could make a real,
    network-dependent call as a side effect of a supposedly cheap,
    deterministic completion check. This only ever inspects the module's
    own AST, in source order, against names bound by everything earlier --
    it never executes anything.

    Known, accepted limitation: walrus-operator bindings (`if (n :=
    f()) > 0:`) aren't modeled as bindings at all, so a name bound only via
    `:=` and used afterward will be (incorrectly) flagged. Left unfixed
    deliberately -- verified zero real files in this repo use that pattern
    today (the same real-file sweep that shook out and fixed every other
    false positive here found none), so chasing full correctness for a
    pattern with no real evidence of use would be scope creep beyond what
    this check actually needs to earn its keep.
    """
    reasons = []
    for path in _touched_files(diff):
        if not path.endswith(".py"):
            continue
        full_path = os.path.join(root, path)
        if not os.path.isfile(full_path):
            continue
        try:
            source = open(full_path, encoding="utf-8").read()
            tree = ast.parse(source)
        except (OSError, SyntaxError):
            continue  # unreadable or a real syntax error -- _check_syntax_valid already reports that case

        reasons.extend(_check_statement_sequence(tree.body, set(), path))
    return reasons


def _check_forbidden_paths_untouched(diff: str, forbidden_files: list[str]) -> list[str]:
    """
    Hard-fail reasons, one per file the task's own spec explicitly named as
    off-limits (extract_task_requirements()'s forbidden_files) that the
    diff touched anyway. Matches by filename, same rationale as
    _check_required_files_touched().

    Real gap found by testing this function against the actual 2026-07-29
    scope-violation incident's real task text: that task's forbidden files
    (nova_api.py/nova_tools.py/nova_orchestrator.py) were never touched --
    the model's real violation was drastically over-rewriting an *allowed*
    file (nova_query.py) far beyond the "just add an early-return branch"
    scope, deleting the real RAG pipeline in the process. This check
    catches "touched a fully off-limits file." The sibling shape --
    "touched an allowed file far more than the task intended" -- is what
    _check_narrow_scope_not_exceeded() below targets instead.
    """
    if not forbidden_files:
        return []
    touched = _touched_files(diff)
    reasons = []
    for forbidden in forbidden_files:
        forbidden_name = os.path.basename(forbidden.strip())
        if not forbidden_name:
            continue
        for touched_path in touched:
            if os.path.basename(touched_path) == forbidden_name:
                reasons.append(
                    f"'{touched_path}' was touched, but the task explicitly said not to change '{forbidden}'."
                )
    return reasons


# A file changed more than this fraction of its original line count is
# treated as "far more than a small/targeted edit" -- picked to be well
# above normal editing noise (a real single-purpose edit rarely rewrites
# more than half a file) while still catching a wholesale rewrite like the
# 2026-07-29 incident (the RAG pipeline was not trimmed, it was replaced).
# Deliberately approximate, not a precise line -- see this check's own
# docstring on tuning.
NARROW_SCOPE_CHANGE_RATIO_THRESHOLD = 0.5

# Below this many original lines, "50% changed" isn't a meaningful signal
# (a 10-line file losing 6 lines to a genuinely small edit is normal) --
# skip the ratio check entirely for files this small.
NARROW_SCOPE_MIN_ORIGINAL_LINES = 20


def _diff_numstat(root: str, base_ref: str) -> dict[str, tuple[int, int]]:
    """
    {path: (added, removed)} for every file changed between base_ref and
    the worktree's current state, via `git diff --numstat` -- more
    reliable than hand-parsing the diff text's hunk headers (which only
    carry as much context as git's default -U3, not necessarily enough to
    infer a file's real total line count).
    """
    result = subprocess.run(
        ["git", "diff", "--numstat", base_ref],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    stats: dict[str, tuple[int, int]] = {}
    for line in result.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        added, removed, path = parts
        if added == "-" or removed == "-":
            continue  # binary file -- numstat reports "-" instead of a count
        stats[path] = (int(added), int(removed))
    return stats


def _original_line_count(root: str, base_ref: str, path: str) -> int | None:
    """Total line count of `path` at base_ref, or None if it didn't exist there (a new file)."""
    result = subprocess.run(
        ["git", "show", f"{base_ref}:{path}"],
        cwd=root,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    if result.returncode != 0:
        return None
    return len(result.stdout.splitlines())


def _check_narrow_scope_not_exceeded(root: str, base_ref: str, narrow_scope_files: list[str]) -> list[str]:
    """
    Hard-fail reasons, one per file the task's own spec said should get
    only a small, targeted edit (extract_task_requirements()'s
    narrow_scope_files) whose real diff removed more than
    NARROW_SCOPE_CHANGE_RATIO_THRESHOLD of its original line count.

    Built in direct response to the real gap _check_forbidden_paths_
    untouched() documents in its own docstring: the 2026-07-29 scope-
    violation incident's real violation was over-rewriting an ALLOWED file
    (nova_query.py) far beyond the "just add an early-return branch" scope,
    not touching a forbidden one. This check targets exactly that shape --
    matched by filename, same rationale as the other checks in this module.

    Line-count ratio is a real approximation, not a precise correctness
    signal -- a legitimate large refactor explicitly asked for elsewhere in
    the same task would not be flagged (only files actually named as
    narrow-scope are checked), but a genuinely small file with unusual
    formatting could still produce a false positive. Treated as a hard fail
    despite that, because this check exists specifically for the most
    severe entry in the failure registry (a full pipeline deletion) --
    surfacing an occasional false positive for a human to dismiss is a far
    better trade than missing a repeat of that incident.
    """
    if not narrow_scope_files:
        return []
    stats = _diff_numstat(root, base_ref)
    reasons = []
    for narrow_file in narrow_scope_files:
        name = os.path.basename(narrow_file.strip())
        if not name:
            continue
        matching_path = next((path for path in stats if os.path.basename(path) == name), None)
        if matching_path is None:
            continue  # not touched at all -- not this check's concern
        _added, removed = stats[matching_path]
        original_lines = _original_line_count(root, base_ref, matching_path)
        if original_lines is None or original_lines < NARROW_SCOPE_MIN_ORIGINAL_LINES:
            continue
        change_ratio = removed / original_lines
        if change_ratio > NARROW_SCOPE_CHANGE_RATIO_THRESHOLD:
            reasons.append(
                f"'{matching_path}' was marked for a small/targeted edit only, but {removed} of its "
                f"original {original_lines} lines were removed ({change_ratio:.0%}) -- looks like a "
                f"much larger rewrite than the task asked for."
            )
    return reasons


def _check_deliverables_present(diff: str, deliverables: list[str]) -> list[str]:
    """
    Soft-flag warnings, one per named deliverable (extract_task_requirements()'s
    deliverables) that never appears in any added line of the diff. Not a
    hard fail -- free-text extraction has real false-positive risk (a name
    mentioned only for context, not as something to create), so this is
    surfaced for the human/review pass rather than blocking completion.
    """
    if not deliverables:
        return []
    added_text = _added_lines_text(diff)
    warnings = []
    for deliverable in deliverables:
        name = deliverable.strip()
        if name and name not in added_text:
            warnings.append(f"'{name}' was named as a deliverable but never appears in the diff's added lines.")
    return warnings


# ── Entry point ────────────────────────────────────────────────


def check_ground_truth_completion(
    diff: str, task_description: str, root: str, base_ref: str = "master", requirements: dict | None = None
) -> dict:
    """
    Runs every ground-truth check and returns {"passed": bool, "hard_fails":
    [...], "warnings": [...]}. "passed" is False if any hard-fail check
    found something -- callers should surface that loudly (see
    nova_orchestrator.run_coding_task()'s commit_note handling) rather than
    let a false "completed" status go unnoticed, per 86bb71x39's whole
    point. Never blocks the commit itself -- Marvin reviews every diff by
    hand regardless, same non-blocking precedent as _review_coding_diff().

    base_ref defaults to "master" -- correct for run_coding_task()'s real
    call site, which always diffs against master via
    _git_diff_against_master(). Only needs overriding by a caller diffing
    against a different base (e.g. a held-out eval harness using a
    historical task's real pre-merge commit).

    requirements: pass a dict already produced by extract_task_requirements()
    to skip re-extracting it here -- the RunPod backend's task-scoped file
    allowlist guard (86bb72wd5) needs this exact extraction before the task
    even starts, and re-running the same Claude call a second time at the
    end of the same task would be a real, avoidable duplicate cost. None
    (the default) preserves the original behavior: extract fresh here.

    An empty diff short-circuits immediately: there is no point extracting
    requirements or checking syntax/deliverables against a diff with
    nothing in it, and skipping the API call here means a genuinely
    incomplete task never spends a Claude call it doesn't need.
    """
    empty_diff_reason = _check_nonzero_diff(diff)
    if empty_diff_reason:
        return {"passed": False, "hard_fails": [empty_diff_reason], "warnings": []}

    if requirements is None:
        requirements = extract_task_requirements(task_description)

    hard_fails = []
    hard_fails.extend(_check_syntax_valid(diff, root))
    hard_fails.extend(_check_module_level_name_order(diff, root))
    hard_fails.extend(_check_required_files_touched(diff, requirements["required_files"]))
    hard_fails.extend(_check_forbidden_paths_untouched(diff, requirements["forbidden_files"]))
    hard_fails.extend(_check_narrow_scope_not_exceeded(root, base_ref, requirements["narrow_scope_files"]))
    warnings = _check_deliverables_present(diff, requirements["deliverables"])

    return {"passed": not hard_fails, "hard_fails": hard_fails, "warnings": warnings}
