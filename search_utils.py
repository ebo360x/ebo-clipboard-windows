import unicodedata

SEARCH_NGRAM_SIZE = 3


ARABIC_ALEF_VARIANTS = str.maketrans({
    "\u0622": "\u0627",
    "\u0623": "\u0627",
    "\u0625": "\u0627",
    "\u0671": "\u0627",
})


def normalize_search_text(text):
    normalized = unicodedata.normalize("NFKC", text).translate(ARABIC_ALEF_VARIANTS).lower()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and character != "\u0640"
        and unicodedata.category(character) != "Mn"
    )


def build_ngram_index(texts, gram_size=SEARCH_NGRAM_SIZE):
    index = {}
    for row_index, values in enumerate(texts):
        for text in values:
            grams = {
                text[offset:offset + gram_size]
                for offset in range(len(text) - gram_size + 1)
            }
            for gram in grams:
                index.setdefault(gram, set()).add(row_index)
    return index


def find_ngram_candidates(index, query, gram_size=SEARCH_NGRAM_SIZE):
    if len(query) < gram_size:
        return None
    grams = {
        query[offset:offset + gram_size]
        for offset in range(len(query) - gram_size + 1)
    }
    postings = [index.get(gram) for gram in grams]
    if any(posting is None for posting in postings):
        return set()
    postings.sort(key=len)
    candidates = postings[0].copy()
    for posting in postings[1:]:
        candidates.intersection_update(posting)
        if not candidates:
            break
    return candidates
