import re


def _terms(text: str) -> set[str]:
    latin = {part.lower() for part in re.findall(r"[A-Za-z0-9_]{3,}", text)}
    cjk = re.findall(r"[㐀-鿿]", text)
    bigrams = {"".join(cjk[index:index + 2]) for index in range(len(cjk) - 1)}
    return latin | bigrams


def retrieve_relevant_memories(user_text: str, memories: list[dict], limit: int = 5) -> list[dict]:
    query_terms = _terms(user_text)
    if not query_terms:
        return []
    ranked = []
    for memory in memories:
        overlap = query_terms & _terms(memory["content"])
        if len(overlap) >= 2:
            ranked.append((len(overlap), memory))
    ranked.sort(key=lambda item: item[0], reverse=True)
    return [memory for _, memory in ranked[:limit]]
