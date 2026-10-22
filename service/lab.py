"""Provision only named disposable local services; no on-premises deployment or public endpoint."""

import argparse
import datetime
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent
LAB = ROOT / ".lab"
PINS = {
    "postgres": "postgres:18@sha256:edb0c08dc33befd0262ab3b5cf1c4f3f8cfd8b09e88c05b45adee29d6761ddb6",
    "redis": "redis:8.2@sha256:e5f23377739f95a2f530b9c99f93accb4f634577f09d00a7d8e0638f2fdf69e0",
    "headscale": "ghcr.io/juanfont/headscale:0.29.5@sha256:780ed2f9e85719a2211277433f2c8afb45105d0d25031949a6848a485f9e216d",
    "bao": "quay.io/openbao/openbao:2.7.1@sha256:6d2b93856e3fcf7b18ad855a0b51eaba474dc8b79cf554379ea32034797d2acf",
}


def docker(*args, check=True):
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "DOCKER_HOST",
            "DOCKER_CONTEXT",
            "DOCKER_TLS",
            "DOCKER_TLS_VERIFY",
            "DOCKER_CERT_PATH",
        }
    }
    return subprocess.run(
        ["docker", "--host=unix:///var/run/docker.sock", *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=60,
        check=check,
    ).stdout.strip()


def private(path, value):
    path.write_text(
        json.dumps(value, indent=2) + "\n" if not isinstance(value, str) else value
    )
    path.chmod(0o600)


def certificates(addresses):
    import ipaddress
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import NameOID

    tls = LAB / "tls"
    tls.mkdir(mode=0o755)
    tls.chmod(0o755)
    now = datetime.datetime.now(datetime.timezone.utc)
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name(
        [x509.NameAttribute(NameOID.COMMON_NAME, "Disposable HMT lab root")]
    )
    ca = (
        x509.CertificateBuilder()
        .subject_name(name)
        .issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(minutes=1))
        .not_valid_after(now + datetime.timedelta(days=2))
        .add_extension(x509.BasicConstraints(ca=True, path_length=1), critical=True)
        .sign(key, hashes.SHA256())
    )
    (tls / "ca.crt").write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (tls / "ca.crt").chmod(0o644)
    for service in ("postgres", "redis", "bao", "headscale", "relay"):
        child = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        cert = (
            x509.CertificateBuilder()
            .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, service)]))
            .issuer_name(name)
            .public_key(child.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=1))
            .not_valid_after(now + datetime.timedelta(days=2))
            .add_extension(
                x509.SubjectAlternativeName(
                    [
                        x509.DNSName(service),
                        x509.DNSName("localhost"),
                        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
                        x509.IPAddress(ipaddress.ip_address(addresses[service])),
                    ]
                ),
                False,
            )
            .sign(key, hashes.SHA256())
        )
        (tls / (service + ".crt")).write_bytes(
            cert.public_bytes(serialization.Encoding.PEM)
        )
        (tls / (service + ".key")).write_bytes(
            child.private_bytes(
                serialization.Encoding.PEM,
                serialization.PrivateFormat.PKCS8,
                serialization.NoEncryption(),
            )
        )
        # The enclosing task-private directory is 0700; only explicitly mounted service containers see these fixtures.
        (tls / (service + ".key")).chmod(0o644)
    # The disposable root private key is never persisted; target PKI must be independently bootstrapped.
    return tls


def port(container, target):
    mapping = json.loads(docker("inspect", container))[0]["NetworkSettings"]["Ports"][
        str(target) + "/tcp"
    ]
    return int(mapping[0]["HostPort"])


