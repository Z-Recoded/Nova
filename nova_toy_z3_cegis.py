# Toy CEGIS (counterexample-guided inductive synthesis) loop using Z3.
# Exploratory only (ClickUp 86bbk03gg) — builds hands-on intuition for the
# generate -> verify -> refine shape planned for the coder specialist's
# gatekeeper/validation stage. Not production infrastructure.

from z3 import BitVec, ForAll, If, Not, Solver, sat, unsat


def synthesize_constant_choice() -> None:
    """
    First pass: search a deliberately too-narrow candidate space (pick a
    constant "always return x" or "always return y") against sampled
    counterexamples, so the loop visibly fails to converge. Demonstrates why
    CEGIS needs a candidate space that can actually express the spec.
    """
    counterexamples: list[tuple[int, int]] = [(0, 0)]

    def result_for(choose_x: bool, cx: int, cy: int) -> int:
        return cx if choose_x else cy

    def spec_holds(choose_x: bool, cx: int, cy: int) -> bool:
        result = result_for(choose_x, cx, cy)
        return result >= cx and result >= cy and result in (cx, cy)

    for round_num in range(1, 6):
        survivors = [
            choose_x for choose_x in (True, False) if all(spec_holds(choose_x, cx, cy) for cx, cy in counterexamples)
        ]

        if not survivors:
            print(
                f"round {round_num}: no constant candidate survives {len(counterexamples)} "
                "counterexample(s) — candidate space is too narrow"
            )
            return

        # Ask Z3 for a real input pair where the surviving candidate breaks the spec.
        choose_x = survivors[0]
        cx_var, cy_var = BitVec("cx", 32), BitVec("cy", 32)
        result_expr = cx_var if choose_x else cy_var
        finder = Solver()
        finder.add(
            Not((result_expr >= cx_var) & (result_expr >= cy_var) & ((result_expr == cx_var) | (result_expr == cy_var)))
        )
        finder.add(cx_var >= -100, cx_var <= 100, cy_var >= -100, cy_var <= 100)

        if finder.check() != sat:
            print(
                f"round {round_num}: candidate 'choose_x={choose_x}' has no counterexample in "
                "range — false convergence from an under-constrained search"
            )
            return

        model = finder.model()
        # as_long() returns the unsigned bit pattern; BitVec comparisons above are
        # signed, so the counterexample must be read back with as_signed_long() or
        # the Python-side spec_holds() re-check disagrees with what Z3 verified,
        # letting a broken candidate falsely "survive" (found live: x=0, y=4294967294
        # looked spec-satisfying in plain Python but is really y=-2, a real violation).
        new_cx, new_cy = model[cx_var].as_signed_long(), model[cy_var].as_signed_long()
        print(
            f"round {round_num}: candidate 'choose_x={choose_x}' survived, but Z3 found "
            f"counterexample x={new_cx}, y={new_cy} — refining"
        )
        counterexamples.append((new_cx, new_cy))

    print("round budget exhausted without convergence")


def synthesize_conditional_choice() -> None:
    """
    Second pass: widen the candidate space to include a comparison-gated
    branch (if x >= y then x else y) and verify it for ALL 32-bit integers
    via ForAll, not just sampled counterexamples — this is what a real CEGIS
    termination proof looks like.
    """
    x, y = BitVec("x", 32), BitVec("y", 32)
    candidate = If(x >= y, x, y)

    spec = ForAll(
        [x, y],
        (candidate >= x) & (candidate >= y) & ((candidate == x) | (candidate == y)),
    )

    solver = Solver()
    solver.add(Not(spec))

    if solver.check() == unsat:
        print("verified correct for ALL 32-bit x, y (no counterexample exists)")
    else:
        print(f"counterexample found: {solver.model()}")


if __name__ == "__main__":
    print("=== pass 1: candidate space too narrow (constant choice) ===")
    synthesize_constant_choice()
    print()
    print("=== pass 2: candidate space wide enough (comparison-gated choice), full verification ===")
    synthesize_conditional_choice()
