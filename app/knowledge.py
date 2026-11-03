"""RAG knowledge base over the documents in data/knowledge (brochures, policies, FAQs).

Uses BM25 keyword search (pure Python, no extra services). The `search` function
is the only interface the agents use, so it can later be swapped for a vector
database (pgvector / Qdrant) without changing anything else.
"""
import math
import re
import threading
from collections import Counter
from pathlib import Path

from .config import BASE_DIR

KNOWLEDGE_DIR = BASE_DIR / "data" / "knowledge"
SUPPORTED = {".md", ".txt", ".pdf"}
_STOP = set("a an the and or of to in on for is are was be with at by it this that from as your you we our".split())


def _tokens(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9؀-ۿ]+", text.lower()) if t not in _STOP]


def _read(path: Path) -> str:
    if path.suffix.lower() == ".pdf":
        from pypdf import PdfReader

        return "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    return path.read_text(encoding="utf-8", errors="ignore")


def _chunk(text: str, size: int = 900, overlap: int = 150) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n\s*\n", text) if p.strip()]
    chunks, current = [], ""
    for para in paragraphs:
        if len(current) + len(para) > size and current:
            chunks.append(current)
            current = current[-overlap:]
        current = f"{current}\n\n{para}" if current else para
    if current:
        chunks.append(current)
    return chunks


class KnowledgeBase:
    def __init__(self):
        self._lock = threading.Lock()
        self.chunks: list[dict] = []
        self.df: Counter = Counter()
        self.avg_len = 1.0

    def rebuild(self):
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        chunks = []
        for path in sorted(KNOWLEDGE_DIR.iterdir()):
            if path.suffix.lower() not in SUPPORTED:
                continue
            try:
                text = _read(path)
            except Exception as exc:  # unreadable file should not break the bot
                print(f"[knowledge] could not read {path.name}: {exc}")
                continue
            for i, chunk in enumerate(_chunk(text)):
                toks = _tokens(chunk)
                chunks.append({"source": path.name, "index": i, "text": chunk, "tf": Counter(toks), "len": len(toks)})
        df = Counter()
        for c in chunks:
            df.update(set(c["tf"]))
        with self._lock:
            self.chunks = chunks
            self.df = df
            self.avg_len = (sum(c["len"] for c in chunks) / len(chunks)) if chunks else 1.0

    def search(self, query: str, k: int = 4) -> list[dict]:
        q = _tokens(query)
        if not q or not self.chunks:
            return []
        n = len(self.chunks)
        scored = []
        for c in self.chunks:
            score = 0.0
            for term in q:
                tf = c["tf"].get(term)
                if not tf:
                    continue
                idf = math.log(1 + (n - self.df[term] + 0.5) / (self.df[term] + 0.5))
                score += idf * tf * 2.2 / (tf + 1.2 * (0.25 + 0.75 * c["len"] / self.avg_len))
            if score > 0:
                scored.append((score, c))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [{"source": c["source"], "text": c["text"], "score": round(s, 2)} for s, c in scored[:k]]

    def files(self) -> list[dict]:
        KNOWLEDGE_DIR.mkdir(parents=True, exist_ok=True)
        counts = Counter(c["source"] for c in self.chunks)
        return [{"name": p.name, "size": p.stat().st_size, "chunks": counts.get(p.name, 0)}
                for p in sorted(KNOWLEDGE_DIR.iterdir()) if p.suffix.lower() in SUPPORTED]


kb = KnowledgeBase()