def start():
    if LAB.exists():
        raise ValueError(
            "Lab already exists: use status or explicitly stop the owned lab"
        )
    LAB.mkdir(mode=0o700)
    prefix = "hmt-gov-lab-" + secrets.token_hex(4)
    manifest = dict(prefix=prefix, containers=[], network=prefix, networks=[prefix,prefix+"-transport",prefix+"-management"], images=PINS)
    private(LAB / "manifest.json", manifest)
    docker("network", "create", "--internal", "--subnet", "172.30.245.0/24", prefix)
    for suffix, subnet in (("-transport","172.30.246.0/24"),("-management","172.30.247.0/24")):
        docker("network","create","--internal","--subnet",subnet,"--ip-range",subnet.replace(".0/24",".128/25"),prefix+suffix)
    import ipaddress

    subnet = json.loads(docker("network", "inspect", prefix))[0]["IPAM"]["Config"][0][
        "Subnet"
    ]
    base = ipaddress.ip_network(subnet).network_address
    addresses = {
        name: str(base + index)
        for index, name in enumerate(
            ("postgres", "redis", "bao", "headscale", "relay"), 2
        )
    }
    addresses["headscale"] = "172.30.247.2"
    addresses["relay"] = "172.30.246.2"
    tls = certificates(addresses)
    password = secrets.token_urlsafe(32)
    (LAB / "pg-password").write_text(password)
    (LAB / "pg-password").chmod(0o644)
    for name in ("pgdata", "headscale-state", "bao-state"):
        (LAB / name).mkdir(mode=0o755 if name == "pgdata" else 0o700)
        (LAB / name).chmod(0o755 if name == "pgdata" else 0o700)
    mounts = {
        name: [
            item
            for file in ("ca.crt", name + ".crt", name + ".key")
            for item in (
                "--mount",
                f"type=bind,src={tls / file},dst=/tls/{file},readonly",
            )
        ]
        for name in ("postgres", "redis", "bao")
    }
    mounts["redis"] += [
        "--mount",
        f"type=bind,src={tls / 'redis.acl'},dst=/tls/redis.acl,readonly",
    ]
    common = [
        "--network",
        prefix,
        "--security-opt",
        "no-new-privileges",
        "--read-only",
        "--tmpfs",
        "/tmp:rw,noexec,nosuid,size=64m",
    ]
    pg = prefix + "-postgres"
    command = "cp /tls/postgres.key /tmp/server.key; chown postgres:postgres /tmp/server.key; chmod 600 /tmp/server.key; exec docker-entrypoint.sh postgres -c ssl=on -c ssl_cert_file=/tls/postgres.crt -c ssl_key_file=/tmp/server.key -c listen_addresses='*'"
    docker(
        "run",
        "-d",
        "--name",
        pg,
        "--ip",
        addresses["postgres"],
        "--user",
        "0:0",
        "--network-alias",
        "postgres",
        *common,
        *mounts["postgres"],
        "--mount",
        f"type=bind,src={LAB / 'pg-password'},dst=/password,readonly",
        "--mount",
        f"type=bind,src={LAB / 'pgdata'},dst=/var/lib/postgresql",
        "--tmpfs",
        "/var/run/postgresql:rw,size=4m",
        "-e",
        "POSTGRES_PASSWORD_FILE=/password",
        "-e",
        "POSTGRES_DB=governance",
        PINS["postgres"],
        "sh",
        "-ec",
        command,
    )
    manifest["containers"].append(pg)
    private(LAB / "manifest.json", manifest)
    redis_pw = secrets.token_urlsafe(32)
    (tls / "redis.acl").write_text(
        "user default off\nuser cache on >"
        + redis_pw
        + " ~hmt:cache:* +ping +get +set +del +pttl\n"
    )
    (tls / "redis.acl").chmod(0o644)
    for certificate in tls.glob("*.crt"):
        certificate.chmod(0o644)
    redis_name = prefix + "-redis"
    docker(
        "run",
        "-d",
        "--name",
        redis_name,
        "--ip",
        addresses["redis"],
        "--network-alias",
        "redis",
        *common,
        *mounts["redis"],
        "--cap-drop",
        "ALL",
        "--user",
        "999:999",
        PINS["redis"],
        "redis-server",
        "--port",
        "0",
        "--tls-port",
        "6379",
        "--tls-cert-file",
        "/tls/redis.crt",
        "--tls-key-file",
        "/tls/redis.key",
        "--tls-ca-cert-file",
        "/tls/ca.crt",
        "--tls-auth-clients",
        "no",
        "--aclfile",
        "/tls/redis.acl",
        "--save",
        "",
        "--appendonly",
        "no",
        "--maxmemory",
        "64mb",
        "--maxmemory-policy",
        "allkeys-lru",
    )
    manifest["containers"].append(redis_name)
    private(LAB / "manifest.json", manifest)
    (LAB / "bao.hcl").write_text(
        'ui = false\ndisable_mlock = true\napi_addr = "https://bao:8200"\ncluster_addr = "https://bao:8201"\nlistener "tcp" {\n address = "0.0.0.0:8200"\n tls_cert_file = "/tls/bao.crt"\n tls_key_file = "/tls/bao.key"\n}\nstorage "raft" {\n path = "/bao/data"\n node_id = "lab-1"\n}\n'
    )
    bao = prefix + "-bao"
    docker(
        "run",
        "-d",
        "--name",
        bao,
        "--ip",
        addresses["bao"],
        "--network-alias",
        "bao",
        *common,
        *mounts["bao"],
        "--cap-drop",
        "ALL",
        "--user",
        f"{os.getuid()}:{os.getgid()}",
        "--mount",
        f"type=bind,src={LAB / 'bao.hcl'},dst=/config/bao.hcl,readonly",
        "--mount",
        f"type=bind,src={LAB / 'bao-state'},dst=/bao/data",
        PINS["bao"],
        "server",
        "-config=/config/bao.hcl",
    )
    manifest["containers"].append(bao)
    private(LAB / "manifest.json", manifest)
    endpoints = dict(
        prefix=prefix,
        postgres_port=5432,
        redis_port=6379,
        bao_port=8200,
        addresses=addresses,
        pg_password=password,
        redis_password=redis_pw,
        ca=str(tls / "ca.crt"),
    )
    private(LAB / "endpoints.json", endpoints)
    prepare(endpoints)
    print(
        "Disposable PostgreSQL/Redis/OpenBao lab ready; credentials remain in service/.lab (excluded)."
    )


