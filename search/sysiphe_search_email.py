#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import argparse
import csv
import os
import re
import sys
import time
from dataclasses import dataclass
from typing import List, Optional, Set, Tuple

import requests


EMAIL_RE = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.IGNORECASE)

# Emails "génériques" qu'on préfère (ordre)
LOCALPART_PRIORITY = [
    "contact",
    "hello",
    "info",
    "support",
    "sales",
    "admin",
    "enquiries",
    "enquiry",
    "privacy",
]


@dataclass
class FoundEmail:
    email: str
    source_url: str
    confidence: int
    reason: str


def normalize_email(email: str) -> str:
    return email.strip().lower()


def domain_from_email(email: str) -> str:
    parts = email.split("@", 1)
    return parts[1].lower().strip() if len(parts) == 2 else ""


def score_email(email: str, expected_domain: Optional[str]) -> Tuple[int, str]:
    """
    Score simple et robuste.
    - On privilégie un email du même domaine que le site attendu
    - On privilégie localparts "contact/info/hello"
    - On pénalise les free emails (gmail/outlook/yahoo)
    """
    e = normalize_email(email)
    dom = domain_from_email(e)

    free = {
        "gmail.com", "googlemail.com", "outlook.com", "hotmail.com", "live.com",
        "yahoo.com", "yahoo.com.au", "icloud.com", "aol.com", "proton.me", "protonmail.com"
    }

    localpart = e.split("@", 1)[0]
    score = 50
    reason = []

    if dom in free:
        score -= 35
        reason.append("free_provider")

    if expected_domain:
        exp = expected_domain.lower().strip()
        exp = exp.replace("http://", "").replace("https://", "").split("/", 1)[0]
        exp = exp.lstrip("www.")
        if dom == exp:
            score += 35
            reason.append("domain_match")
        elif dom.endswith("." + exp):
            score += 20
            reason.append("subdomain_match")
        else:
            score -= 10
            reason.append("domain_mismatch")

    # localpart preference
    for i, lp in enumerate(LOCALPART_PRIORITY):
        if localpart == lp:
            bonus = max(0, 25 - i * 3)
            score += bonus
            reason.append(f"lp={lp}")
            break

    score = max(0, min(100, score))
    return score, ",".join(reason) if reason else "generic"


def pick_best_email(emails: Set[str], expected_domain: Optional[str], source_url: str) -> Optional[FoundEmail]:
    best = None
    for em in emails:
        s, why = score_email(em, expected_domain)
        if (best is None) or (s > best.confidence):
            best = FoundEmail(email=normalize_email(em), source_url=source_url, confidence=s, reason=why)
    return best


def search_serpapi(query: str, api_key: str, timeout: int = 30, num: int = 5) -> List[str]:
    params = {
        "q": query,
        "engine": "google",
        "api_key": api_key,
        "num": num,
    }
    r = requests.get("https://serpapi.com/search.json", params=params, timeout=timeout)
    r.raise_for_status()
    data = r.json()

    urls = []
    for item in data.get("organic_results", [])[:num]:
        link = item.get("link")
        if link:
            urls.append(link)
    return urls


def fetch_page(url: str, timeout: int, ua: str) -> Optional[str]:
    try:
        r = requests.get(url, headers={"User-Agent": ua}, timeout=timeout, allow_redirects=True)
        if r.status_code >= 400:
            return None
        # évite de parser des pdf, images, etc.
        ctype = (r.headers.get("content-type") or "").lower()
        if "text/html" not in ctype and "text/plain" not in ctype:
            return None
        return r.text
    except Exception:
        return None


def extract_emails_from_text(text: str) -> Set[str]:
    return set(EMAIL_RE.findall(text or ""))


def build_query(legal_name: str, state: str = "", postcode: str = "") -> str:
    # Query pragmatique. Tu peux ajuster.
    name = (legal_name or "").strip()
    extra = " ".join([x.strip() for x in [state, postcode] if x and x.strip()])
    if extra:
        return f'"{name}" {extra} contact email'
    return f'"{name}" contact email'


