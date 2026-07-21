import pytest
from radcliffe_control.job_egress_proxy import (
    ProxyRequestError,
    filtered_headers,
    public_addresses,
    split_authority,
)


def test_proxy_accepts_only_uniformly_public_destinations() -> None:
    assert public_addresses(["93.184.216.34", "2606:2800:220:1:248:1893:25c8:1946"])
    for addresses in (
        ["127.0.0.1"],
        ["10.0.0.1"],
        ["169.254.169.254"],
        ["93.184.216.34", "192.168.1.1"],
        [],
    ):
        with pytest.raises(ProxyRequestError):
            public_addresses(addresses)


def test_proxy_restricts_ports_and_strips_proxy_credentials() -> None:
    assert split_authority("example.com", 443) == ("example.com", 443)
    assert split_authority("[2606:2800:220:1:248:1893:25c8:1946]:80", 80)[1] == 80
    with pytest.raises(ProxyRequestError):
        split_authority("example.com:22", 443)

    filtered = filtered_headers(
        [
            b"Host: example.com\r\n",
            b"Proxy-Authorization: private\r\n",
            b"Proxy-Connection: keep-alive\r\n",
        ]
    )
    assert b"Host: example.com" in filtered
    assert b"private" not in filtered
    assert b"Connection: close" in filtered
