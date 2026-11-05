"""Knowledge layer: AP policy, GST notes and past reviewer decisions, searchable.

Three uses:
  1. policy(ref)        exact clause for a check (checks carry an explicit ref, e.g. R06 -> §6.2).
                        Citations are looked up, not retrieved, so they are always the right clause.
  2. similar_cases(run) precedent: how reviewers resolved the same kind of exception before.
                        Retrieval (hybrid) over seeded history + every reviewer note written here.
  3. ask(question, run) "Ask Ledgerline": retrieval-augmented answer from Claude, grounded in the
                        policy, the run's own audit record and precedents, with citations.

Retrieval is hybrid: BM25 (exact words, numbers, check IDs) and dense embeddings
(BAAI/bge-small-en-v1.5 via fastembed, ONNX on CPU; meaning, paraphrase) fused
with reciprocal rank fusion. If the embedding model is not available, it runs
on BM25 alone and says so. The same embeddings give rules.py a semantic
similarity for matching invoice lines to PO lines that are worded differently.
"""
from __future__ import annotations

import json
import math
import os
import re
import threading
from collections import Counter
from functools import lru_cache
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent
KB_DIR = ROOT / "knowledge"
EMBED_MODEL = os.environ.get("EMBED_MODEL", "BAAI/bge-small-en-v1.5")
EMBED_CACHE = Path(os.environ.get("EMBED_CACHE", ROOT / "models" / "embed"))
DENSE = os.environ.get("DENSE_RETRIEVAL", "1") != "0"

# each check points at the clause that governs it
POLICY_REF = {
    "R00": "§2.2", "R01": "§4.1", "R02": "§3.1", "R03": "§3.3", "R04": "§4.2", "R05": "§4.3", "R06": "§6.2",
    "R07": "§4.4", "R08": "§4.5", "R09": "§5.2", "R10": "§6.1", "R11": "§5.3", "R12": "§5.4", "R13": "§5.5",
    "R14": "§2.4", "R15": "§2.5",
}
DOC_TITLE = {"ap-policy": "AP Policy", "gst-notes": "GST notes", "precedent": "Past decision"}

_lock = threading.RLock()


# ------------------------------------------------------------------ corpus
def _chunks_from_md(path: Path) -> list[dict]:
    doc = path.stem
    out, section, cur = [], None, None
    for ln in path.read_text().splitlines():
        m2 = re.match(r"^## (§\S+)\s+(.*)", ln)
        m3 = re.match(r"^### (§\S+)\s+(.*)", ln)
        if m2:
            section = m2.group(2).strip()
            continue
        if m3:
            if cur:
                out.append(cur)
            cur = {"id": f"{doc}:{m3.group(1)}", "doc": doc, "ref": m3.group(1), "title": m3.group(2).strip(),
                   "section": section, "text": ""}
            continue
        if cur is not None and ln.strip():
            cur["text"] += (" " if cur["text"] else "") + ln.strip()
    if cur:
        out.append(cur)
    return out


def _precedent_chunk(p: dict) -> dict:
    return {"id": f"precedent:{p['id']}", "doc": "precedent", "ref": p["id"], "title": f"{p['vendor']} · {p['check']} · {p['decision']}",
            "section": "Past decisions", "check": p["check"], "decision": p["decision"], "vendor": p["vendor"], "date": p.get("date"),
            "text": f"{p['facts']} Resolution: {p['note']}", "facts": p["facts"], "note": p["note"], "amount": p.get("amount"),
            "run_id": p.get("run_id")}


def _load_precedents() -> list[dict]:
    seeded = json.loads((KB_DIR / "precedents.json").read_text())
    try:
        from . import db
        live = db.precedents()
    except Exception:
        live = []
    return [_precedent_chunk(p) for p in seeded + live]


# ------------------------------------------------------------------ BM25
_TOK = re.compile(r"[a-z0-9§.]+")
STOP = set("a an the of to in on for and or is are be by with as at it this that from if not no any an its their was were has have".split())


def tokens(s: str) -> list[str]:
    return [t.strip(".") for t in _TOK.findall(s.lower()) if t.strip(".") and t.strip(".") not in STOP]