def prepare(endpoints):
    import ssl
    import httpx
    import psycopg
    from psycopg.conninfo import make_conninfo
    from urllib.parse import quote

    dsn = make_conninfo(
        host=endpoints["addresses"]["postgres"],
        port=endpoints["postgres_port"],
        dbname="governance",
        user="postgres",
        password=endpoints["pg_password"],
        sslmode="verify-full",
        sslrootcert=endpoints["ca"],
        connect_timeout=1,
    )
    deadline = time.monotonic() + 30
    while True:
        try:
            with psycopg.connect(dsn) as conn:
                break
        except psycopg.Error:
            if time.monotonic() > deadline:
                raise ValueError(
                    "PostgreSQL startup failed; private lab logs available"
                ) from None
            time.sleep(0.1)
    from hmt_governance.database import migrate
    import asyncio

    asyncio.run(migrate(dsn))
    app_pw = secrets.token_urlsafe(32)
    with psycopg.connect(dsn) as conn:
        from psycopg import sql

        conn.execute(
            sql.SQL("CREATE ROLE gov_broker LOGIN PASSWORD {}").format(
                sql.Literal(app_pw)
            )
        )
        conn.execute("GRANT USAGE ON SCHEMA governance TO gov_broker")
        conn.execute("GRANT SELECT ON ALL TABLES IN SCHEMA governance TO gov_broker")
        conn.execute(
            "GRANT INSERT ON governance.sessions,governance.receipts,governance.records,governance.events,governance.effects TO gov_broker"
        )
        conn.execute("GRANT UPDATE(state,reason) ON governance.sessions TO gov_broker")
        conn.execute("GRANT UPDATE(sessions) ON governance.budget TO gov_broker")
        conn.execute("GRANT UPDATE(status,result) ON governance.receipts TO gov_broker")
        conn.execute(
            "GRANT UPDATE(state,claim,result,updated_at) ON governance.effects TO gov_broker"
        )
        conn.execute(
            "GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA governance TO gov_broker"
        )
        conn.execute(
            "GRANT EXECUTE ON FUNCTION governance.chain_guard(),governance.lock_agents(text[]),governance.lock_route(text,text) TO gov_broker"
        )
    runtime = make_conninfo(
        host=endpoints["addresses"]["postgres"],
        port=endpoints["postgres_port"],
        dbname="governance",
        user="gov_broker",
        password=app_pw,
        sslmode="verify-full",
        sslrootcert=endpoints["ca"],
        connect_timeout=2,
    )
    url = f"https://{endpoints['addresses']['bao']}:{endpoints['bao_port']}"
    client = httpx.Client(
        base_url=url,
        verify=ssl.create_default_context(cafile=endpoints["ca"]),
        trust_env=False,
        timeout=15,
    )
    while True:
        try:
            if client.get("/v1/sys/seal-status").status_code == 200:
                break
        except httpx.HTTPError:
            pass
        if time.monotonic() > deadline:
            raise ValueError("OpenBao startup failed")
        time.sleep(0.1)
    init = client.post(
        "/v1/sys/init", json=dict(secret_shares=1, secret_threshold=1)
    ).json()
    private(
        LAB / "bao-admin.json",
        dict(root_token=init["root_token"], unseal_key=init["keys_base64"][0]),
    )
    client.post(
        "/v1/sys/unseal", json=dict(key=init["keys_base64"][0])
    ).raise_for_status()
    ready_deadline = time.monotonic() + 20
    while client.get("/v1/sys/health").status_code != 200:
        if time.monotonic() > ready_deadline:
            raise ValueError("OpenBao leadership not ready")
        time.sleep(0.2)
    client.headers["X-Vault-Token"] = init["root_token"]
    client.post("/v1/sys/mounts/transit", json=dict(type="transit")).raise_for_status()
    client.post(
        "/v1/transit/keys/ledger-checkpoints", json=dict(type="ed25519")
    ).raise_for_status()
    client.post(
        "/v1/transit/keys/ledger-cache", json=dict(type="aes256-gcm96")
    ).raise_for_status()
    policy = 'path "transit/sign/ledger-checkpoints" { capabilities = ["update"] }\npath "transit/keys/ledger-checkpoints" { capabilities = ["read"] }\npath "transit/datakey/plaintext/ledger-cache" { capabilities = ["update"] }'
    client.put(
        "/v1/sys/policies/acl/governance-runtime", json=dict(policy=policy)
    ).raise_for_status()
    token = client.post(
        "/v1/auth/token/create",
        json=dict(
            policies=["governance-runtime"],
            no_default_policy=True,
            ttl="1h",
            renewable=True,
        ),
    ).json()["auth"]["client_token"]
    private(
        LAB / "bao-admin.json",
        dict(root_token=init["root_token"], unseal_key=init["keys_base64"][0]),
    )
    private(LAB / "bao-runtime.json", dict(url=url, token=token, ca=endpoints["ca"]))
    client.close()
    private(LAB / "admin.json", dict(dsn=dsn))
    redis_url = f"rediss://cache:{quote(endpoints['redis_password'])}@{endpoints['addresses']['redis']}:{endpoints['redis_port']}/0?ssl_ca_certs={quote(endpoints['ca'], safe='')}&ssl_check_hostname=true"
    private(LAB / "runtime.json", dict(dsn=runtime, redis_url=redis_url))


