# Runtime token: cannot rotate keys, export private keys, issue tokens, or change ACLs.
path "transit/sign/ledger-checkpoints" { capabilities = ["update"] }
path "transit/keys/ledger-checkpoints" { capabilities = ["read"] }
path "transit/datakey/plaintext/ledger-cache" { capabilities = ["update"] }
