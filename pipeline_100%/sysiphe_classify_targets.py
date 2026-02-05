import os, json, requests, psycopg2
from psycopg2.extras import RealDictCursor

DB_DSN = os.environ.get("PG_DSN", "dbname=commercial_ai user=romain")
MODEL = os.environ.get("OLLAMA_MODEL", "mistral:latest")
BATCH = int(os.environ.get("CLASSIFY_BATCH", "80"))

SYSTEM = """You are classifying Australian companies for B2B outreach.
Return STRICT JSON with keys: company_type, score, rationale.
company_type must be one of: b2b, b2c, agency, saas, recruitment, consulting, training, solo, corp, unknown.
score: 1 (best target) to 5 (worst).
rationale: short reason.
"""

def ollama_json(prompt: str) -> dict:
    r = requests.post(
        "http://localhost:11434/api/generate",
        json={"model": MODEL, "prompt": prompt, "system": SYSTEM, "stream": False},
        timeout=60,
    )
    r.raise_for_status()
    text = r.json()["response"].strip()
    # extract json robustly
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("No JSON in response: " + text[:200])
    return json.loads(text[start:end+1])

def main():
    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT target_id, company_name, website_url, source_name
            FROM targets_typed
            WHERE company_type='unknown'
            ORDER BY created_at DESC
            LIMIT %s
        """, (BATCH,))
        rows = cur.fetchall()

    for row in rows:
        name = row["company_name"] or ""
        url = row["website_url"] or ""
        src = row["source_name"]
        prompt = f"Company name: {name}\nWebsite: {url}\nSource: {src}\nClassify this company."
        try:
            out = ollama_json(prompt)
            ctype = out.get("company_type", "unknown")
            score = int(out.get("score", 3))
            rationale = (out.get("rationale") or "")[:240]
        except Exception as e:
            ctype, score, rationale = "unknown", 5, f"classify_error: {e}"

        with conn.cursor() as cur:
            cur.execute("""
                UPDATE targets_typed
                SET company_type=%s, score=%s, rationale=%s, updated_at=now()
                WHERE target_id=%s
            """, (ctype, score, rationale, row["target_id"]))
        print(row["target_id"], ctype, score)

    conn.close()

if __name__ == "__main__":
    main()