def stop():
    manifest = json.loads((LAB / "manifest.json").read_text())
    prefix = manifest["prefix"]
    if not re.fullmatch(r"hmt-gov-lab-[0-9a-f]{8}", prefix):
        raise ValueError("Unowned lab")
    created = docker(
        "ps",
        "-a",
        "--filter",
        "name=^" + prefix + "-",
        "--format",
        "{{.Names}}",
        check=False,
    ).splitlines()
    for name in set(manifest["containers"]) | set(created):
        if not name.startswith(prefix + "-"):
            raise ValueError("Unowned container")
        docker("rm", "-f", name, check=False)
    for network in manifest.get("networks",[prefix]):
        if network not in (prefix,prefix+"-transport",prefix+"-management"):
            raise ValueError("Unowned network")
        docker("network", "rm", network, check=False)
    # DB data may be owned by container UID 999; remove it using a narrowly mounted cleanup container.
    docker(
        "run",
        "--rm",
        "--network",
        "none",
        "--user",
        "0:0",
        "--mount",
        f"type=bind,src={LAB},dst=/owned",
        PINS["redis"],
        "sh",
        "-ec",
        "rm -rf /owned/pgdata /owned/headscale-state /owned/bao-state",
    )
    shutil.rmtree(LAB)
    print("Owned disposable lab removed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=["start", "stop"])
    args = parser.parse_args()
    start() if args.operation == "start" else stop()
