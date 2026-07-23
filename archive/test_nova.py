import chromadb
import ollama

# Test 1 — Chroma memory core
print("Testing Chroma...")
client = chromadb.Client()
collection = client.create_collection("nova_test")
collection.add(documents=["Nova is a persistent AI companion built for Marvin."], ids=["test1"])
results = collection.query(query_texts=["What is Nova?"], n_results=1)
print(f"Chroma result: {results['documents'][0][0]}")

# Test 2 — Ollama LLM
print("\nTesting Ollama...")
response = ollama.chat(
    model="llama3.1:8b", messages=[{"role": "user", "content": "In one sentence, what is a vector database?"}]
)  # noqa: E501
print(f"Ollama response: {response['message']['content']}")

print("\nAll systems nominal.")