class BM25:
    def __init__(self, docs: list[str], k1=1.4, b=0.75):
        self.toks = [tokens(d) for d in docs]
        self.k1, self.b = k1, b
        self.avg = sum(map(len, self.toks)) / max(1, len(self.toks))
        df = Counter(t for ts in self.toks for t in set(ts))
        n = len(self.toks)
        self.idf = {t: math.log(1 + (n - c + .5) / (c + .5)) for t, c in df.items()}
        self.tf = [Counter(ts) for ts in self.toks]

    def scores(self, q: str) -> np.ndarray:
        qs = tokens(q)
        out = np.zeros(len(self.toks))
        for i, (tf, ts) in enumerate(zip(self.tf, self.toks)):
            L = len(ts)
            for t in qs:
                if t in tf:
                    f = tf[t]
                    out[i] += self.idf[t] * f * (self.k1 + 1) / (f + self.k1 * (1 - self.b + self.b * L / self.avg))
        return out


# ------------------------------------------------------------------ dense
_embed_state = {"model": None, "error": None}


def _embedder():
    if not DENSE:
        return None
    with _lock:
        if _embed_state["model"] is None and _embed_state["error"] is None:
            try:
                from fastembed import TextEmbedding
                _embed_state["model"] = TextEmbedding(EMBED_MODEL, cache_dir=str(EMBED_CACHE))
            except Exception as e:  # offline, no model files, etc.
                _embed_state["error"] = f"{type(e).__name__}: {str(e)[:120]}"
        return _embed_state["model"]


def dense_available() -> bool:
    return _embedder() is not None


def _norm(v):
    v = np.asarray(v, dtype=np.float32)
    return v / (np.linalg.norm(v, axis=-1, keepdims=True) + 1e-9)


@lru_cache(maxsize=4096)
def _passage_vec(text: str) -> np.ndarray | None:
    m = _embedder()
    if m is None:
        return None
    return _norm(next(iter(m.passage_embed([text]))))


def _query_vec(text: str):
    m = _embedder()
    if m is None:
        return None
    return _norm(next(iter(m.query_embed(text))))


# ------------------------------------------------------------------ index
class Index:
    def __init__(self):
        self.version = None
        self.chunks: list[dict] = []
        self.bm25 = None
        self.vecs = None

    def ensure(self):
        pre = _load_precedents()
        version = (len(pre), pre[-1]["id"] if pre else None)
        with _lock:
            if version == self.version:
                return
            chunks = []
            for f in sorted(KB_DIR.glob("*.md")):
                chunks += _chunks_from_md(f)
            chunks += pre
            self.chunks = chunks
            self.bm25 = BM25([f"{c['title']} {c.get('section') or ''} {c['text']}" for c in chunks])
            m = _embedder()
            if m is not None:
                texts = [f"{c['title']}. {c['text']}" for c in chunks]
                self.vecs = _norm(np.array(list(m.passage_embed(texts))))
            else:
                self.vecs = None
            self.version = version


INDEX = Index()


def warm():
    """Load the embedding model and build the index (called in a background thread at startup)."""
    try:
        INDEX.ensure()
    except Exception:
        pass


