#!/usr/bin/env python3
"""
Free hosted laptop price watcher for GitHub Actions.

What it does:
- Reads product URLs from config.yaml
- Tries to extract product title and price from JSON-LD, meta tags, and page text
- Scores each listing against Blender/Maya-oriented requirements
- Saves latest results to data/latest.json and data/history.json
- Optionally opens a GitHub issue when a product is at/below your target price

Important:
- This is a polite watchlist agent, not a full web-wide search engine.
- Some retailers block automated requests or render prices with JavaScript.
- Check retailer terms and keep request volume low.
"""
from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests
import yaml
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.yaml"
DATA_DIR = ROOT / "data"
LATEST_PATH = DATA_DIR / "latest.json"
HISTORY_PATH = DATA_DIR / "history.json"
ALERTS_MD_PATH = DATA_DIR / "alerts.md"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (compatible; MichaelaLaptopPriceAgent/1.0; +https://github.com/)",
    "Accept-Language": "en-US,en;q=0.9",
}

GPU_RANK = {
    "RTX 5090": 100,
    "RTX 5080": 95,
    "RTX 4090": 90,
    "RTX 4080": 85,
    "RTX 5070 TI": 82,
    "RTX 5070": 78,
    "RTX 4070": 70,
    "RTX 4060": 55,
    "RTX 4050": 40,
}

PRICE_RE = re.compile(r"\$\s?([0-9]{1,3}(?:,[0-9]{3})*(?:\.[0-9]{2})?|[0-9]+(?:\.[0-9]{2})?)")
RAM_RE = re.compile(r"(?:^|\D)(16|24|32|48|64|96|128)\s?GB\s+(?:DDR\d\s+)?(?:RAM|Memory)?", re.I)
SSD_RE = re.compile(r"(?:^|\D)(512\s?GB|1\s?TB|2\s?TB|4\s?TB|1000\s?GB|2000\s?GB)\s+(?:NVMe\s+)?(?:SSD|Storage)?", re.I)


@dataclass
class Result:
    name: str
    url: str
    ok: bool
    alert: bool
    price: float | None
    max_total_price: float | None
    title: str | None
    detected_gpu: str | None
    detected_ram_gb: int | None
    detected_ssd_gb: int | None
    score: int
    reasons: list[str]
    checked_at: str


def load_config() -> dict[str, Any]:
    with CONFIG_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def fetch(url: str) -> str:
    response = requests.get(url, headers=HEADERS, timeout=25)
    response.raise_for_status()
    return response.text


def flatten_json(obj: Any) -> Iterable[Any]:
    if isinstance(obj, dict):
        yield obj
        for value in obj.values():
            yield from flatten_json(value)
    elif isinstance(obj, list):
        for value in obj:
            yield from flatten_json(value)


def parse_jsonld(soup: BeautifulSoup) -> tuple[str | None, float | None]:
    title = None
    price = None
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        text = script.string or script.get_text(strip=True)
        if not text:
            continue
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            continue
        for node in flatten_json(data):
            if not isinstance(node, dict):
                continue
            node_type = node.get("@type")
            if isinstance(node_type, list):
                node_type = " ".join(str(x) for x in node_type)
            node_type = str(node_type or "").lower()
            if "product" in node_type and not title:
                title = node.get("name") or title
            if "offer" in node_type or "product" in node_type:
                candidate = node.get("price") or node.get("lowPrice") or node.get("highPrice")
                if candidate is not None and price is None:
                    try:
                        price = float(str(candidate).replace(",", "").replace("$", ""))
                    except ValueError:
                        pass
    return title, price


def parse_meta(soup: BeautifulSoup) -> tuple[str | None, float | None]:
    title = None
    price = None
    for selector in [
        {"property": "og:title"},
        {"name": "twitter:title"},
        {"name": "title"},
    ]:
        tag = soup.find("meta", attrs=selector)
        if tag and tag.get("content") and not title:
            title = tag["content"].strip()
    if not title and soup.title and soup.title.string:
        title = soup.title.string.strip()

    for selector in [
        {"property": "product:price:amount"},
        {"name": "price"},
        {"itemprop": "price"},
    ]:
        tag = soup.find("meta", attrs=selector)
        if tag and tag.get("content") and price is None:
            try:
                price = float(tag["content"].replace(",", "").replace("$", ""))
            except ValueError:
                pass
    return title, price


