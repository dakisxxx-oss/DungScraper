"""Structured readers for sites whose generic extraction is mostly navigation.

An extractor runs on HTML that DungScraper has already fetched, so robots.txt,
challenge handling and the browser fallback all apply first. It returns clean
Markdown plus a ``structured`` dict, or None when the page is not one it knows.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup, Tag

from dungscraper.models import LinkItem


@dataclass
class SiteResult:
    kind: str
    title: str
    markdown: str
    structured: dict[str, Any]
    links: list[LinkItem] = field(default_factory=list)


def site_extract(url: str, html: str) -> SiteResult | None:
    host = (urlsplit(url).hostname or "").lower()
    try:
        if host == "skelbiu.lt" or host.endswith(".skelbiu.lt"):
            return extract_skelbiu(url, html)
        if host == "autoplius.lt" or host.endswith(".autoplius.lt"):
            return extract_autoplius(url, html)
    except Exception:  # noqa: BLE001
        return None
    return None


# --- skelbiu.lt -----------------------------------------------------------------

_AD_PATH = re.compile(r"-(\d{5,})\.html$")
_MASKED_PHONE = re.compile(r"[\d*+\s()-]{6,}")


def _clean(text: str | None) -> str:
    return re.sub(r"\s+", " ", (text or "").replace("\xa0", " ")).strip()


def _price_number(text: str) -> float | None:
    digits = re.sub(r"[^\d,.]", "", text.replace("\xa0", "").replace(" ", "")).replace(",", ".")
    try:
        return float(digits) if digits else None
    except ValueError:
        return None


def _currency(text: str) -> str | None:
    if "€" in text:
        return "EUR"
    return None


def _split_place_age(text: str) -> tuple[str, str]:
    """'Vilnius, prieš 6 val.' -> ('Vilnius', 'prieš 6 val.'); older ads show 'Kaunas, lapkričio 21 d.'"""
    text = _clean(text)
    match = re.search(
        r"(prieš .+|šiandien.*|vakar.*|[^\W\d_]+ \d{1,2} d\.?.*|\d{4}-\d{2}-\d{2}.*)$", text, re.I
    )
    if not match:
        return text, ""
    return text[: match.start()].strip(" ,"), match.group(1).strip()


def _fmt(value: Any) -> str:
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value)


def _listing_markdown(title: str, url: str, listings: list[dict[str, Any]], next_link: str | None) -> str:
    lines = [f"# {title}", "", f"{len(listings)} ads on this page. Source: {url}", ""]
    lines += ["| # | Title | Price | Location | Posted | Details |", "|---|---|---|---|---|---|"]
    for index, item in enumerate(listings, 1):
        price = item["price_text"] or "-"
        if item.get("price_change"):
            price += f" ({item['price_change']})"
        cells = [
            str(index),
            f"[{item['title']}]({item['url']})",
            price,
            item["location"],
            item["posted"],
            ", ".join(item["params"]),
        ]
        lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
    if next_link:
        lines += ["", f"Next page: {next_link}"]
    return "\n".join(lines)


def _ad_markdown(ad: dict[str, Any]) -> str:
    lines = [f"# {ad['title']}", ""]
    price = ad.get("price")
    price_line = ad.get("price_text") or (f"{_fmt(price)} {ad.get('currency') or ''}".strip() if price is not None else "no price")
    lines.append(f"**Price:** {price_line}")
    for label, key in (("Location", "location"), ("Condition", "condition"), ("Updated", "updated"), ("Ad ID", "id")):
        if ad.get(key):
            lines.append(f"**{label}:** {ad[key]}")
    if ad.get("category"):
        lines.append(f"**Category:** {' > '.join(ad['category'])}")
    if ad.get("attributes"):
        lines += ["", "## Attributes", ""]
        lines += [f"- {k}: {v}" if v else f"- {k}" for k, v in ad["attributes"].items()]
    if ad.get("features"):
        lines += ["", "## Features", "", ", ".join(ad["features"])]
    if ad.get("description"):
        lines += ["", "## Description", "", ad["description"]]
    if ad.get("seller"):
        lines += ["", "## Seller", ""]
        lines += [f"- {k.replace('_', ' ')}: {v}" for k, v in ad["seller"].items()]
    if ad.get("images"):
        lines += ["", f"## Images ({len(ad['images'])})", ""]
        lines += [f"- {src}" for src in ad["images"]]
    lines += ["", f"Source: {ad['url']}"]
    return "\n".join(lines)


def extract_skelbiu(url: str, html: str) -> SiteResult | None:
    soup = BeautifulSoup(html, "lxml")
    path = urlsplit(url).path
    if soup.select("a.standard-list-item"):
        return _skelbiu_listing(url, soup)
    if _AD_PATH.search(path) and soup.find("h1"):
        return _skelbiu_ad(url, soup)
    return None


def _skelbiu_listing(url: str, soup: BeautifulSoup) -> SiteResult:
    heading = soup.select_one("h1")
    title = _clean(heading.get_text(" ")) if heading else _clean(soup.title.get_text()) if soup.title else "Skelbiu.lt"
    listings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in soup.select("a.standard-list-item"):
        href = node.get("href") or ""
        ad_id = node.get("data-item-id") or (_AD_PATH.search(href).group(1) if _AD_PATH.search(href) else "")
        if not href or ad_id in seen:
            continue
        seen.add(ad_id)
        name = node.select_one(".collapsed-info .title") or node.select_one(".title")
        price_node = node.select_one(".collapsed-info .price") or node.select_one(".price")
        price_text = _clean(price_node.get_text(" ")) if price_node else ""
        place_node = node.select_one(".collapsed-info .second-line") or node.select_one(".second-dataline")
        place, age = _split_place_age(place_node.get_text(" ") if place_node else "")
        change = node.select_one(".price-change")
        change_text = _clean(change.get_text(" ")) if change else ""
        if change_text and change.select_one(".down"):
            change_text = "-" + change_text.lstrip("-")
        elif change_text and change.select_one(".up"):
            change_text = "+" + change_text.lstrip("+")
        image = node.select_one(".img-block img")
        listings.append(
            {
                "id": ad_id,
                "title": _clean(name.get_text(" ")) if name else "",
                "price": _price_number(price_text),
                "currency": _currency(price_text),
                "price_text": price_text,
                "price_change": change_text or None,
                "location": place,
                "posted": age,
                "params": [_clean(p.get_text(" ")) for p in node.select(".item-params .param")],
                "image": image.get("src") if image is not None else None,
                "url": urljoin(url, href),
                "promoted": bool(node.select_one(".star")),
            }
        )
    next_link = None
    for anchor in soup.select(".pagination a, a[rel=next]"):
        if _clean(anchor.get_text()) in {"»", "›", "Kitas", "Next"} or anchor.get("rel") == ["next"]:
            next_link = urljoin(url, anchor.get("href") or "")
            break
    markdown = _listing_markdown(title, url, listings, next_link)
    structured = {"site": "skelbiu.lt", "type": "listing", "title": title, "count": len(listings),
                  "next_page": next_link, "listings": listings}
    links = [LinkItem(url=item["url"], text=item["title"]) for item in listings]
    return SiteResult("listing", title, markdown, structured, links)


def _json_ld_product(soup: BeautifulSoup) -> dict[str, Any]:
    for script in soup.find_all("script", type="application/ld+json"):
        try:
            data = json.loads(script.string or script.get_text() or "")
        except ValueError:
            continue
        for node in data if isinstance(data, list) else [data]:
            if isinstance(node, dict) and node.get("@type") in {"Product", "Offer"}:
                return node
    return {}


def _skelbiu_ad(url: str, soup: BeautifulSoup) -> SiteResult:
    product = _json_ld_product(soup)
    offer = product.get("offers") if isinstance(product.get("offers"), dict) else {}
    h1 = soup.find("h1")
    title = _clean(h1.get_text(" ")) if h1 else _clean(str(product.get("name") or ""))
    ad_id = ""
    match = _AD_PATH.search(urlsplit(url).path)
    if match:
        ad_id = match.group(1)
    id_block = soup.select_one(".block.id")
    if id_block is not None:
        found = re.search(r"ID:\s*(\d+)", id_block.get_text(" "))
        if found:
            ad_id = found.group(1)
    price: float | None = None
    if offer.get("price") not in (None, ""):
        try:
            price = float(offer["price"])
        except (TypeError, ValueError):
            price = None
    price_node = soup.select_one(".price")
    price_text = _clean(price_node.get_text(" ")) if price_node else ""
    if price is None and price_text:
        price = _price_number(price_text)
    currency = offer.get("priceCurrency") or _currency(price_text)
    condition = str(offer.get("itemCondition") or "").rsplit("/", 1)[-1].replace("Condition", "") or None
    availability = str(offer.get("availability") or "").rsplit("/", 1)[-1] or None

    attributes: dict[str, str] = {}
    features: list[str] = []
    for row in soup.select(".details-row"):
        label = row.find("label")
        if label is None:
            continue
        key = _clean(label.get_text(" ")).rstrip(":")
        if "features-list" in (row.get("class") or []):
            features = [_clean(s.get_text(" ")) for s in row.select(".values span") if _clean(s.get_text(" "))]
            continue
        values = row.select_one(".values") or row
        text = _clean(values.get_text(" ")) if values is not row else _clean(row.get_text(" ")).split(":", 1)[-1].strip()
        if key:
            attributes[key] = text
    description_node = soup.select_one(".item-description .description")
    description = ""
    if description_node is not None:
        for br in description_node.find_all("br"):
            br.replace_with("\n")
        description = "\n".join(_clean(line) for line in description_node.get_text().splitlines()).strip()
        description = re.sub(r"\n{3,}", "\n\n", description)
    elif product.get("description"):
        description = str(product["description"]).strip()

    location_node = soup.select_one(".cities-name") or soup.select_one(".cities-location")
    location = _clean(location_node.get_text(" ")) if location_node else ""
    breadcrumb = [_clean(a.get_text(" ")) for a in soup.select("ol.breadcrumbs a") if _clean(a.get_text(" "))]
    images = [str(i) for i in (product.get("image") or []) if i] if isinstance(product.get("image"), list) else (
        [str(product["image"])] if product.get("image") else []
    )
    renewed = soup.select_one(".block.renewed")
    updated = _clean(renewed.get_text(" ")) if renewed else ""

    seller: dict[str, Any] = {}
    card = soup.select_one(".user-card")
    if card is not None:
        name_node = card.select_one(".profile-data-owner-name")
        name = _clean(name_node.get_text(" ")) if name_node else ""
        # Skelbiu shows a masked phone number when the seller has no public name. Never keep digits.
        if name and not _MASKED_PHONE.fullmatch(name) and not re.search(r"\d{4,}|\*{2,}", name):
            seller["name"] = name
        info = card.select_one(".profile-info-line")
        info_text = _clean(info.get_text(" ")) if info else ""
        since = re.search(r"Užsiregistravo\s+(.+?)\.", info_text)
        if since:
            seller["member_since"] = since.group(1)
        total = re.search(r"Įdėjo skelbimų:\s*(\d+)", info_text)
        if total:
            seller["ads_posted_total"] = int(total.group(1))
        all_items = card.select_one("a.all-sellers-items")
        if all_items is not None:
            seller["profile_url"] = urljoin(url, all_items.get("href") or "")
            count = re.search(r"(\d+)\s*$", _clean(all_items.get_text(" ")))
            if count:
                seller["active_ads"] = int(count.group(1))

    canonical = soup.find("link", rel="canonical")
    ad = {
        "id": ad_id,
        "title": title,
        "price": price,
        "currency": currency,
        "price_text": price_text,
        "condition": condition,
        "availability": availability,
        "location": location,
        "category": breadcrumb[1:] if breadcrumb and breadcrumb[0] == "Skelbimai" else breadcrumb,
        "attributes": attributes,
        "features": features,
        "description": description,
        "images": images,
        "updated": updated,
        "seller": seller,
        "url": (canonical.get("href") if isinstance(canonical, Tag) and canonical.get("href") else url),
    }
    links = [LinkItem(url=src, text="image") for src in images]
    return SiteResult("ad", title, _ad_markdown(ad), {"site": "skelbiu.lt", "type": "ad", **ad}, links)


# --- autoplius.lt ---------------------------------------------------------------

_AUTOPLIUS_AD = re.compile(r"-(\d{6,})\.html$")
_HIDDEN_ATTRS = ("VIN", "SDK")  # partial identifiers behind a "show" button; never copied


def extract_autoplius(url: str, html: str) -> SiteResult | None:
    soup = BeautifulSoup(html, "lxml")
    if soup.select("a.announcement-item"):
        return _autoplius_listing(url, soup)
    if _AUTOPLIUS_AD.search(urlsplit(url).path) and soup.select_one(".parameter-row"):
        return _autoplius_ad(url, soup)
    return None


def _autoplius_listing(url: str, soup: BeautifulSoup) -> SiteResult:
    heading = soup.select_one("h1")
    title = _clean(heading.get_text(" ")) if heading else _clean(soup.title.get_text()) if soup.title else "Autoplius.lt"
    listings: list[dict[str, Any]] = []
    seen: set[str] = set()
    for node in soup.select("a.announcement-item"):
        href = node.get("href") or ""
        ad_id = node.get("data-hidden-id") or (_AUTOPLIUS_AD.search(href).group(1) if _AUTOPLIUS_AD.search(href) else "")
        if not href or ad_id in seen:
            continue
        seen.add(ad_id)
        name = node.select_one(".announcement-title")
        price_node = node.select_one(".announcement-pricing-info strong")
        price_text = _clean(price_node.get_text(" ")) if price_node else ""
        heading_params = [_clean(x.get_text(" ")) for x in node.select(".announcement-title-parameters span")]
        spans = node.select(".announcement-parameters-block .announcement-parameters > span")
        body_params = [_clean(x.get_text(" ")) for x in spans if "announcement-parameter-range-km" not in (x.get("class") or [])]
        location = ""
        if body_params and not re.search(r"\d", body_params[-1]):
            location = body_params.pop()
        badge = node.select_one(".badge-new, .badge-updated")
        image = node.select_one(".announcement-media img")
        listings.append(
            {
                "id": ad_id,
                "title": _clean(name.get_text(" ")) if name else "",
                "price": _price_number(price_text),
                "currency": _currency(price_text),
                "price_text": price_text,
                "price_change": None,
                "location": location,
                "posted": _clean(badge.get_text(" ")) if badge else "",
                "params": [*heading_params, *[p for p in body_params if p]],
                "image": image.get("src") if image is not None else None,
                "url": urljoin(url, href),
                "promoted": False,
            }
        )
    next_node = soup.select_one("a[rel=next], a.next")
    next_link = urljoin(url, next_node.get("href") or "") if next_node is not None and next_node.get("href") else None
    markdown = _listing_markdown(title, url, listings, next_link)
    structured = {"site": "autoplius.lt", "type": "listing", "title": title, "count": len(listings),
                  "next_page": next_link, "listings": listings}
    links = [LinkItem(url=item["url"], text=item["title"]) for item in listings]
    return SiteResult("listing", title, markdown, structured, links)


def _autoplius_ad(url: str, soup: BeautifulSoup) -> SiteResult:
    year = soup.select_one(".title-year")
    name = soup.select_one(".title-text")
    title = _clean(" ".join(x.get_text(" ") for x in (year, name) if x is not None)) or _clean(
        soup.title.get_text() if soup.title else ""
    )
    price_node = soup.select_one(".announcement-price .price")
    price_text = ""
    if price_node is not None:
        for warn in price_node.select(".cash-usage-limit-container"):
            warn.decompose()
        price_text = _clean(price_node.get_text(" "))
    price = _price_number(price_text)
    attributes: dict[str, str] = {}
    ad_id = ""
    for row in soup.select(".parameter-row"):
        label = row.select_one(".parameter-label")
        value = row.select_one(".parameter-value")
        if label is None:
            continue
        key = _clean(label.get_text(" "))
        if not key or any(word in key.upper() for word in _HIDDEN_ATTRS):
            continue
        if value is None:
            attributes[key] = ""
            continue
        for extra in value.select("a, .tooltip, .js-show-vin, button"):
            extra.decompose()
        text = _clean(value.get_text(" "))
        if key == "Skelbimo ID":
            ad_id = text
            continue
        text = re.sub(r"\s*(Gamintojo duomenys|Naudinga žinoti »?|Rodyti)$", "", text)
        if key.startswith("Registracijos mokestis"):
            text = text.split(" Mokestis")[0]
        attributes[key] = text
    match = _AUTOPLIUS_AD.search(urlsplit(url).path)
    if not ad_id and match:
        ad_id = "A" + match.group(1)
    crumbs = [_clean(a.get_text(" ")) for a in soup.select(".breadcrumbs a") if _clean(a.get_text(" "))]
    description_node = soup.select_one(".announcement-description")
    description = ""
    if description_node is not None:
        for br in description_node.find_all("br"):
            br.replace_with("\n")
        description = "\n".join(_clean(x) for x in description_node.get_text().splitlines() if _clean(x))
    seller: dict[str, Any] = {}
    seller_node = soup.select_one(".seller-contact-name a")
    if seller_node is not None:
        seller["name"] = _clean(seller_node.get_text(" "))
        if seller_node.get("href"):
            seller["profile_url"] = urljoin(url, seller_node["href"])
    if soup.select_one(".identity-badge.verified"):
        seller["identity_verified"] = True
    location_node = soup.select_one(".seller-contact-location")
    location = _clean(location_node.get_text(" ")) if location_node else ""
    images: list[str] = []
    stem = ""
    seen_ids: set[str] = set()
    for img in soup.find_all("img"):
        src = img.get("src") or img.get("data-src") or ""
        found = re.match(r"(https://autoplius-img\.dgn\.lt/ann_3_(\d+)/(.+)-\d+\.\w+)$", src)
        if not found:
            continue
        this_stem = found.group(3)
        stem = stem or this_stem
        if this_stem != stem or found.group(2) in seen_ids:
            continue
        seen_ids.add(found.group(2))
        images.append(src)
    ad = {
        "id": ad_id,
        "title": title,
        "price": price,
        "currency": _currency(price_text),
        "price_text": price_text,
        "condition": None,
        "availability": None,
        "location": location,
        "category": crumbs[1:] if crumbs and crumbs[0] == "Skelbimai" else crumbs,
        "attributes": attributes,
        "features": [],
        "description": description,
        "images": images,
        "updated": "",
        "seller": seller,
        "url": url,
    }
    return SiteResult("ad", title, _ad_markdown(ad), {"site": "autoplius.lt", "type": "ad", **ad},
                      [LinkItem(url=src, text="image") for src in images])
