# Source and reference provenance

Reviewed upstream Tailcat and the two supplied design references to distinguish reusable custody/continuity patterns from inherited claims. Reference test counts are not counted as this repository's evidence.

| Input | Pinned identity | Use |
|---|---|---|
| [Tailcat](https://github.com/tailscale/tailcat/tree/v0.7.0) | v0.7.0, module checksums in `tailcat-gateway/go.sum` | Native WireGuard/DERP transport and authenticated `PeerEnv` node identity; explicit nonempty allowlist |
| Tailcat upstream reviewed head | `52fbad323e9d352f1febb623e0975368e9a8cbdd` | Reviewed newer admission/removal interfaces; runtime remains on the release pin |
| [Headscale](https://github.com/juanfont/headscale/releases/tag/v0.29.5) | v0.29.5, image digest in `lab.py` and Compose | Separate private management authority and verified-client DERP |
| [OpenBao](https://github.com/openbao/openbao/releases/tag/v2.7.1) | v2.7.1, image digest in `lab.py` and Compose | OSS transit keys/signatures, scoped runtime ACL, sealed persistent Raft |
| Supplied `tailcat_rendezvous_tracer_v0.1.0.zip` | SHA-256 `69c143be430648e9844467b385e9388b034ea4d2f5a3f6a881192643f4304a25` | ID-only cache hints, expiry/listener-closure distinction and limits on native claims |
| Supplied `tailcat-docker-agents-native-v0.2.0.zip` | SHA-256 `10068f624b3e3e768cbaad736dd4ce2745af0d27bf4a49d11a853044aa9552b6` | Durable authority, private TLS relay, distinct containers and explicit target qualification HOLDs |
| Go SDK used locally | go1.27.3 archive SHA-256 `65efbc80fdc1fa2cd2631cad5b481777a68e8725f34e89f50fc6ac714e379b40` | Native bridge build; CI obtains the same Go version using a pinned setup action |

The reference tracer's local HMAC/SQLite relay was not promoted into an authenticated transport. Headscale does not govern Tailcat's bypass control path; both authorities are explicitly implemented and tested. Redis is never a decision authority. OpenBao was selected because its open-source self-hosted transit/ACL/seal design fits the existing lab and avoids inventing cryptographic key custody in the ledger. No reference archive, private endpoint, private key, token, CA key or unseal share is published.

The initial Go 1.27.1 / upstream indirect-module audit found reachable HTTP/TLS advisories. The uplift updates Go to 1.27.3 and x/net to v0.61.0; both the initial scan and the current call-graph summary are retained. Module-only advisories are reported separately, never mislabeled as reached or silently suppressed. Govulncheck v1.9.0 is pinned; its JSON stream is explicitly checked because a zero process exit code alone is insufficient.
