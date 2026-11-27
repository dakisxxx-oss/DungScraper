"""skelbiu.lt structured extraction (0.3.3)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from dungscraper.core.fetch import make_client
from dungscraper.core.scrape import scrape_url
from dungscraper.core.sites import extract_skelbiu, site_extract
from dungscraper.models import ScrapeRequest

FIX = Path(__file__).parent / "fixtures"
LIST_URL = "https://www.skelbiu.lt/skelbimai/transportas/dviraciai/"
AD_URL = "https://www.skelbiu.lt/skelbimai/kukirin-g2-800-w-2026m-turime-vietoje-garantija-81870230.html"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_listing_fields():
    result = extract_skelbiu(LIST_URL, _read("skelbiu_listing.html"))
    assert result and result.kind == "listing"
    assert result.title == "Dviračiai, paspirtukai - skelbimai"
    items = result.structured["listings"]
    assert len(items) == 6 and result.structured["count"] == 6
    first = items[0]
    assert first["id"] == "81870230"
    assert first["title"].startswith("Kukirin G2 800 W")
    assert first["price"] == 529 and first["currency"] == "EUR"
    assert first["location"] == "Vilnius"
    assert first["posted"].startswith("prieš")
    assert "Naujas" in first["params"]
    assert first["image"].startswith("https://skelbiu-img.dgn.lt/")
    assert first["url"] == "https://www.skelbiu.lt/skelbimai/kukirin-g2-800-w-2026m-turime-vietoje-garantija-81870230.html"
    assert result.structured["next_page"] == "https://www.skelbiu.lt/skelbimai/transportas/dviraciai/2"
    assert "| 1 | [Kukirin G2 800 W" in result.markdown
    assert "Next page:" in result.markdown
    assert any(item["price_change"] == "-25%" for item in items)
    assert len({item["id"] for item in items}) == 6


def test_ad_fields_and_no_phone():
    result = extract_skelbiu(AD_URL, _read("skelbiu_ad.html"))
    assert result and result.kind == "ad"
    ad = result.structured
    assert ad["id"] == "81870230"
    assert ad["title"].startswith("Kukirin G2 800 W")
    assert ad["price"] == 529 and ad["currency"] == "EUR"
    assert ad["condition"] == "New"
    assert ad["location"] == "Vilnius"
    assert ad["category"][0] == "Transportas" and ad["category"][-1] == "Kugoo"
    assert ad["attributes"]["Galia, W"] == "800"
    assert ad["attributes"]["Spalva"] == "Juoda"
    assert "Sulankstomas" in ad["features"]
    assert "Techniniai duomenys" in ad["description"]
    assert "\n" in ad["description"]
    assert len(ad["images"]) == 13
    assert ad["updated"].startswith("Atnaujintas")
    seller = ad["seller"]
    # The page shows a masked phone number instead of a name. It must not leak into the output.
    assert "name" not in seller
    assert seller["member_since"] == "2010 liepą"
    assert seller["ads_posted_total"] == 12616
    assert seller["active_ads"] == 19
    assert seller["profile_url"] == "https://www.skelbiu.lt/pasiulymai/smart123/"
    assert "3706241" not in result.markdown
    assert "**Price:** 529 €" in result.markdown


def test_place_and_age_split():
    from dungscraper.core.sites import _split_place_age

    assert _split_place_age("Vilnius, prieš 6 val.") == ("Vilnius", "prieš 6 val.")
    assert _split_place_age("Šiauliai, lapkričio 21 d.") == ("Šiauliai", "lapkričio 21 d.")
    assert _split_place_age("Tauragės r., spalio 30 d.") == ("Tauragės r.", "spalio 30 d.")
    assert _split_place_age("Vilnius") == ("Vilnius", "")


def test_unknown_pages_are_left_alone():
    assert site_extract("https://example.com/", "<html><body><h1>x</h1></body></html>") is None
    assert extract_skelbiu("https://www.skelbiu.lt/", "<html><body><h1>Home</h1></body></html>") is None
    assert site_extract("https://www.skelbiu.lt/skelbimai/x-12345678.html", "") is None


@pytest.mark.asyncio
async def test_scrape_uses_extractor_and_respects_robots(settings):
    robots = "User-agent: *\nDisallow: /*?keywords=*\nAllow: /\n"

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=robots)
        if request.url.path.endswith(".html"):
            return httpx.Response(200, text=_read("skelbiu_ad.html"), headers={"content-type": "text/html; charset=utf-8"})
        return httpx.Response(200, text=_read("skelbiu_listing.html"), headers={"content-type": "text/html; charset=utf-8"})

    settings = settings.model_copy(update={"allow_private_networks": True})
    async with make_client(settings) as client:
        client._transport = httpx.MockTransport(handler)  # noqa: SLF001
        listing = await scrape_url(
            ScrapeRequest(url=LIST_URL, formats=["markdown", "metadata", "links"]),
            settings=settings,
            client=client,
        )
        assert listing.structured and listing.structured["type"] == "listing"
        assert "site_extractor:listing" in listing.warnings
        assert listing.metadata.title == "Dviračiai, paspirtukai - skelbimai"
        assert len(listing.links) == 6

        ad = await scrape_url(ScrapeRequest(url=AD_URL), settings=settings, client=client)
        assert ad.structured["price"] == 529
        assert ad.markdown.startswith("# Kukirin G2")

        with pytest.raises(Exception, match="robots"):
            await scrape_url(
                ScrapeRequest(url="https://www.skelbiu.lt/skelbimai/?keywords=dviratis"),
                settings=settings,
                client=client,
            )
