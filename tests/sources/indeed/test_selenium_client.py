from __future__ import annotations

from visa_jobs_api.sources.indeed.selenium_client import ProxyConfig


def test_proxy_config_holds_host_and_port() -> None:
    proxy = ProxyConfig(host="gw.example.com", port=823)

    assert proxy.host == "gw.example.com"
    assert proxy.port == 823