def parse_text_price(text: str) -> float | None:
    matches = []
    for m in PRICE_RE.finditer(text[:250000]):
        try:
            value = float(m.group(1).replace(",", ""))
            # laptop prices we care about; filters bogus small prices
            if 700 <= value <= 7000:
                matches.append(value)
        except ValueError:
            pass
    if not matches:
        return None
    # Use the lowest plausible large price found; many pages include MSRP and sale price.
    return min(matches)


def detect_gpu(text: str, acceptable: list[str]) -> str | None:
    upper = text.upper().replace("GEFORCE", "")
    candidates = sorted(set(acceptable + list(GPU_RANK.keys())), key=lambda x: GPU_RANK.get(x.upper(), 0), reverse=True)
    for gpu in candidates:
        if gpu.upper() in upper:
            return gpu.upper().replace(" TI", " Ti")
    return None


def detect_ram(text: str) -> int | None:
    values = []
    for m in RAM_RE.finditer(text):
        try:
            values.append(int(m.group(1)))
        except ValueError:
            pass
    return max(values) if values else None


def ssd_to_gb(value: str) -> int | None:
    value = value.upper().replace(" ", "")
    if value.endswith("TB"):
        return int(float(value.replace("TB", "")) * 1000)
    if value.endswith("GB"):
        return int(float(value.replace("GB", "")))
    return None


def detect_ssd(text: str) -> int | None:
    values = []
    for m in SSD_RE.finditer(text):
        gb = ssd_to_gb(m.group(1))
        if gb:
            values.append(gb)
    return max(values) if values else None


def evaluate_item(item: dict[str, Any], config: dict[str, Any]) -> Result:
    now = datetime.now(timezone.utc).isoformat()
    url = item["url"]
    max_price = float(item.get("max_total_price") or config["settings"].get("max_total_price_default") or 0)
    reasons: list[str] = []
    score = 0

    if "example.com" in url:
        return Result(item.get("name", "Unnamed"), url, False, False, None, max_price, None, None, None, None, 0, ["Replace example URL with a real retailer product URL."], now)

    try:
        html = fetch(url)
    except Exception as e:
        return Result(item.get("name", "Unnamed"), url, False, False, None, max_price, None, None, None, None, 0, [f"Fetch failed: {e}"], now)

    soup = BeautifulSoup(html, "lxml")
    jsonld_title, jsonld_price = parse_jsonld(soup)
    meta_title, meta_price = parse_meta(soup)
    text = soup.get_text(" ", strip=True)

    title = (jsonld_title or meta_title or item.get("name") or "").strip()
    title_and_text = f"{title} {text}"
    price = jsonld_price or meta_price or parse_text_price(text)

    req = config.get("requirements", {})
    acceptable_gpus = req.get("acceptable_gpus", [])
    avoid_terms = [x.lower() for x in req.get("avoid_if_title_contains", [])]
    detected_gpu = detect_gpu(title_and_text, acceptable_gpus)
    detected_ram = detect_ram(title_and_text)
    detected_ssd = detect_ssd(title_and_text)

    lower_title = title.lower()
    for avoid in avoid_terms:
        if avoid in lower_title:
            reasons.append(f"Avoid term found: {avoid}")
            score -= 50

    if detected_gpu:
        score += GPU_RANK.get(detected_gpu.upper(), 60)
        reasons.append(f"GPU detected: {detected_gpu}")
    else:
        reasons.append("Could not detect acceptable GPU from page text/title.")

    min_ram = int(req.get("min_ram_gb", 32))
    if detected_ram is not None:
        if detected_ram >= min_ram:
            score += 25
            reasons.append(f"RAM OK: {detected_ram} GB")
        else:
            reasons.append(f"RAM too low: {detected_ram} GB")
            score -= 25
    else:
        reasons.append("Could not detect RAM.")

    min_ssd = int(req.get("min_ssd_gb", 1000))
    if detected_ssd is not None:
        if detected_ssd >= min_ssd:
            score += 10
            reasons.append(f"SSD OK: {detected_ssd} GB")
        else:
            reasons.append(f"SSD smaller than target: {detected_ssd} GB")
    else:
        reasons.append("Could not detect SSD size.")

    if price is not None:
        if price <= max_price:
            score += 40
            reasons.append(f"Price is at/below target: ${price:,.2f} <= ${max_price:,.2f}")
        else:
            reasons.append(f"Price above target: ${price:,.2f} > ${max_price:,.2f}")
    else:
        reasons.append("Could not detect price.")

    ok = score >= 80 and price is not None
    alert = ok and price <= max_price
    return Result(item.get("name", title or "Unnamed"), url, ok, alert, price, max_price, title, detected_gpu, detected_ram, detected_ssd, score, reasons, now)


