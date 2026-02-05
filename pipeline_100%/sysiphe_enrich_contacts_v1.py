#!/usr/bin/env python3
import os
import re
import time
import random
from urllib.parse import urlparse, quote_plus

import requests
from bs4 import BeautifulSoup

import psycopg2
from psycopg2.extras import RealDictCursor

# -------------------
# CONFIG
# -------------------
DB_HOST = os.getenv("PGHOST", "127.0.0.1")
DB_PORT = int(os.getenv("PGPORT", "5432"))
DB_NAME = os.getenv("PGDATABASE", "commercial_ai")
DB_USER = os.getenv("PGUSER", "romain")
DB_PASS = os.getenv("PGPASSWORD")

BATCH = int(os.getenv("ENRICH_BATCH", "30"))          # combien on tente
SLEEP_MIN = float(os.getenv("ENRICH_SLEEP_MIN", "2.0"))
SLEEP_MAX = float(os.getenv("ENRICH_SLEEP_MAX", "5.0"))
HTTP_TIMEOUT = int(os.getenv("HTTP_TIMEOUT", "15"))

USER_AGENT = os.getenv(
    "SYSIPHE_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
BAD_EMAIL_HINTS = ("example.com", "yourcompany.com", "email.com")

# -------------------
SQL_FETCH = """
SELECT
  oq.outreach_id,
  c.canonical_name AS company_name,
  (cr.extra->>'abn') AS abn,
  (cr.extra->>'state') AS state
FROM outreach_queue oq
JOIN companies c ON c.company_id = oq.company_id
JOIN companies_seed cs ON cs.company_id = oq.company_id
JOIN companies_raw cr ON cr.raw_id = cs.raw_id
WHERE oq.status='draft_ready'
  AND oq.contact_email IS NULL
ORDER BY oq.updated_at
LIMIT %s;
"""

SQL_UPDATE_OK = """
UPDATE outreach_queue
SET contact_email = %s,
    updated_at = now(),
    notes = COALESCE(notes,'') || %s
WHERE outreach_id = %s;
"""

SQL_UPDATE_FAIL = """
UPDATE outreach_queue
SET updated_at = now(),
    notes = COALESCE(notes,'') || %s
WHERE outreach_id = %s;
"""

# -------------------
def sleep_a_bit():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

def norm_domain(url: str) -> str:
    try:
        p = urlparse(url)
        host = (p.netloc or "").lower()
        host = host.split("@")[-1].split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""

def ddg_search_first_domain(query: str) -> str:
    """
    DuckDuckGo HTML endpoint (simple). Returns a domain or ''.
    """
    q = quote_plus(query)
    url = f"https://duckduckgo.com/html/?q={q}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")

    # Results links
    for a in soup.select("a.result__a"):
        href = a.get("href") or ""
        dom = norm_domain(href)
        if dom and "duckduckgo.com" not in dom:
            return dom
    return ""

def fetch_url(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.text

def extract_emails_from_html(html: str) -> list[str]:
    emails = set(e.lower() for e in EMAIL_RE.findall(html))
    clean = []
    for e in emails:
        if any(bad in e for bad in BAD_EMAIL_HINTS):
            continue
        clean.append(e)
    return sorted(clean)

def pick_best_email(emails: list[str]) -> str:
    """
    Prefer generic inboxes.
    """
    if not emails:
        return ""
    priority = ("info@", "contact@", "hello@", "support@", "admin@")
    for pref in priority:
        for e in emails:
            if e.startswith(pref):
                return e
    return emails[0]

def try_extract_email_from_site(domain: str) -> tuple[str, str]:
    """
    Returns (email, reason). Might return ('','no_email').
    """
    pages = [
        f"https://{domain}/",
        f"https://{domain}/contact",
        f"https://{domain}/contact-us",
        f"https://{domain}/about",
        f"https://{domain}/privacy",
    ]

    all_emails = set()
    for u in pages:
        try:
            html = fetch_url(u)
            for e in extract_emails_from_html(html):
                all_emails.add(e)
            sleep_a_bit()
        except Exception:
            continue

    best = pick_best_email(sorted(all_emails))
    if best:
        return best, "email_found_on_site"
    return "", "no_email_on_site"

def main():
    if not DB_PASS:
        print("❌ PGPASSWORD manquant (export PGPASSWORD='...').")
        return

    conn = psycopg2.connect(
        host=DB_HOST, port=DB_PORT, dbname=DB_NAME, user=DB_USER, password=DB_PASS
    )
    conn.autocommit = False

    with conn, conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(SQL_FETCH, (BATCH,))
        rows = cur.fetchall()

    if not rows:
        print("No rows to enrich (draft_ready with contact_email NULL).")
        conn.close()
        return

    print(f"[+] Enriching {len(rows)} rows...")

    ok = 0
    fail = 0

    for r in rows:
        oid = r["outreach_id"]
        name = (r["company_name"] or "").strip()
        abn = (r["abn"] or "").strip()
        state = (r["state"] or "").strip()

        # Website discovery queries (simple + robust)
        queries = []
        if abn:
            queries.append(f"{abn} {name} website")
        queries.append(f"{name} Australia website")
        if state:
            queries.append(f"{name} {state} Australia")

        domain = ""
        for q in queries:
            try:
                domain = ddg_search_first_domain(q)
                if domain:
                    break
            except Exception:
                pass
            sleep_a_bit()

        if not domain:
            note = f"\nSysiphe_enrich=no_domain_found at={time.strftime('%Y-%m-%dT%H:%M:%S')}"
            with conn, conn.cursor() as cur2:
                cur2.execute(SQL_UPDATE_FAIL, (note, oid))
            fail += 1
            print(f"[-] {name}: no domain found")
            continue

        email, reason = try_extract_email_from_site(domain)
        if email:
            note = (
                f"\nSysiphe_enrich=ok domain={domain} reason={reason} "
                f"at={time.strftime('%Y-%m-%dT%H:%M:%S')}"
            )
            with conn, conn.cursor() as cur2:
                cur2.execute(SQL_UPDATE_OK, (email, note, oid))
            ok += 1
            print(f"[✓] {name}: {email} (domain={domain})")
        else:
            note = (
                f"\nSysiphe_enrich=no_email domain={domain} reason={reason} "
                f"at={time.strftime('%Y-%m-%dT%H:%M:%S')}"
            )
            with conn, conn.cursor() as cur2:
                cur2.execute(SQL_UPDATE_FAIL, (note, oid))
            fail += 1
            print(f"[-] {name}: no email (domain={domain})")

        sleep_a_bit()

    conn.close()
    print(f"[✓] Done. ok={ok} fail={fail}")

if __name__ == "__main__":
    main()

