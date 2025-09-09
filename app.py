# app.py — Offline Campus Helpdesk Agent (No-LLM)
# -----------------------------------------------
# Uses only local static data + simple matching. No API keys, no internet.

import json, re, pathlib
from typing import Dict, Any, List, Tuple, Optional
from rapidfuzz import fuzz

# =========================
# Paths & data loading
# =========================
ROOT = pathlib.Path(__file__).parent
STATIC = ROOT / "static"
POLICIES_DIR = STATIC / "policies"
TEMPLATES_DIR = STATIC / "templates"   # exported for ui.py

def _load_json(p: pathlib.Path):
    with open(p, "r", encoding="utf-8") as f:
        return json.load(f)

FAQ = _load_json(STATIC / "faq.json")
GLOSSARY = _load_json(STATIC / "glossary.json")
WORKFLOWS = _load_json(STATIC / "workflows.json")

FAQ_QS = [x["q"] for x in FAQ]

# =========================
# Helpers
# =========================
def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())

def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9\-]", "", normalize(s).replace(" ", "-"))

def expand_query_with_glossary(q: str) -> List[str]:
    """Return query variants enriched with glossary synonyms (if present)."""
    words = re.findall(r"[a-zA-Z\-]+", normalize(q))
    variants = set([normalize(q)])
    for _, vals in GLOSSARY.items():
        if any(w in vals for w in words):
            variants.update(vals)
    return list(variants)

# ---------- Policy markdown parsing ----------
def parse_policy_markdown(content: str):
    """
    Extracts the H1 title and a list of sections as [{heading, body}, ...]
    Sections are parsed from '## ' headings.
    """
    # Title (H1)
    title_match = re.search(r"^#\s+(.*)$", content, flags=re.M)
    title = title_match.group(1).strip() if title_match else "Policy"

    # Split into H2 sections (keep blocks that begin with '## ')
    sections: List[Dict[str, str]] = []
    blocks = re.split(r"\n(?=##\s+)", content.strip())

    for block in blocks:
        # Remove a leading H1 line if present in this block
        block = re.sub(r"^#\s+.*$", "", block, flags=re.M).strip()
        if not block:
            continue

        m = re.match(r"^##\s+(.*)\n(.*)$", block, flags=re.S | re.M)
        if m:
            heading = m.group(1).strip()
            body = m.group(2).strip()
            sections.append({"heading": heading, "body": body})

    return title, sections

# =========================
# Tool 1: FAQLookup
# =========================
def faq_lookup(query: str) -> Dict[str, Any]:
    """
    Search static FAQs for best match. Returns:
    {found, score, matched_question, answer, id}
    """
    variants = expand_query_with_glossary(query)
    best_score, best_idx = -1, -1

    for i, fq in enumerate(FAQ_QS):
        sc = max((fuzz.token_set_ratio(v, fq) for v in variants),
                 default=fuzz.token_set_ratio(query, fq))
        if sc > best_score:
            best_score, best_idx = sc, i

    if best_idx == -1:
        return {"found": False, "score": 0}

    item = FAQ[best_idx]
    return {
        "found": True,
        "score": round(best_score / 100.0, 4),
        "matched_question": item["q"],
        "answer": item["a"],
        "id": item.get("id", "")
    }

# =========================
# Tool 2: PolicyFetch
# =========================
def _read_policy_file(topic_slug: str) -> Optional[str]:
    p = (POLICIES_DIR / f"{topic_slug}.md")
    if p.exists():
        return p.read_text(encoding="utf-8")
    return None

def policy_fetch(topic_or_section: str) -> Dict[str, Any]:
    """
    Input examples:
      'attendance'
      'attendance#medical leave'

    Returns either:
      - {found, topic, title, section, body}  # specific section match
      - {found, topic, title, sections:[{heading, body}, ...]}  # whole policy
    """
    s = topic_or_section.strip()
    if "#" in s:
        topic, section = s.split("#", 1)
    else:
        topic, section = s, ""

    topic_slug = slugify(topic)
    content = _read_policy_file(topic_slug)
    if not content:
        return {"found": False, "topic": topic_slug}

    title, sections = parse_policy_markdown(content)

    if section:
        target = section.strip().lower()
        for sec in sections:
            h = sec["heading"].strip().lower()
            if target in h or h in target:
                return {
                    "found": True,
                    "topic": topic_slug,
                    "title": title,
                    "section": sec["heading"],
                    "body": sec["body"]
                }
        # If section not found, fall back to whole policy

    return {
        "found": True,
        "topic": topic_slug,
        "title": title,
        "sections": sections
    }

