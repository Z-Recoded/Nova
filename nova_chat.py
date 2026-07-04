# nova_chat.py
# Interactive CLI for talking to Nova

from nova_query import ask
from nova_memory_store import load_history, clear_history

BANNER = """
╔══════════════════════════════════════╗
║           N O V A  v0.2              ║
║     Memory-augmented assistant       ║
╚══════════════════════════════════════╝
Commands: 'sources', 'clear', 'quit'
"""

def main():
    print(BANNER)

    # Load persistent history from last session
    history = load_history()
    if history:
        exchanges = len(history) // 2
        print(f"  Resumed {exchanges} exchange(s) from last session.\n")

    last_sources = []
    last_category = ""

    while True:
        try:
            query = input("You: ").strip()
        except (KeyboardInterrupt, EOFError):
            print("\nNova offline.")
            break

        if not query:
            continue

        if query.lower() in ("quit", "exit"):
            print("Nova offline.")
            break

        if query.lower() == "sources":
            if last_sources:
                print(f"\n[{last_category}] Sources: {', '.join(last_sources)}\n")
            else:
                print("No previous query.\n")
            continue

        if query.lower() == "clear":
            clear_history()
            history = []
            last_sources = []
            print("Conversation history cleared.\n")
            continue

        print("\nNova: ", end="", flush=True)
        result = ask(query, history=history, persist=True)
        answer = result["answer"]
        print(answer)
        print()

        # Update in-memory history for this session
        history.append({"role": "user", "content": query})
        history.append({"role": "assistant", "content": answer})

        last_sources = result["sources"]
        last_category = result["category"]

if __name__ == "__main__":
    main()
