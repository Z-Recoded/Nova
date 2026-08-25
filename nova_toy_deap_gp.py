# Toy genetic programming loop using DEAP, targeting symbolic regression.
# Exploratory only (ClickUp 86bbk03ma) — builds hands-on intuition for
# population-based search (many candidate program trees competing and
# recombining) as a contrast to the coder specialist's planned single-lineage
# MCTS tree search (Generate/Improve/Fix). Not production infrastructure.

import functools
import operator
import random

import numpy
from deap import algorithms, base, creator, gp, tools

# The evolved population must rediscover this hidden target formula using
# only the primitives declared below (+, -, *, protected /) — it is never
# told the formula directly, only shown (input, output) sample pairs.
TARGET_FORMULA = "x**2 + x + 1"


def target_function(x: float) -> float:
    return x**2 + x + 1


def protected_division(numerator: float, denominator: float) -> float:
    # Guards against division-by-zero crashing an entire generation — a
    # random candidate tree WILL produce a zero denominator eventually.
    return numerator / denominator if abs(denominator) > 1e-6 else 1.0


def build_primitive_set() -> gp.PrimitiveSet:
    primitives = gp.PrimitiveSet("MAIN", arity=1)
    primitives.renameArguments(ARG0="x")
    primitives.addPrimitive(operator.add, 2)
    primitives.addPrimitive(operator.sub, 2)
    primitives.addPrimitive(operator.mul, 2)
    primitives.addPrimitive(protected_division, 2)
    primitives.addPrimitive(operator.neg, 1)
    primitives.addEphemeralConstant("rand_const", functools.partial(random.uniform, -1, 1))
    return primitives


def evaluate_fitness(individual, primitives: gp.PrimitiveSet, sample_points: list[float]):
    # Mean squared error between the candidate tree's output and the true
    # target function, sampled across a fixed set of x values. DEAP minimizes
    # this, so a perfect match converges toward fitness 0.0.
    compiled = gp.compile(individual, primitives)
    squared_errors = []
    for x in sample_points:
        try:
            predicted = compiled(x)
        except (OverflowError, ZeroDivisionError):
            return (float("inf"),)
        squared_errors.append((predicted - target_function(x)) ** 2)
    return (float(numpy.mean(squared_errors)),)


def run_evolution(num_generations: int = 30, population_size: int = 200) -> None:
    primitives = build_primitive_set()

    # DEAP's creator module registers new classes globally by name — guard
    # against re-registration if this function is ever called twice in one
    # process (e.g. from a notebook), which DEAP otherwise errors on.
    if not hasattr(creator, "FitnessMin"):
        creator.create("FitnessMin", base.Fitness, weights=(-1.0,))
    if not hasattr(creator, "Individual"):
        creator.create("Individual", gp.PrimitiveTree, fitness=creator.FitnessMin)

    toolbox = base.Toolbox()
    toolbox.register("expr", gp.genHalfAndHalf, pset=primitives, min_=1, max_=3)
    toolbox.register("individual", tools.initIterate, creator.Individual, toolbox.expr)
    toolbox.register("population", tools.initRepeat, list, toolbox.individual)
    toolbox.register("compile", gp.compile, pset=primitives)

    sample_points = [x / 10.0 for x in range(-20, 21)]  # -2.0 .. 2.0
    toolbox.register("evaluate", evaluate_fitness, primitives=primitives, sample_points=sample_points)
    toolbox.register("select", tools.selTournament, tournsize=3)
    toolbox.register("mate", gp.cxOnePoint)
    toolbox.register("expr_mut", gp.genFull, min_=0, max_=2)
    toolbox.register("mutate", gp.mutUniform, expr=toolbox.expr_mut, pset=primitives)

    # DEAP's own documented mitigation for genetic programming "bloat" —
    # trees growing unboundedly large without fitness improvement. Caps
    # mate/mutate results at depth 17, well under DEAP's own ~91-primitive
    # parser-depth ceiling noted in the ticket.
    toolbox.decorate("mate", gp.staticLimit(key=operator.attrgetter("height"), max_value=17))
    toolbox.decorate("mutate", gp.staticLimit(key=operator.attrgetter("height"), max_value=17))

    population = toolbox.population(n=population_size)
    hall_of_fame = tools.HallOfFame(1)

    stats = tools.Statistics(lambda ind: ind.fitness.values[0] if ind.fitness.valid else float("inf"))
    stats.register("min", numpy.min)
    stats.register("avg", numpy.mean)

    print(f"Evolving toward target f(x) = {TARGET_FORMULA}")
    print(f"population={population_size}, generations={num_generations}, sample points={len(sample_points)}\n")

    algorithms.eaSimple(
        population,
        toolbox,
        cxpb=0.5,
        mutpb=0.2,
        ngen=num_generations,
        stats=stats,
        halloffame=hall_of_fame,
        verbose=True,
    )

    best = hall_of_fame[0]
    print(f"\nBest evolved tree (fitness={best.fitness.values[0]:.6f}):")
    print(f"  {best}")


if __name__ == "__main__":
    run_evolution()