# =========================
# Tool 3: WorkflowGuide
# =========================
def workflow_guide(name_or_id: str) -> Dict[str, Any]:
    query = normalize(name_or_id)
    best, idx = -1, -1
    for i, w in enumerate(WORKFLOWS):
        name_sc = fuzz.token_set_ratio(query, normalize(w["name"]))
        id_sc = fuzz.token_set_ratio(query, normalize(w["id"]))
        sc = name_sc if name_sc > id_sc else id_sc
        if sc > best:
            best, idx = sc, i
    if idx == -1:
        return {"found": False}

    w = WORKFLOWS[idx]
    return {
        "found": True,
        "id": w["id"],
        "name": w["name"],
        "steps": w["steps"],
        "required_docs": w["required_docs"],
        "template_id": w.get("template_id", None),
        "score": round(best / 100.0, 4)
    }

# =========================
# Offline router (no LLM)
# =========================
def naive_router(question: str) -> Tuple[str, list]:
    """
    Simple intent router:
      - If looks like a policy → PolicyFetch (formatted markdown, ALL sections).
      - Else try FAQLookup (threshold).
      - Else if looks like a workflow → WorkflowGuide.
      - Else fallback refusal.
    """
    qn = normalize(question)
    steps = []

    # 0) Policy-first if user hints at policy/rules
    policy_triggers = ["policy", "rule", "attendance", "plagiarism", "leave", "discipline"]
    if any(k in qn for k in policy_triggers):
        topic = "attendance" if "attend" in qn else ("plagiarism" if "plag" in qn else "attendance")
        pol = policy_fetch(topic)
        steps.append(("PolicyFetch", pol))
        if pol.get("found"):
            if "body" in pol:
                # specific section match
                answer_md = f"# {pol['title']}\n\n## {pol['section']}\n\n{pol['body']}"
                return answer_md, steps
            else:
                # full policy: show title + ALL sections
                sections = pol.get("sections", [])
                chunks = [f"# {pol['title']}"]
                for sec in sections:
                    chunks.append(f"## {sec['heading']}\n\n{sec['body']}")
                return "\n\n".join(chunks), steps

    # 1) FAQ
    faq = faq_lookup(question)
    steps.append(("FAQLookup", faq))
    if faq.get("found") and faq.get("score", 0) >= 0.72:
        return faq["answer"], steps

    # 2) Workflow
    if any(k in qn for k in ["how to", "how do i", "steps", "apply", "process", "re-eval", "reeval", "re-evaluation", "transcript"]):
        wf_key = "reval" if ("re" in qn and "val" in qn) else "transcript"
        wf = workflow_guide(wf_key)
        steps.append(("WorkflowGuide", wf))
        if wf.get("found"):
            lines = [f"**{wf['name']}**"]
            for i, s in enumerate(wf["steps"], 1):
                lines.append(f"{i}. {s}")
            if wf.get("required_docs"):
                lines.append(f"Required docs: {', '.join(wf['required_docs'])}")
            return "\n".join(lines), steps

    # 3) Fallback
    return "I don't have this in my static knowledge.", steps

# =========================
# Public API
# =========================
def ask(question: str) -> Tuple[str, List[Dict[str, Any]]]:
    answer, steps = naive_router(question)
    steps.append(("Notice", {"info": "No-LLM mode (offline)."}))
    return answer, steps

# CLI quick test:  python app.py "attendance policy"
if __name__ == "__main__":
    import sys
    q = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "How to get transcript?"
    ans, steps = ask(q)
    print("\n=== Answer ===\n", ans)
    print("\n=== Steps ===")
    for s in steps:
        print(s)

# Export for ui.py
__all__ = ["ask", "TEMPLATES_DIR", "policy_fetch"]