def read_input_rows(path: str, limit: int) -> List[dict]:
    rows = []
    with open(path, "r", encoding="utf-8", errors="replace", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            rows.append(row)
            if limit and len(rows) >= limit:
                break
    return rows


def write_output(path: str, results: List[dict]) -> None:
    fieldnames = [
        "abn",
        "legal_name",
        "email",
        "confidence",
        "source_url",
        "reason",
        "query",
    ]
    with open(path, "w", encoding="utf-8", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for r in results:
            w.writerow(r)


def main():
    ap = argparse.ArgumentParser(description="Search emails via SerpApi and extract from pages.")
    ap.add_argument("--input", required=True, help="Input CSV (ABN bulk extract, etc.)")
    ap.add_argument("--output", required=True, help="Output CSV")
    ap.add_argument("--limit", type=int, default=100, help="Max rows to test")
    ap.add_argument("--provider", default="serpapi", choices=["serpapi"], help="Search provider")
    ap.add_argument("--timeout", type=int, default=20, help="HTTP timeout seconds")
    ap.add_argument("--sleep", type=float, default=1.2, help="Sleep between searches (seconds)")
    ap.add_argument("--links", type=int, default=2, help="Max result links to fetch per company")
    ap.add_argument("--user-agent", default="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121 Safari/537.36")
    ap.add_argument("--expected-domain-field", default="", help="Optional CSV field containing a known domain/website")
    args = ap.parse_args()

    api_key = os.getenv("SERPAPI_API_KEY", "").strip()
    if not api_key:
        print("ERROR: SERPAPI_API_KEY is not set. Do: export SERPAPI_API_KEY='...'", file=sys.stderr)
        sys.exit(2)

    rows = read_input_rows(args.input, args.limit)

    results = []
    tested = 0
    found = 0

    for row in rows:
        tested += 1

        abn = (row.get("abn") or "").strip()
        legal_name = (row.get("legal_name") or row.get("business_name") or "").strip()
        state = (row.get("main_state") or "").strip()
        postcode = (row.get("main_postcode") or "").strip()

        expected_domain = None
        if args.expected_domain_field:
            expected_domain = (row.get(args.expected_domain_field) or "").strip() or None

        if not legal_name:
            # pas exploitable
            results.append({
                "abn": abn,
                "legal_name": "",
                "email": "",
                "confidence": 0,
                "source_url": "",
                "reason": "missing_legal_name",
                "query": "",
            })
            continue

        query = build_query(legal_name, state, postcode)

        # 1) search
        try:
            urls = search_serpapi(query, api_key=api_key, timeout=args.timeout, num=max(5, args.links))
        except Exception as e:
            results.append({
                "abn": abn,
                "legal_name": legal_name,
                "email": "",
                "confidence": 0,
                "source_url": "",
                "reason": f"search_error:{type(e).__name__}",
                "query": query,
            })
            time.sleep(args.sleep)
            continue

        # 2) fetch a couple pages + extract
        best: Optional[FoundEmail] = None
        fetched = 0

        for url in urls:
            if fetched >= args.links:
                break
            fetched += 1

            html = fetch_page(url, timeout=args.timeout, ua=args.user_agent)
            if not html:
                continue

            emails = extract_emails_from_text(html)
            if not emails:
                continue

            candidate = pick_best_email(emails, expected_domain=expected_domain, source_url=url)
            if candidate and (best is None or candidate.confidence > best.confidence):
                best = candidate

            # si c'est très bon, on stop
            if best and best.confidence >= 85:
                break

        if best:
            found += 1
            results.append({
                "abn": abn,
                "legal_name": legal_name,
                "email": best.email,
                "confidence": best.confidence,
                "source_url": best.source_url,
                "reason": best.reason,
                "query": query,
            })
        else:
            results.append({
                "abn": abn,
                "legal_name": legal_name,
                "email": "",
                "confidence": 0,
                "source_url": "",
                "reason": "not_found",
                "query": query,
            })

        time.sleep(args.sleep)

    write_output(args.output, results)
    print(f"[done] tested={tested} found={found} output={args.output}")


if __name__ == "__main__":
    main()