def search(q: str, k: int = 5, docs: tuple[str, ...] | None = None, where: dict | None = None) -> list[dict]:
    """Hybrid search. Returns chunks with bm25_rank, dense_rank and fused score."""
    INDEX.ensure()
    idx = [i for i, c in enumerate(INDEX.chunks)
           if (not docs or c["doc"] in docs) and all(c.get(a) == b for a, b in (where or {}).items())]
    if not idx:
        return []
    bm = INDEX.bm25.scores(q)[idx]
    order_bm = list(np.argsort(-bm))
    ranks = {"bm25": {idx[j]: r for r, j in enumerate(order_bm) if bm[j] > 0}}
    dense_sc = None
    if INDEX.vecs is not None:
        qv = _query_vec(q)
        dense_sc = INDEX.vecs[idx] @ qv
        ranks["dense"] = {idx[j]: r for r, j in enumerate(np.argsort(-dense_sc))}
    K = 60
    fused = {}
    for name, rk in ranks.items():
        for i, r in rk.items():
            fused[i] = fused.get(i, 0.0) + 1.0 / (K + r + 1)
    best = sorted(fused, key=lambda i: -fused[i])[:k]
    out = []
    for i in best:
        c = dict(INDEX.chunks[i])
        c["score"] = round(fused[i] * 1000, 2)
        c["bm25_rank"] = ranks["bm25"].get(i, None)
        c["bm25_rank"] = None if c["bm25_rank"] is None else c["bm25_rank"] + 1
        if "dense" in ranks:
            c["dense_rank"] = ranks["dense"][i] + 1
            c["dense_sim"] = round(float(dense_sc[idx.index(i)]), 3)
        out.append(c)
    return out


def retrieval_mode() -> str:
    return "hybrid (BM25 + dense embeddings, RRF)" if dense_available() else "BM25 only (embedding model unavailable)"


# ------------------------------------------------------------------ uses
def policy(ref: str, doc: str = "ap-policy") -> dict | None:
    INDEX.ensure()
    return next((dict(c) for c in INDEX.chunks if c["doc"] == doc and c["ref"] == ref), None)


def cite(check_id: str, detail: str = "") -> dict | None:
    ref = POLICY_REF.get(check_id)
    if check_id == "R14" and "handwriting" in detail and "stamp" not in detail:
        ref = "§2.3"   # handwritten alterations, not stamps
    c = policy(ref) if ref else None
    if not c:
        return None
    return {"ref": c["ref"], "title": c["title"], "text": c["text"], "doc": DOC_TITLE[c["doc"]]}


def similar_cases(run: dict, k: int = 3) -> list[dict]:
    """Past decisions on the same kinds of exception, most relevant first."""
    blocking = [c for c in run.get("checks", []) if c["status"] == "fail"]
    if not blocking:
        return []
    vendor = (run.get("vendor") or {}).get("name") or (run.get("fields") or {}).get("vendor_name") or ""
    seen, out = set(), []
    for c in blocking[:3]:
        q = f"{c['label']}. {c['detail']} {vendor}"
        hits = search(q, k=4, docs=("precedent",), where={"check": c["id"]})   # same kind of exception only
        for h in hits:
            if h["ref"] in seen or h.get("run_id") == run.get("id"):
                continue
            seen.add(h["ref"])
            h["for_check"] = c["id"]
            out.append(h)
    out.sort(key=lambda h: (-(h["vendor"] == vendor), -h["score"]))
    return [{k_: h.get(k_) for k_ in ("ref", "vendor", "check", "decision", "date", "facts", "note", "score", "for_check", "run_id")}
            for h in out[:k]]


# ------------------------------------------------------------------ semantic similarity for PO line matching
_UNIT = re.compile(r"(\d)\s+(gsm|ml|kg|g|l|pp|w|mm|cm|ft|gb|tb|inch|in)\b")


def _codes(s: str) -> set[str]:
    s = _UNIT.sub(r"\1\2", s.lower())
    return {t for t in re.findall(r"[a-z0-9]+", s) if re.search(r"\d", t) and len(t) >= 2}


def semantic_similarity(a: str, b: str) -> float | None:
    """Cosine similarity of two item descriptions, or None if embeddings are unavailable.
    Model numbers must agree: 'HP 88A' and 'HP 12A' are different items however close the words."""
    va, vb = _passage_vec(a), _passage_vec(b)
    if va is None or vb is None:
        return None
    sim = float(va @ vb)
    ca, cb = _codes(a), _codes(b)
    if ca and cb and not (ca & cb):
        sim = min(sim, 0.5)
    return round(sim, 3)


