from __future__ import annotations

import httpx
import respx

from paper_mcp.pipelines.unpaywall import open_access_urls

_URL = "https://api.unpaywall.org/v2/10.5555/x"


@respx.mock
async def test_returns_best_location_first_then_others() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "best_oa_location": {"url_for_pdf": "https://best.example/p.pdf"},
                "oa_locations": [
                    {"url_for_pdf": "https://best.example/p.pdf"},
                    {"url_for_pdf": "https://other.example/p.pdf"},
                    {"url": "https://landing.example/abs"},
                ],
            },
        )
    )

    urls = await open_access_urls("10.5555/x", email="a@b.c")

    assert urls[0] == "https://best.example/p.pdf"
    assert "https://other.example/p.pdf" in urls
    assert "https://landing.example/abs" in urls
    assert len(urls) == len(set(urls))  # deduplicated


@respx.mock
async def test_sends_the_contact_email_unpaywall_requires() -> None:
    route = respx.get(_URL).mock(return_value=httpx.Response(200, json={}))

    await open_access_urls("10.5555/x", email="a@b.c")

    assert route.calls.last.request.url.params["email"] == "a@b.c"


@respx.mock
async def test_upstream_failure_yields_no_urls_rather_than_raising() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(404))

    # "No open access" is a normal answer, not an error.
    assert await open_access_urls("10.5555/x", email="a@b.c") == []


@respx.mock
async def test_transport_error_yields_no_urls() -> None:
    respx.get(_URL).mock(side_effect=httpx.ConnectError("dns"))

    assert await open_access_urls("10.5555/x", email="a@b.c") == []


@respx.mock
async def test_closed_access_paper_yields_no_urls() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200, json={"best_oa_location": None, "oa_locations": []}
        )
    )

    assert await open_access_urls("10.5555/x", email="a@b.c") == []
