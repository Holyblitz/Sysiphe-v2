#!/usr/bin/env python3
import os, re, csv, time, random
from urllib.parse import urlparse, quote_plus

import requests
from bs4 import BeautifulSoup
import psycopg2


# -------------------
# CONFIG
# -------------------
DB_DSN = os.environ.get("PG_DSN", "dbname=commercial_ai user=romain")
INPUT_CSV = os.environ.get("INPUT_CSV", "/tmp/sysiphe_sitefind_day2_200.csv")

BATCH = int(os.environ.get("SITEFIND_BATCH", "60"))      # combien de targets traiter par run
SLEEP_MIN = float(os.environ.get("SLEEP_MIN", "1.0"))
SLEEP_MAX = float(os.environ.get("SLEEP_MAX", "2.5"))
HTTP_TIMEOUT = int(os.environ.get("HTTP_TIMEOUT", "15"))

USER_AGENT = os.environ.get(
    "SYSIPHE_UA",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0 Safari/537.36"
)

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}\b")
BAD_EMAIL_HINTS = ("example.com", "yourcompany.com", "email.com")

GOOD_DOMAIN_HINTS = ("com.au", "net.au", "org.au", "edu.au", "gov.au", "au")


def sleep_a_bit():
    time.sleep(random.uniform(SLEEP_MIN, SLEEP_MAX))


def norm_domain(url: str) -> str:
    try:
        p = urlparse(url if "://" in url else "https://" + url)
        host = (p.netloc or "").lower()
        host = host.split("@")[-1].split(":")[0]
        if host.startswith("www."):
            host = host[4:]
        return host
    except Exception:
        return ""


def is_plausible_domain(dom: str) -> bool:
    if not dom:
        return False
    # évite les plateformes/annuaires qui polluent
    bad_hosts = (
        "google.", "duckduckgo.", "facebook.", "linkedin.", "instagram.",
        "youtube.", "yelp.", "yellowpages.", "hotfrog.", "truelocal.",
        "aussieweb.", "dnb.", "zoominfo.", "clutch.co"
    )
    if any(b in dom for b in bad_hosts):
        return False
    return any(dom.endswith(suf) for suf in GOOD_DOMAIN_HINTS) or "." in dom


def google_search_first_domain(query: str) -> str:
    """
    Recherche Google HTML légère (peut être bloquée par consent/captcha).
    Si bloqué -> retourne "" pour fallback.
    """
    q = quote_plus(query)
    url = f"https://www.google.com/search?hl=en&q={q}"
    r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
    txt = r.text.lower()

    # consent / captcha / unusual traffic -> bail out
    if "consent.google" in txt or "unusual traffic" in txt or "our systems have detected" in txt:
        return ""

    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.select("a"):
        href = a.get("href") or ""
        if href.startswith("/url?q="):
            real = href.split("/url?q=")[1].split("&")[0]
            dom = norm_domain(real)
            if is_plausible_domain(dom):
                return dom
    return ""


def ddg_search_first_domain(query: str) -> str:
    q = quote_plus(query)
    url = f"https://duckduckgo.com/html/?q={q}"
    try:
        r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT)
        # Si DDG renvoie 403/429, on stoppe proprement
        if r.status_code in (403, 429):
            return ""
        r.raise_for_status()
    except Exception:
        return ""

    soup = BeautifulSoup(r.text, "html.parser")
    for a in soup.select("a.result__a"):
        href = a.get("href") or ""
        dom = norm_domain(href)
        if is_plausible_domain(dom):
            return dom
    return ""

def fetch_url(url: str) -> str:
    r = requests.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=HTTP_TIMEOUT,
        allow_redirects=True
    )
    r.raise_for_status()
    return r.text


def extract_emails(html: str) -> list[str]:
    emails = set(e.lower() for e in EMAIL_RE.findall(html))
    clean = []
    for e in emails:
        if any(bad in e for bad in BAD_EMAIL_HINTS):
            continue
        clean.append(e)
    return sorted(clean)


def pick_best_email(emails: list[str]) -> str:
    if not emails:
        return ""
    preferred_prefixes = (
        "info@", "contact@", "hello@", "sales@", "support@", "admin@",
        "enquiries@", "enquiry@", "office@", "team@"
    )
    for p in preferred_prefixes:
        for e in emails:
            if e.startswith(p):
                return e
    return emails[0]


SQL_UPDATE_SITE = """
UPDATE targets_typed
SET website_domain = %s,
    website_url = %s,
    updated_at = now(),
    rationale = COALESCE(rationale,'') || %s
WHERE target_id = %s;
"""

SQL_INSERT_CONTACT = """
INSERT INTO targets_contacts (target_id, email, status, found_method, found_url)
VALUES (%s, %s, 'found', %s, %s)
ON CONFLICT (email) DO NOTHING;
"""


def main():
    # Load CSV targets
    rows = []
    with open(INPUT_CSV, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append((r["target_id"], r["company_name"]))

    rows = rows[:BATCH]

    conn = psycopg2.connect(DB_DSN)
    conn.autocommit = True

    ok_site = 0
    ok_email = 0
    fail = 0

    for target_id, name in rows:
        query = f"{name} Australia official website"

        dom = google_search_first_domain(query)
        method = "google"
        if not dom:
            dom = ddg_search_first_domain(query)
            method = "ddg_fallback"

        if not dom:
            with conn.cursor() as cur:
                # IMPORTANT: NULLs avoid UNIQUE conflict on (country_code, website_domain)
                cur.execute(SQL_UPDATE_SITE, (None, None, f"\n[site] no_domain_found ({method})", target_id))
            fail += 1
            sleep_a_bit()
            continue

        site_url = "https://" + dom

        # update site in targets_typed
        with conn.cursor() as cur:
            cur.execute(SQL_UPDATE_SITE, (dom, site_url, f"\n[site] {method} -> {dom}", target_id))
        ok_site += 1

        emails_found = set()
        candidate_pages = (
            site_url,
            site_url + "/contact",
            site_url + "/contact-us",
            site_url + "/about",
            site_url + "/about-us",
            site_url + "/support",
        )

        for u in candidate_pages:
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
            with conn.cursor() as cur:
                cur.execute(SQL_UPDATE_SITE, (dom, site_url, f"\n[email] no_email_found_on_site", target_id))

        sleep_a_bit()

    conn.close()
    print(f"[done] batch={len(rows)} ok_site={ok_site} ok_email={ok_email} fail={fail}")


if __name__ == "__main__":
    main()
