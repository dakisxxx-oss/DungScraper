"""autoplius.lt extraction, generic JSON-LD data and the watch tool (0.4.0)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from dungscraper.core.fetch import make_client
from dungscraper.core.sites import extract_autoplius
from dungscraper.core.structured import page_structured_data
from dungscraper.core.watch import compare_listings, watch_page
from dungscraper.models import ScrapeRequest

FIX = Path(__file__).parent / "fixtures"
LIST_URL = "https://autoplius.lt/skelbimai/naudoti-automobiliai"
AD_URL = "https://autoplius.lt/skelbimai/volkswagen-passat-1-4-l-universalas-2015-benzinas-elektra-33239618.html"


def _read(name: str) -> str:
    return (FIX / name).read_text(encoding="utf-8")


def test_autoplius_listing():
    result = extract_autoplius(LIST_URL, _read("autoplius_listing.html"))
    assert result and result.kind == "listing"
    items = result.structured["listings"]
    assert len(items) == 6
    first = items[0]
    assert first["id"] == "33239618"
    assert first["title"] == "Volkswagen Passat"
    assert first["price"] == 8700 and first["currency"] == "EUR"
    assert first["location"] == "Kaunas"
    assert first["posted"] == "Prieš 3 val."
    assert "2015-10" in first["params"] and "209 000 km" in first["params"]
    assert "Kaunas" not in first["params"]
    assert first["image"].startswith("https://autoplius-img.dgn.lt/")
    assert result.structured["next_page"] == "https://autoplius.lt/skelbimai/naudoti-automobiliai?page_nr=2"
    assert "| 1 | [Volkswagen Passat]" in result.markdown


def test_autoplius_ad_hides_vin_and_keeps_facts():
    result = extract_autoplius(AD_URL, _read("autoplius_ad.html"))
    assert result and result.kind == "ad"
    ad = result.structured
    assert ad["id"] == "A33239618"
    assert ad["title"] == "2015 Volkswagen Passat"
    assert ad["price"] == 8700
    assert ad["location"] == "Kaunas, Lietuva"
    assert ad["category"] == ["Automobiliai", "Volkswagen", "Passat"]
    assert ad["attributes"]["Rida"] == "209 000 km"
    assert ad["attributes"]["Pavarų dėžė"] == "Automatinė"
    assert ad["attributes"]["Elektra nuvažiuojamas atstumas"] == "50 km"
    assert not any("VIN" in key or "SDK" in key for key in ad["attributes"])
    assert "WVWZZZ" not in result.markdown
    assert "Panoraminis stoglangis" in ad["description"]
    assert ad["seller"]["name"] == "MB APTRANS"
    assert ad["seller"]["identity_verified"] is True
    assert ad["images"] and all("ann_3_" in src for src in ad["images"])
    assert len(ad["images"]) == len(set(ad["images"]))
    assert not any("some-other-car" in src for src in ad["images"])


def test_unrelated_autoplius_page_is_none():
    assert extract_autoplius("https://autoplius.lt/", "<html><body><h1>Hi</h1></body></html>") is None


def test_page_structured_data():
    html = (
        '<html><head><meta property="og:title" content="T"/><meta property="og:price:amount" content="5"/>'
        '<script type="application/ld+json">{"@context":"https://schema.org","@graph":['
        '{"@type":"Product","name":"Lamp","offers":{"@type":"Offer","price":"12.5"}},'
        '{"@type":["Organization","Store"],"name":"Shop"}]}</script>'
        '<script type="application/ld+json">{broken</script></head><body></body></html>'
    )
    data = page_structured_data(html)
    assert data["types"] == ["Product", "Organization", "Store"]
    assert data["json_ld"][0]["offers"]["price"] == "12.5"
    assert data["open_graph"]["og:title"] == "T"
    assert page_structured_data("<html><body>plain</body></html>") is None


def test_compare_listings():
    old = {"1": {"id": "1", "price": 100}, "2": {"id": "2", "price": 50}, "3": {"id": "3", "price": None}}
    new = {"2": {"id": "2", "price": 40}, "3": {"id": "3", "price": None}, "4": {"id": "4", "price": 9}}
    diff = compare_listings(old, new)
    assert [i["id"] for i in diff["added"]] == ["4"]
    assert [i["id"] for i in diff["removed"]] == ["1"]
    assert diff["price_changed"][0]["old_price"] == 50 and diff["price_changed"][0]["new_price"] == 40


@pytest.mark.asyncio
async def test_watch_listing_over_two_runs(settings, tmp_path):
    settings = settings.model_copy(update={"data_dir": str(tmp_path), "allow_private_networks": True})
    state = {"html": _read("autoplius_listing.html")}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=state["html"], headers={"content-type": "text/html; charset=utf-8"})

    async with make_client(settings) as client:
        client._transport = httpx.MockTransport(handler)  # noqa: SLF001
        first = await watch_page(LIST_URL, settings=settings, client=client)
        assert first["first_run"] and first["count"] == 6 and first["saved"]
        assert "First snapshot saved" in first["report"]

        quiet = await watch_page(LIST_URL, settings=settings, client=client)
        assert not quiet["first_run"]
        assert quiet["added"] == [] and quiet["removed"] == [] and quiet["price_changed"] == []

        html = state["html"].replace("8 700", "7 900", 1)
        # Drop the last ad so one disappears.
        marker = 'class="announcement-item'
        last = html.rindex("<a " + marker) if "<a " + marker in html else html.rindex(marker)
        end = html.index("</a>", last) + 4
        state["html"] = html[:last] + html[end:]
        changed = await watch_page(LIST_URL, settings=settings, client=client, save=False)
        assert len(changed["removed"]) == 1
        assert changed["price_changed"][0]["new_price"] == 7900
        assert "Price:" in changed["report"] and "Gone:" in changed["report"]
        assert not changed["saved"]

        reset = await watch_page(LIST_URL, settings=settings, client=client, reset=True)
        assert reset["first_run"]


@pytest.mark.asyncio
async def test_watch_plain_page_diff(settings, tmp_path):
    settings = settings.model_copy(update={"data_dir": str(tmp_path), "allow_private_networks": True})
    body = {"text": "<html><body><h1>Prices</h1><p>Widget costs ten euros today for everyone here.</p></body></html>"}

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        return httpx.Response(200, text=body["text"], headers={"content-type": "text/html"})

    async with make_client(settings) as client:
        client._transport = httpx.MockTransport(handler)  # noqa: SLF001
        first = await watch_page("https://example.test/p", settings=settings, client=client)
        assert first["first_run"] and first["kind"] == "page"
        same = await watch_page("https://example.test/p", settings=settings, client=client)
        assert same["changed"] is False
        body["text"] = body["text"].replace("ten", "twelve")
        diff = await watch_page("https://example.test/p", settings=settings, client=client)
        assert diff["changed"] is True
        assert "twelve" in diff["diff"]


@pytest.mark.asyncio
async def test_watch_follows_next_page(settings, tmp_path):
    settings = settings.model_copy(update={"data_dir": str(tmp_path), "allow_private_networks": True})
    page1 = _read("autoplius_listing.html")
    page2 = page1.replace("33239618", "11111111").replace('rel="next"', 'rel="prev"')

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        text = page2 if "page_nr=2" in str(request.url) else page1
        return httpx.Response(200, text=text, headers={"content-type": "text/html"})

    import dungscraper.core.watch as watch_mod

    watch_mod.PAGE_DELAY_SECONDS = 0
    async with make_client(settings) as client:
        client._transport = httpx.MockTransport(handler)  # noqa: SLF001
        result = await watch_page(LIST_URL, settings=settings, client=client, pages=3)
    assert result["pages_read"] == 2
    assert result["count"] == 7  # six ads, plus the one ad whose id differs on page two


def test_cli_watch_help_and_mcp_tool_registered():
    from typer.testing import CliRunner

    from dungscraper.cli import app

    out = CliRunner().invoke(app, ["watch", "--help"])
    assert out.exit_code == 0 and "--pages" in out.output
    from dungscraper.mcp_server import build_mcp

    assert build_mcp() is not None
