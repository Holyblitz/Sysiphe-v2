#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse, csv, re, time, random
from urllib.parse import urlparse
import requests
from bs4 import BeautifulSoup

EMAIL_RE = re.compile(r"\b[a-zA-Z0-9._%+-]+@([a-zA-Z0-9-]+\.)+[a-zA-Z]{2,}\b")
BAD_EMAIL_HINTS = ("example.com", "yourcompany.com", "email.com", "test.com", "domain.com")
CONTACT_PATHS = ("/contact", "/contact-us", "/about", "/about-us", "/support", "/enquiries")

UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"

def norm_domain(d: str) -> str:
    d = (d or "").strip().lower()
    d = d.replace("https://", "").replace("http://", "")
    d = d.split("/")[0]
    if d.startswith("www."):
        d = d[4:]
    return d

def extract_emails(text: str):
    emails = set(m.group(0).lower() for m in EMAIL_RE.finditer(text or ""))
    out = []
    for e in emails:
        if any(bad in e for bad in BAD_EMAIL_HINTS):
            continue
        out.append(e)
    return sorted(out)

def pick_best_email(emails):
    if not emails:
        return None
    preferred = ("contact@", "info@", "hello@", "enquiries@", "enquiry@", "sales@", "support@", "admin@")
    for p in preferred:
        for e in emails:
            if e.startswith(p):
                return e
    return emails[0]

def fetch(url, timeout):
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=timeout, allow_redirects=True)
        if r.status_code != 200:
            return None
        return r.text
    except Exception:
        return None

def verify_email_on_site(domain: str, timeout: int):
    base = "https://" + domain
    for path in ("",) + CONTACT_PATHS:
        url = base if path == "" else base.rstrip("/") + path
        html = fetch(url, timeout)
        if not html:
            continue
        emails = extract_emails(html)
        best = pick_best_email(emails)
        if best:
            return best, url
    return None, None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--timeout", type=int, default=15)
    ap.add_argument("--sleep-min", type=float, default=1.0)
    ap.add_argument("--sleep-max", type=float, default=2.2)
    args = ap.parse_args()

    rows = []
    with open(args.input, newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        for row in r:
            rows.append(row)
            if len(rows) >= args.limit:
                break

    found = 0
    with open(args.output, "w", newline="", encoding="utf-8") as out_f:
        w = csv.DictWriter(out_f, fieldnames=["abn", "legal_name", "domain", "email", "found_url", "method"])
        w.writeheader()

        for row in rows:
            abn = (row.get("abn") or "").strip()
            name = (row.get("legal_name") or "").strip()
            dom = norm_domain(row.get("guessed_domain") or "")
            if not dom:
                continue

            email, url = verify_email_on_site(dom, args.timeout)
            if email:
                w.writerow({
                    "abn": abn,
                    "legal_name": name,
                    "domain": dom,
                    "email": email,
                    "found_url": url,
                    "method": "verify_on_site",
                })
                found += 1

            time.sleep(random.uniform(args.sleep_min, args.sleep_max))

    print(f"[done] tested={len(rows)} verified_emails={found} output={args.output}")

if __name__ == "__main__":
    main()

