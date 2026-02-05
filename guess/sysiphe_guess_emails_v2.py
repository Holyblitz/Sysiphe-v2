#!/usr/bin/env python3
import csv
import re
import argparse
from pathlib import Path
import dns.resolver
import requests

# --------------------
# CONFIG
# --------------------
LEGAL_SUFFIXES = [
    r"\bPTY\b", r"\bPTY LTD\b", r"\bLIMITED\b", r"\bLTD\b",
    r"\bPROPRIETARY\b", r"\bPROPRIETARY LIMITED\b",
    r"\bGROUP\b", r"\bHOLDINGS?\b", r"\bAUSTRALIA\b"
]

# AU: enquiries/enquiry très courant
PREFERRED_PREFIXES_AU = [
    "enquiries", "enquiry", "contact", "info", "hello", "sales", "support"
]

DOMAIN_SUFFIXES_AU = [".com.au", ".net.au", ".org.au", ".com"]

# mots “trop génériques” => risque élevé de mauvais domaine
GENERIC_TOKENS = {"insurance", "steel", "hardware", "investments", "finance", "solutions", "services", "group"}

USER_AGENT = "Mozilla/5.0 (X11; Linux x86_64) Sysiphe/1.0"
HTTP_TIMEOUT = 6


def normalize_name(name: str) -> str:
    n = name.upper()
    for suf in LEGAL_SUFFIXES:
        n = re.sub(suf, "", n)
    n = re.sub(r"[^A-Z0-9 ]", "", n)
    n = re.sub(r"\s+", " ", n).strip()
    return n.lower()


def domain_core_candidates(norm: str):
    """
    Génère des cores de domaine à partir du nom normalisé.
    On conserve quelques variantes mais on limite le bruit.
    """
    tokens = [t for t in norm.split() if t]
    if not tokens:
        return []

    # si nom ultra générique (1 token et générique), on baisse le risque en refusant
    if len(tokens) == 1 and tokens[0] in GENERIC_TOKENS:
        return []

    joined = "".join(tokens)
    dashed = "-".join(tokens)

    cores = set()

    # core complet si pas trop long
    if 4 <= len(joined) <= 24:
        cores.add(joined)
    if 4 <= len(dashed) <= 28:
        cores.add(dashed)

    # core “2 premiers tokens” (souvent marque)
    if len(tokens) >= 2:
        core2 = "".join(tokens[:2])
        if 4 <= len(core2) <= 18:
            cores.add(core2)

    return list(cores)


def domain_candidates(cores):
    out = []
    for core in cores:
        for suf in DOMAIN_SUFFIXES_AU:
            out.append(core + suf)
    return out


def mx_hosts(domain: str):
    try:
        answers = dns.resolver.resolve(domain, "MX")
        return [str(r.exchange).rstrip(".").lower() for r in answers]
    except Exception:
        return []


def is_obvious_sink(mx_list):
    """
    MX Google/Microsoft = OK (Workspace/M365).
    On ne pénalise pas.
    """
    return False


def site_responds(domain: str) -> bool:
    """
    Vérifie rapidement si le domaine semble avoir un site web.
    Ça réduit les domaines “parking” ou trop ambigus.
    """
    for scheme in ("https://", "http://"):
        url = scheme + domain
        try:
            r = requests.get(url, headers={"User-Agent": USER_AGENT}, timeout=HTTP_TIMEOUT, allow_redirects=True)
            if r.status_code < 500:
                return True
        except Exception:
            pass
    return False


def best_email(domain: str) -> str:
    for p in PREFERRED_PREFIXES_AU:
        return f"{p}@{domain}"
    return f"info@{domain}"


def confidence_score(domain: str, norm_name: str, mx_list, site_ok: bool) -> int:
    """
    Score simple (0-100) : on veut un tri rapide pour l’envoi.
    """
    score = 0

    # MX présent = base
    if mx_list:
        score += 40

    # Site répond
    if site_ok:
        score += 30

    # Bonus si core ressemble au nom (longueur / tokens)
    tokens = norm_name.split()
    if tokens:
        core = domain.split(".")[0]
        if tokens[0] in core:
            score += 20
        if len(tokens) >= 2 and tokens[1] in core:
            score += 10

    # Cap
    return min(score, 100)


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
    tested = 0
    kept = 0

    for r in rows:
        legal_name = (r.get("legal_name") or "").strip()
        business_name = (r.get("business_name") or "").strip()
        name = business_name or legal_name
        if not name:
            continue

        norm = normalize_name(name)
        cores = domain_core_candidates(norm)
        if not cores:
            continue

        candidates = domain_candidates(cores)

        best = None

        for dom in candidates:
            tested += 1
            mx_list = mx_hosts(dom)
            if not mx_list:
                continue

            # Optionnel : filtrer des MX “suspects” (ici on garde)
            if is_obvious_sink(mx_list):
                continue

            site_ok = site_responds(dom)
            # si pas de site, on peut quand même garder mais score plus faible.
            score = confidence_score(dom, norm, mx_list, site_ok)

            # on garde le meilleur domaine trouvé
            if best is None or score > best["confidence"]:
                best = {
                    "abn": r.get("abn", ""),
                    "legal_name": legal_name,
                    "display_name": name,
                    "guessed_domain": dom,
                    "email": best_email(dom),
                    "confidence": score,
                    "site_ok": "yes" if site_ok else "no",
                    "mx": ";".join(mx_list[:3]),
                    "method": "guess_mx_http"
                }

        if best:
            results.append(best)
            kept += 1

    print(f"[+] Tested domains: {tested}")
    print(f"[+] Kept companies: {kept}")
    print(f"[+] Output rows: {len(results)}")

    with open(output_csv, "w", newline="", encoding="utf-8") as f:
        fieldnames = ["email", "company_name", "abn", "guessed_domain", "confidence", "site_ok", "mx", "method"]
        w = csv.DictWriter(f, fieldnames=fieldnames)
        w.writeheader()
        for x in results:
            w.writerow({
                "email": x["email"],
                "company_name": x["display_name"],
                "abn": x["abn"],
                "guessed_domain": x["guessed_domain"],
                "confidence": x["confidence"],
                "site_ok": x["site_ok"],
                "mx": x["mx"],
                "method": x["method"]
            })

    print(f"[✓] Wrote {output_csv}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    ap.add_argument("--limit", type=int, default=50)
    args = ap.parse_args()
    main(Path(args.input), Path(args.output), args.limit)

