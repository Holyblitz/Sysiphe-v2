#!/usr/bin/env python3
import csv
import re
import socket
import dns.resolver
import argparse
from pathlib import Path

# --------------------
# CONFIG
# --------------------
LEGAL_SUFFIXES = [
    r"\bPTY\b", r"\bPTY LTD\b", r"\bLIMITED\b", r"\bLTD\b",
    r"\bPROPRIETARY\b", r"\bPROPRIETARY LIMITED\b",
    r"\bGROUP\b", r"\bHOLDINGS?\b", r"\bAUSTRALIA\b"
]

EMAIL_PREFIXES_AU = [
    "info", "contact", "hello", "enquiries", "enquiry",
    "sales", "support", "admin"
]

DOMAIN_SUFFIXES_AU = [
    ".com.au", ".net.au", ".org.au", ".com"
]

# --------------------
# UTILS
# --------------------
def normalize_name(name: str) -> str:
    n = name.upper()
    for suf in LEGAL_SUFFIXES:
        n = re.sub(suf, "", n)
    n = re.sub(r"[^A-Z0-9 ]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n.lower()

def generate_domain_candidates(base: str):
    tokens = base.split()
    joined = "".join(tokens)
    dashed = "-".join(tokens)

    candidates = set()
    for core in {joined, dashed}:
        if len(core) >= 4:
            for suf in DOMAIN_SUFFIXES_AU:
                candidates.add(core + suf)
    return list(candidates)

def has_mx(domain: str) -> bool:
    try:
        answers = dns.resolver.resolve(domain, 'MX')
        return len(answers) > 0
    except Exception:
        return False

def generate_emails(domain: str):
    return [f"{p}@{domain}" for p in EMAIL_PREFIXES_AU]

# --------------------
# MAIN
# --------------------
def main(input_csv: Path, output_csv: Path, limit: int):
    rows = []

    with open(input_csv, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for r in reader:
            rows.append(r)
            if limit and len(rows) >= limit:
                break

    print(f"[+] Loaded {len(rows)} rows")

    results = []

    for r in rows:
        legal_name = r.get("legal_name") or r.get("business_name") or ""
        if not legal_name.strip():
            continue

        norm = normalize_name(legal_name)
        domains = generate_domain_candidates(norm)

        for dom in domains:
            if has_mx(dom):
                emails = generate_emails(dom)
                for email in emails:
                    results.append({
                        "abn": r.get("abn", ""),
                        "legal_name": legal_name,
                        "guessed_domain": dom,
                        "email": email,
                        "method": "guess_mx"
                    })
                break  # one domain with MX is enough

    print(f"[+] Generated {len(results)} email candidates")

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["abn", "legal_name", "guessed_domain", "email", "method"]
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for r in results:
            writer.writerow(r)

    print(f"[✓] Output written to {output_csv}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="ABN CSV input")
    parser.add_argument("--output", required=True, help="Output CSV")
    parser.add_argument("--limit", type=int, default=50, help="Limit rows for test")
    args = parser.parse_args()

    main(Path(args.input), Path(args.output), args.limit)
