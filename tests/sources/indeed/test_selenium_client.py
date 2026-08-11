from __future__ import annotations

import json
import os
import shutil

from visa_jobs_api.sources.indeed.selenium_client import ProxyConfig, _build_proxy_extension


def test_proxy_config_holds_host_port_and_credentials() -> None:
    proxy = ProxyConfig(host="gw.example.com", port=823, username="user", password="pass")

    assert proxy.host == "gw.example.com"
    assert proxy.port == 823
    assert proxy.username == "user"
    assert proxy.password == "pass"


def test_build_proxy_extension_writes_a_valid_manifest() -> None:
    proxy = ProxyConfig(host="gw.example.com", port=823, username="user", password="pass")
    extension_dir = _build_proxy_extension(proxy)
    try:
        with open(f"{extension_dir}/manifest.json") as f:
            manifest = json.load(f)
        assert manifest["manifest_version"] == 2
        assert os.path.exists(f"{extension_dir}/background.js")
    finally:
        shutil.rmtree(extension_dir, ignore_errors=True)


def test_build_proxy_extension_embeds_proxy_details_with_no_country_suffix() -> None:
    proxy = ProxyConfig(host="gw.example.com", port=823, username="user", password="pass")
    extension_dir = _build_proxy_extension(proxy)
    try:
        with open(f"{extension_dir}/background.js") as f:
            background_js = f.read()
        assert "gw.example.com" in background_js
        assert "823" in background_js
        # Deliberately no "__cr.<code>" country-targeting suffix -- this
        # app doesn't try to match the proxy's exit country to the domain
        # being scraped, see module docstring.
        assert '"user"' in background_js
        assert "__cr." not in background_js
        assert "pass" in background_js
    finally:
        shutil.rmtree(extension_dir, ignore_errors=True)


def test_build_proxy_extension_creates_a_fresh_directory_each_call() -> None:
    proxy = ProxyConfig(host="gw.example.com", port=823, username="user", password="pass")
    dir_a = _build_proxy_extension(proxy)
    dir_b = _build_proxy_extension(proxy)
    try:
        assert dir_a != dir_b
    finally:
        shutil.rmtree(dir_a, ignore_errors=True)
        shutil.rmtree(dir_b, ignore_errors=True)
