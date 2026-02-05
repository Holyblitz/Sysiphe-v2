#!/usr/bin/env python3
import os, re, csv, time, random
import requests
from bs4 import BeautifulSoup
import psycopg2

DB_DSN = os.environ.get("PG_DSN", "dbname=commercial_ai user=romain")
INPUT_CSV = os.environ.get("INPUT_CSV", "/tmp/sysiphe_sites_existing_80.csv")

BATCH = int(os.environ.get("EMAIL_BATCH", "80"))
SLEEP_MIN = float(os.environ.get("SLEEP_MIN", "0.8"))
SLEEP_MAX = float(os.environ.get("SLEEP_MAX", "2.0"))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "15"))

USER_AGENT = os.environ.get(
    "SYSIPHE_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
BAD_EMAIL_HINTS = ("example.com", "yourcompany.com", "email.com")

SQL_INSERT_CONTACT = """
INSERT INTO targets_contacts (target_id, email, status, found_method, found_url)
VALUES (%s, %s, 'found', %s, %s)
ON CONFLICT (email) DO NOTHING;
"""

def sleep_a_bit():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))

def fetch_url(url: str) -> str:
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT, allow_redirects=True)
    r.raise_for_status()
    return r.text

def extract_emails(html: str):
    emails = set(e.lower() for e in EMAIL_RE.findall(html))
    clean = []
    for e in emails:
        if any(bad in e for bad in BAD_EMAIL_HINTS):
            continue
        clean.append(e)
    return sorted(clean)

def pick_best_email(emails):
    if not emails:
        return ""
    preferred_prefixes = ("info@", "contact@", "hello@", "sales@", "support@", "admin@", "enquiries@", "enquiry@", "office@")
    for p in preferred_prefixes:
        for e in emails:
            if e.startswith(p):
                return e
    return emails[0]

def main():
    rows = []
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append((r["target_id"], r["website_url"]))

    rows = rows[:BATCH]

    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True

    ok_email = 0
    fail = 0

    for target_id, site_url in rows:
        emails_found = set()
        pages = (
            site_url,
            site_url + "/contact",
            site_url + "/contact-us",
            site_url + "/about",
            site_url + "/about-us",
            site_url + "/support",
            site_url + "/help",
        )
        for u in pages:
            try:
                html = fetch_url(u)
                for e in extract_emails(html):
                    emails_found.add(e)
            except Exception:
                pass

        best = pick_best_email(sorted(emails_found))
        if best:
            with conn.cursor() as cur:
                cur.execute(SQL_INSERT_CONTACT, (target_id, best, "site_scrape", site_url))
            ok_email += 1
        else:
            fail += 1

        sleep_a_bit()

    conn.close()
    print(f"[done] batch={len(rows)} ok_email={ok_email} fail={fail}")

if __name__ == "__main__":
    main()
