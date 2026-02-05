# Sysiphe (V2) — AI-powered B2B Prospecting Pipeline

Sysiphe is a lightweight pipeline that turns large administrative business datasets into **actionable B2B outreach lists** (companies + emails), using open-source tooling and minimal paid APIs.

The goal is not “growth hacking”, but **reproducible data engineering + measurable outreach experiments**.

## What it does

- Ingest massive business registries (20M+ rows)
- Filter / score companies (targets)
- Find company websites and contact emails (2 validated methods)
- Build outreach-ready contact lists
- Track campaign performance (open/click/unsub/reply) to iterate

## Data Source (example)
- **ABN Bulk Extract** (Australian Business Number) from data.gov.au  
  Example fields used:
  - abn (company identifier)
  - legal_name
  - entity_type
  - state, postcode
  - optional business_name

## Pipeline overview

text
ABN CSV (20M+)
   ↓ filtering / scoring
targets
   ↓
[Web scraping] OR [SerpAPI]
   ↓
contacts_found
   ↓
Brevo campaign
   ↓
metrics (opens/clicks/unsubs/replies)

## Methods

## 1) Web scraping (validated)

Find official website (search engine)

Scrape key pages (/, /contact, /about)

Extract emails via regex

Pros:

Free

Real emails → lower bounce risk

Cons:

Slow

Many companies have weak/no websites

Observed: ~40% of scraped sites yielded a usable email (small batches).

## 2) Search via SerpAPI (validated)

Automate Google queries like:
"LEGAL NAME" NSW postcode contact email

Parse SERP and extract emails

Pros:

More scalable

Works even for low-visibility SMEs

Cons:

Paid API

Variable success rate

Observed: ~18–22% email discovery on tested batches.

## 3) Email guessing (exploratory — not used in production)

Generate info@ / contact@ / enquiries@

Validate with MX / HTTP

Result: too many false positives → abandoned.

Early performance (Feb 2026)

Context:

Cold outbound

No brand / no audience

Free Brevo account

Campaign totals:

Emails sent: 84

Opens: 15 (17.8% open rate)

Clicks: 0

Unsubscribes: 1 (1.1%)

Replies: 0

Interpretation:

Deliverability is acceptable (opens in a normal cold range)

Main bottleneck is message conversion (copy + targeting)

V2 objective was pipeline reliability & measurement, not immediate sales

## Repository structure

data/ — sample inputs / outputs (no sensitive data)

search/ — search / serpapi scripts
Setup
Requirements

    Python 3.10+

    pip install -r requirements.txt (if provided)

Environment variables

Copy env_example.txt → .env and set:

    SERPAPI_KEY=... (optional)

Usage (high level)

    Build a target list (filter ABN extract)

    Run email discovery:

        web scraping OR SerpAPI

    Export contacts to Brevo CSV

    Send campaign and record metrics

    This repo is designed for learning, iteration, and reproducible experiments.

Roadmap (paused)

    Better industry filtering (sector selection)

    Smarter scoring (LLM-assisted rationale)

    Caching / deduplication at scale

    Scheduler for batches

    A/B testing framework for outreach copy

Disclaimer

This project is for educational and experimental purposes.
Always comply with local regulations and anti-spam rules.
outreach/ — campaign exports / formatting helpers

guess/ — exploratory guessing approach (disabled)

schema.sql — storage schema draft

eval/ — small evaluation notebooks / notes

## Setup

Requirements

Python 3.10+

pip install -r requirements.txt (if provided)

Environment variables

Copy env_example.txt → .env and set:

SERPAPI_KEY=... (optional)

Usage (high level)

Build a target list (filter ABN extract)

Run email discovery:

web scraping OR SerpAPI

Export contacts to Brevo CSV

Send campaign and record metrics

This repo is designed for learning, iteration, and reproducible experiments.

## Roadmap (paused)

Better industry filtering (sector selection)

Smarter scoring (LLM-assisted rationale)

Caching / deduplication at scale

Scheduler for batches

A/B testing framework for outreach copy

## Disclaimer

This project is for educational and experimental purposes.
Always comply with local regulations and anti-spam rules.