# ------------------------------------------------------------------ Ask Ledgerline (RAG + Claude)
ASK_SYSTEM = """You are the policy assistant on Halden Foods' accounts-payable desk. A reviewer is looking at one invoice run and asks you a question.

Answer only from the CONTEXT: the run record, the policy clauses, and past decisions. Cite every claim with its reference in square brackets, e.g. [§6.2], [§G1.2], [P-102], [run]. If the context does not answer the question, say so plainly and say who would know (Procurement, Treasury, the tax team).

You advise; you do not decide. Never tell the reviewer an invoice is approved or should be paid; say what the policy requires and what to check. Be brief: at most 6 sentences or a short list. Plain words, no preamble."""


def _run_context(run: dict) -> str:
    if not run:
        return "No run selected."
    f = run.get("fields") or {}
    r = run.get("result") or {}
    lines = [f"Run {run.get('id')}: {run.get('filename')}",
             f"Vendor: {(run.get('vendor') or {}).get('name') or f.get('vendor_name')}  Invoice: {f.get('invoice_number')}  "
             f"Total: {f.get('total')}  PO: {(run.get('po_match') or {}).get('po_id')} ({(run.get('po_match') or {}).get('method')})",
             f"Decision: {r.get('label')} - {r.get('summary')}  Queue: {r.get('queue')}"]
    v = run.get("vision") or {}
    if v:
        lines.append(f"Document: {v.get('source_label')}, legibility {v.get('grade')} ({v.get('score')}/100)")
    for c in run.get("checks", []):
        if c["status"] != "pass":
            lines.append(f"- {c['id']} {c['label']} [{c['status']}]: {c['detail']}")
    rep = (run.get("verification") or {}).get("repairs") or []
    for x in rep:
        lines.append(f"- repaired {x['field']}: {x['from']} -> {x['to']} ({x['why']})")
    if run.get("review"):
        lines.append(f"Reviewer: {run['review']['action']} - {run['review']['note']}")
    return "\n".join(lines)


def ask(question: str, run: dict | None) -> dict:
    q = question.strip()
    ctx_q = q
    if run:
        fails = " ".join(c["label"] for c in run.get("checks", []) if c["status"] == "fail")
        ctx_q = f"{q} {fails}"
    hits = search(ctx_q, k=6, docs=("ap-policy", "gst-notes"))
    cases = search(ctx_q, k=3, docs=("precedent",))
    sources = [{"ref": h["ref"], "title": h["title"], "doc": DOC_TITLE[h["doc"]], "text": h["text"], "score": h["score"],
                "bm25_rank": h.get("bm25_rank"), "dense_rank": h.get("dense_rank")} for h in hits + cases]
    context = "RUN RECORD [run]\n" + _run_context(run) + "\n\nPOLICY AND NOTES\n" + "\n".join(
        f"[{h['ref']}] {h['title']}: {h['text']}" for h in hits) + "\n\nPAST DECISIONS\n" + "\n".join(
        f"[{h['ref']}] {h['title']} ({h.get('date')}): {h['text']}" for h in cases)

    from .extract import MODEL, claude_available
    if not claude_available():
        return {"answer": None, "mode": "retrieval-only", "sources": sources, "retrieval": retrieval_mode(),
                "note": "Claude is not connected, so here are the most relevant passages instead of a written answer."}
    import anthropic
    client = anthropic.Anthropic(max_retries=2, timeout=60)
    params = dict(model=MODEL, max_tokens=4000, system=ASK_SYSTEM, output_config={"effort": "low"},
                  messages=[{"role": "user", "content": f"CONTEXT\n{context}\n\nQUESTION\n{q}"}])
    try:
        try:
            resp = client.beta.messages.create(betas=["server-side-fallback-2026-07-01"], fallbacks="default", **params)
        except anthropic.BadRequestError:
            resp = client.messages.create(**params)
        if resp.stop_reason == "refusal":
            raise RuntimeError("Claude declined this question")
        text = "".join(b.text for b in resp.content if b.type == "text").strip()
        return {"answer": text, "mode": "claude", "model": getattr(resp, "model", MODEL), "sources": sources, "retrieval": retrieval_mode()}
    except Exception as e:
        return {"answer": None, "mode": "retrieval-only", "sources": sources, "retrieval": retrieval_mode(),
                "note": f"Claude call failed ({type(e).__name__}); showing the retrieved passages."}
