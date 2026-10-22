"""Headscale control/ACL fixture. Tailcat's separate identity plane is never implied by enrollment."""

import json
import os
import sys
import time

import httpx

from lab import LAB, PINS, docker, private


def start():
    manifest = json.loads((LAB / "manifest.json").read_text())
    endpoints = json.loads((LAB / "endpoints.json").read_text())
    prefix = manifest["prefix"]
    address = endpoints["addresses"]["headscale"]
    name = prefix + "-headscale"
    config = dict(
        server_url=f"https://{address}:8443",
        listen_addr="0.0.0.0:8443",
        metrics_listen_addr="127.0.0.1:9090",
        noise=dict(private_key_path="/state/noise.key"),
        prefixes={"v4": "100.64.0.0/24", "v6": "fd7a:115c:a1e0::/48"},
        derp=dict(
            server=dict(
                enabled=True,
                region_id=998,
                region_code="hmt-headscale",
                region_name="Private management relay",
                verify_clients=True,
                stun_listen_addr="0.0.0.0:3478",
                private_key_path="/state/derp.key",
                automatically_add_embedded_derp_region=True,
                ipv4=address,
            ),
            urls=[],
            paths=[],
            auto_update_enabled=False,
        ),
        database=dict(
            type="sqlite", sqlite=dict(path="/state/control.db", write_ahead_log=True)
        ),
        tls_cert_path="/tls/headscale.crt",
        tls_key_path="/tls/headscale.key",
        unix_socket="/state/headscale.sock",
        policy=dict(mode="file", path="/config/policy.hujson"),
        disable_check_updates=True,
        dns=dict(
            magic_dns=False,
            base_domain="lab.hmt.invalid",
            override_local_dns=False,
            nameservers=dict(global_=[], split={}),
        ),
        log=dict(level="error", format="json"),
    )
    config["dns"]["nameservers"]["global"] = config["dns"]["nameservers"].pop("global_")
    policy = dict(
        tagOwners={
            tag: ["operator@lab.hmt.invalid"]
            for tag in ("tag:gov-admin", "tag:gov-agent", "tag:gov-service")
        },
        acls=[
            dict(
                action="accept",
                src=["tag:gov-admin"],
                dst=["tag:gov-service:8443", "tag:gov-service:9443"],
            ),
            dict(action="accept", src=["tag:gov-agent"], dst=["tag:gov-service:8443"]),
        ],
    )
    import yaml

    directory = LAB / "headscale-config"
    directory.mkdir(mode=0o755)
    directory.chmod(0o755)
    (directory / "config.yaml").write_text(yaml.safe_dump(config))
    (directory / "config.yaml").chmod(0o644)
    (directory / "policy.hujson").write_text(json.dumps(policy))
    (directory / "policy.hujson").chmod(0o644)
    mounts = []
    for file in ("headscale.crt", "headscale.key", "ca.crt"):
        mounts += [
            "--mount",
            f"type=bind,src={LAB / 'tls' / file},dst=/tls/{file},readonly",
        ]
    docker(
        "run",
        "-d",
        "--name",
        name,
        "--network",
        prefix+"-management",
        "--network-alias",
        "headscale",
        "--ip",
        address,
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--read-only",
        "--cap-drop",
        "ALL",
        "--security-opt",
        "no-new-privileges",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=16m",
        "--pids-limit",
        "128",
        "--memory",
        "256m",
        *mounts,
        "--mount",
        f"type=bind,src={directory},dst=/config,readonly",
        "--mount",
        f"type=bind,src={LAB / 'headscale-state'},dst=/state",
        PINS["headscale"],
        "serve",
        "--config",
        "/config/config.yaml",
    )
    manifest["containers"].append(name)
    private(LAB / "manifest.json", manifest)
    import ssl

    client = httpx.Client(
        verify=ssl.create_default_context(cafile=endpoints["ca"]),
        trust_env=False,
        timeout=2,
    )
    deadline = time.monotonic() + 20
    while True:
        try:
            if client.get(f"https://{address}:8443/health").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise ValueError("Headscale startup failed; inspect owned container logs")
        time.sleep(0.1)
    client.close()
    user = json.loads(
        docker(
            "exec",
            name,
            "headscale",
            "--config",
            "/config/config.yaml",
            "users",
            "create",
            "operator",
            "--email",
            "operator@lab.hmt.invalid",
            "--output",
            "json",
        )
    )
    keys = {}
    for purpose in ("service", "agent", "admin"):
        result = json.loads(
            docker(
                "exec",
                name,
                "headscale",
                "--config",
                "/config/config.yaml",
                "preauthkeys",
                "create",
                "--user",
                str(user["id"]),
                "--tags",
                "tag:gov-" + purpose,
                "--expiration",
                "1h",
                "--output",
                "json",
            )
        )
        keys[purpose] = result["key"]
    private(
        LAB / "headscale-runtime.json",
        dict(
            control_url=f"https://{address}:8443",
            state_dir=str(LAB / "tsnet"),
            auth_keys=keys,
        ),
    )
    print(
        "Private Headscale control, verified-client management DERP and tag policy are ready"
    )


if __name__ == "__main__":
    start()
