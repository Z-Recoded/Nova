# nova_router.py
# Query routing map — classifies queries before Chroma retrieval
# Routes to the right search strategy without an extra LLM call

from dataclasses import dataclass

@dataclass
class RouteResult:
    category: str        # identity | fiction | project | finance | technical | general
    n_results: int       # how many chunks to pull from Chroma
    note: str = ""       # hint passed to the system prompt for this query type

# ── Keyword maps ───────────────────────────────────────────────
IDENTITY_TRIGGERS = [
    "who am i", "who are you", "about me", "my profile", "tell me about myself",
    "what do you know about me", "describe me", "marvin royal", "my background",
    "my values", "what am i working on"
]

FICTION_TRIGGERS = [
    "null", "helel", "raven", "fatale", "luci", "varas", "aseir", "beat",
    "rhythm", "felicity", "nullius", "nox", "marisol", "kille", "sorae",
    "symphony", "sys_symphony", "arc one", "arc two", "arc three", "phase one",
    "phase two", "phase three", "phase four", "phase five", "phase six",
    "phase seven", "phase eight", "phase nine", "phase ten", "phase eleven",
    "flow college", "the kernel", "beastman", "reincarnation", "gilgamesh",
    "enkidu", "story", "fiction", "character", "scene", "worldbuilding"
]

FINANCE_TRIGGERS = [
    "money", "finance", "trading", "crypto", "bitcoin", "ethereum", "invest",
    "income", "revenue", "passive", "dividend", "etf", "futures", "smc",
    "smart money", "financial freedom", "roadmap", "instagram page", "affiliate"
]

TECHNICAL_TRIGGERS = [
    "code", "python", "algorithm", "debug", "function", "class", "script",
    "linux", "git", "sql", "postgres", "terminal", "command", "godot",
    "chroma", "ollama", "nova", "ingest", "vector", "embedding", "rag"
]

PROJECT_MAP = {
    "mood garden": "Mood Garden — Godot game about emotional input",
    "trading bot": "Automated Trading Bot",
    "content management": "Content Management Tool",
    "football": "NCAA College Football 2026",
    "ncaa": "NCAA College Football 2026",
    "memory reconstruction": "Memory Reconstruction System",
    "nova project": "Nova AI memory system",
    "faceless": "Faceless Instagram Pages / Nostalgic Polygon",
}

# ── Router ─────────────────────────────────────────────────────
def route(query: str) -> RouteResult:
    """
    Classify a query and return a RouteResult with retrieval hints.
    Fast keyword-based — no LLM call needed.
    """
    q = query.lower().strip()

    # Identity — check first so "who am I" never hits fiction
    if any(trigger in q for trigger in IDENTITY_TRIGGERS):
        return RouteResult(
            category="identity",
            n_results=3,
            note="This is a question about Marvin personally. Use the pinned profile above. Do not reference fictional characters."
        )

    # Fiction — story questions
    if any(trigger in q for trigger in FICTION_TRIGGERS):
        return RouteResult(
            category="fiction",
            n_results=6,
            note="This question is about SYS_Symphony.EXE, Marvin's creative fiction project. These are fictional characters and events, not real people."
        )

    # Specific project lookup
    for keyword, project_name in PROJECT_MAP.items():
        if keyword in q:
            return RouteResult(
                category="project",
                n_results=5,
                note=f"This question is about the '{project_name}' project."
            )

    # Finance
    if any(trigger in q for trigger in FINANCE_TRIGGERS):
        return RouteResult(
            category="finance",
            n_results=5,
            note="This question is about Marvin's financial strategy or income goals."
        )

    # Technical
    if any(trigger in q for trigger in TECHNICAL_TRIGGERS):
        return RouteResult(
            category="technical",
            n_results=5,
            note="This is a technical question. Prioritize code, tools, and system notes."
        )

    # General fallback
    return RouteResult(
        category="general",
        n_results=5,
        note=""
    )