def read_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True), encoding="utf-8")


def alert_markdown(results: list[Result]) -> str:
    alert_results = [r for r in results if r.alert]
    lines = ["# Laptop price alert", ""]
    if not alert_results:
        lines.append("No current alerts.")
        return "\n".join(lines) + "\n"
    for r in alert_results:
        lines.append(f"## {r.title or r.name}")
        lines.append(f"- Price: **${r.price:,.2f}**")
        lines.append(f"- Target: ${r.max_total_price:,.2f}")
        lines.append(f"- GPU: {r.detected_gpu or 'unknown'}")
        lines.append(f"- RAM: {r.detected_ram_gb or 'unknown'} GB")
        lines.append(f"- SSD: {r.detected_ssd_gb or 'unknown'} GB")
        lines.append(f"- Score: {r.score}")
        lines.append(f"- URL: {r.url}")
        lines.append("- Reasons:")
        for reason in r.reasons:
            lines.append(f"  - {reason}")
        lines.append("")
    return "\n".join(lines)


def maybe_create_github_issue(results: list[Result], config: dict[str, Any]) -> None:
    if not config.get("settings", {}).get("create_github_issue_alerts", True):
        return
    alert_results = [r for r in results if r.alert]
    if not alert_results:
        return

    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo:
        print("GITHUB_TOKEN/GITHUB_REPOSITORY not available; skipping issue alert.")
        return

    # Prevent duplicate issue spam for the same URL/price.
    state = read_json(DATA_DIR / "alert_state.json", {})
    current_key = "|".join(sorted(f"{r.url}:{r.price}" for r in alert_results))
    if state.get("last_alert_key") == current_key:
        print("Same alert already recorded; skipping duplicate issue.")
        return

    title = f"Laptop deal alert: {len(alert_results)} candidate(s) at target price"
    body = alert_markdown(results)
    label = config.get("settings", {}).get("issue_label", "laptop-price-alert")

    response = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
        json={"title": title, "body": body, "labels": [label]},
        timeout=25,
    )
    if response.status_code >= 300:
        print(f"GitHub issue creation failed: {response.status_code} {response.text[:300]}")
        return
    state["last_alert_key"] = current_key
    state["last_alerted_at"] = datetime.now(timezone.utc).isoformat()
    write_json(DATA_DIR / "alert_state.json", state)
    print("Created GitHub issue alert.")


def main() -> int:
    DATA_DIR.mkdir(exist_ok=True)
    config = load_config()
    items = config.get("watch_items", [])
    if not items:
        print("No watch_items in config.yaml")
        return 1

    results = [evaluate_item(item, config) for item in items]
    result_dicts = [asdict(r) for r in results]
    write_json(LATEST_PATH, result_dicts)

    history = read_json(HISTORY_PATH, [])
    history.extend(result_dicts)
    history = history[-500:]  # keep file small
    write_json(HISTORY_PATH, history)

    ALERTS_MD_PATH.write_text(alert_markdown(results), encoding="utf-8")
    maybe_create_github_issue(results, config)

    print(json.dumps(result_dicts, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
