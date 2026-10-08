"""ToolIndex.index_mcp_tools must never upsert duplicate ids.

ChromaDB rejects an upsert whose id list has repeats ("Expected IDs to be
unique, found duplicates of: mcp_get"), which took the whole RAG tool
selection down to the keyword fallback. Even if the prompt text parser
mis-reads a line, the index has to stay healthy.
"""

from src.tool_index import ToolIndex


class _FakeCollection:
    def __init__(self):
        self.upserted_ids = None

    def get(self, where=None):
        return {"ids": []}

    def delete(self, ids=None):
        pass

    def upsert(self, ids, documents, embeddings, metadatas):
        assert len(ids) == len(set(ids)), f"duplicate ids reached chroma: {ids}"
        self.upserted_ids = list(ids)


class _FakeEmbedder:
    def encode(self, texts, normalize_embeddings=True):
        return [[0.0, 1.0] for _ in texts]


class _FakeMcpManager:
    _generation = 1

    def __init__(self, prompt_text):
        self._prompt_text = prompt_text

    def get_tool_descriptions_for_prompt(self, disabled_map=None):
        return self._prompt_text


def _bare_index() -> ToolIndex:
    idx = ToolIndex.__new__(ToolIndex)
    idx._collection = _FakeCollection()
    idx._embedder = _FakeEmbedder()
    idx._fingerprint = ""
    idx._mcp_generation = -1
    idx._healthy = True
    return idx


def test_repeated_tool_names_are_deduplicated_before_upsert():
    prompt = (
        "**Spotify:**\n"
        "  - mcp__spotify__SpotifyPlayback: Manages playback:\n"
        "- get: current track\n"
        "- start: play\n"
        "  - mcp__spotify__SpotifyQueue: Manage the queue:\n"
        "- add: add a track\n"
        "- get: show the queue\n"
    )
    idx = _bare_index()

    idx.index_mcp_tools(_FakeMcpManager(prompt))

    ids = idx._collection.upserted_ids
    assert ids is not None
    assert len(ids) == len(set(ids))
    assert "mcp__spotify__SpotifyPlayback" in " ".join(ids)
