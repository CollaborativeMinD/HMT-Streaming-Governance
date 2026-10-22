package DurableGovernance {
    private import ScalarValues::*;
    doc /* Durable profile v0.2. PostgreSQL is authoritative; Redis never grants trust.
       Tailcat peer keys and Headscale management enrollment are separate authorities.
       External SHA-256, TLS, WireGuard and transit signatures are allocated implementation services. */
    item def Transaction {
        attribute sessionId : String;
        attribute sequenceNumber : Integer;
        attribute sourceAgent : String;
        attribute targetAgent : String;
        attribute payload : String;
        attribute previousHash : String;
    }
    item def Envelope {
        item transaction : Transaction;
        attribute payloadHash : String;
        attribute ledgerHash : String;
    }
    item def Receipt { attribute id : String; attribute status : String; }
    port def FramePort { out item value : Transaction; }
    port def EnvelopePort { out item value : Envelope; }
    calc def ReleasePermitted {
        in postgresAvailable : Boolean;
        in peerCurrent : Boolean;
        in routeCurrent : Boolean;
        in sessionActive : Boolean;
        in recipientMatches : Boolean;
        in recordRegistered : Boolean;
        in hashesMatch : Boolean;
        return : Boolean = postgresAvailable and peerCurrent and routeCurrent and
            sessionActive and recipientMatches and recordRegistered and hashesMatch;
    }
    calc def EffectTransition {
        in oldState : String;
        in newState : String;
        return : Boolean =
            (oldState == "PREPARED" and (newState == "PREPARED" or newState == "DISPATCHING")) or
            (oldState == "DISPATCHING" and (newState == "DISPATCHING" or newState == "UNKNOWN" or newState == "COMPLETED" or newState == "FAILED_NO_EFFECT")) or
            (oldState == "UNKNOWN" and (newState == "UNKNOWN" or newState == "COMPLETED" or newState == "FAILED_NO_EFFECT")) or
            (oldState == "COMPLETED" and newState == "COMPLETED") or
            (oldState == "FAILED_NO_EFFECT" and newState == "FAILED_NO_EFFECT");
    }
    action def Admit { in item transactionInput : Transaction; out item receipt : Receipt; }
    action def VerifyAndAppend {
        doc /* One PostgreSQL transaction locks budget/session/registry/route, recomputes
           version-one payload/block identities, appends record and commits receipt/head/event.
           PENDING is durable but cannot authorize ingestion or external effects. */
        in item transactionInput : Transaction;
        out item envelope : Envelope;
    }
    action def Receive {
        in item envelope : Envelope;
        out release : Boolean;
        out quarantine : Boolean;
    }
    action def DispatchOnce {
        doc /* Persist PREPARED -> DISPATCHING claim before invoking a fixed host adapter.
           Unknown acknowledgement remains UNKNOWN or DISPATCHING across restart.
           Reconciliation is read-only and cannot authorize another dispatch. */
        in item envelope : Envelope;
        out effectState : String;
    }
    action def Hold { doc /* No release; do not infer malicious content from unavailable authority. */ }
    action def Quarantine { doc /* Persist a terminal integrity event and reject later use of this session. */ }
    action def Workflow {
        in item transactionInput : Transaction;
        out item receipt : Receipt;
        action admit : Admit;
        action validateAppend : VerifyAndAppend;
        action receive : Receive;
        action dispatch : DispatchOnce;
        action hold : Hold;
        action quarantine : Quarantine;
        bind admit.transactionInput = transactionInput;
        bind admit.receipt = receipt;
        bind validateAppend.transactionInput = transactionInput;
        flow from validateAppend.envelope to receive.envelope;
        flow from validateAppend.envelope to dispatch.envelope;
        first start then admit;
        then validateAppend;
        then receive;
        then decide;
            if receive.release then dispatch;
            if not receive.release and receive.quarantine then quarantine;
            if not receive.release and not receive.quarantine then hold;
        first dispatch then done;
        first quarantine then done;
        first hold then done;
    }
    part def Broker {
        port frames : ~FramePort;
        port envelopes : EnvelopePort;
    }
    part def AgentGateway {
        port frames : FramePort;
        port registered : ~EnvelopePort;
    }
    part def DurableSystem {
        part broker : Broker;
        part postgres;
        part encryptedRedis;
        part tailcatGateway : AgentGateway;
        part privateTailcatRelay;
        part headscaleManagement;
        part openBaoTransit;
        connect tailcatGateway.frames to broker.frames;
        connect broker.envelopes to tailcatGateway.registered;
        doc /* Broker and Tailcat gateway share only a private Unix socket. Agent containers
           join only the transport network; PostgreSQL/Redis have no agent-facing listener.
           Management ACLs do not authenticate Tailcat messages. */
    }
    requirement <'D01'> D01 { doc /* Durable pending work survives process loss. Status: EXECUTABLE. */ }
    requirement <'D02'> D02 { doc /* Verified identity and acknowledgement survive restart. Status: EXECUTABLE. */ }
    requirement <'D03'> D03 { doc /* Concurrent idempotent admissions create one record. Status: EXECUTABLE. */ }
    requirement <'D04'> D04 { doc /* An idempotency key cannot change payload. Status: EXECUTABLE. */ }
    requirement <'I01'> I01 { doc /* Version-one Unicode hashes bind metadata and payload atomically. Status: EXECUTABLE. */ }
    requirement <'I02'> I02 { doc /* Wrong lineage is quarantined without record insertion. Status: EXECUTABLE. */ }
    requirement <'I03'> I03 { doc /* Wrong recipient and tampered handoffs cannot execute. Status: EXECUTABLE. */ }
    requirement <'A01'> A01 { doc /* Sender identity is derived from the authenticated peer. Status: EXECUTABLE. */ }
    requirement <'A02'> A02 { doc /* Roles and routes are checked before admission. Status: EXECUTABLE. */ }
    requirement <'A03'> A03 { doc /* Fresh authority overrides a warm cache. Status: EXECUTABLE. */ }
    requirement <'A04'> A04 { doc /* Revocation between enqueue and commit blocks trust. Status: EXECUTABLE. */ }
    requirement <'A05'> A05 { doc /* Expired receipts cannot become verified. Status: EXECUTABLE. */ }
    requirement <'D05'> D05 { doc /* Broker cannot rewrite append-only records or provision identities. Status: EXECUTABLE. */ }
    requirement <'B01'> B01 { doc /* Capacity holds without evicting accepted records. Status: EXECUTABLE. */ }
    requirement <'C01'> C01 { doc /* Poisoned Redis falls back to registered PostgreSQL data. Status: EXECUTABLE. */ }
    requirement <'C02'> C02 { doc /* Cached metadata is checked against durable hashes. Status: EXECUTABLE. */ }
    requirement <'E01'> E01 { doc /* A durable record permits one bound host-adapter dispatch. Status: EXECUTABLE. */ }
    requirement <'E02'> E02 { doc /* Lost acknowledgement never authorizes automatic retry. Status: EXECUTABLE. */ }
    requirement <'E03'> E03 { doc /* Unbound external witnesses cannot report completion. Status: EXECUTABLE. */ }
    requirement <'K01'> K01 { doc /* Independent verification binds context and supports signer rotation. Status: EXECUTABLE. */ }
    requirement <'K02'> K02 { doc /* Runtime key policy cannot administer the key service. Status: EXECUTABLE. */ }
    requirement <'K03'> K03 { doc /* Seal and revocation stop key service access. Status: EXECUTABLE. */ }
    requirement <'N01'> N01 { doc /* Native Headscale enforces distinct agent and management ports. Status: EXECUTABLE. */ }
    requirement <'N02'> N02 { doc /* Native isolated Tailcat containers enforce peer identity and revocation. Status: EXECUTABLE. */ }
    requirement <'N03'> N03 { doc /* Agent container has no direct data-plane route or elevated host access. Status: EXECUTABLE. */ }
    requirement <'B02'> B02 { doc /* RPC schemas cannot override transport identity or select arbitrary execution. Status: EXECUTABLE. */ }
    requirement <'B03'> B03 { doc /* Request capacity and lifecycle remain bounded. Status: EXECUTABLE. */ }
    requirement <'B04'> B04 { doc /* JSON depth and exact byte limits reject before decoding. Status: EXECUTABLE. */ }
    requirement <'H01'> H01 { doc /* Hostile database-owner rollback requires an independent monotonic checkpoint anchor. Status: HOLD. */ }
    requirement <'H02'> H02 { doc /* On-prem WAN/NAT, partitions and reboot fencing need target-host qualification. Status: HOLD. */ }
    requirement <'H03'> H03 { doc /* Production PKI, multi-share unseal and workload-token enrollment need operator bootstrap. Status: HOLD. */ }
    requirement <'H04'> H04 { doc /* Rapid repeated one-shot Tailcat reconnection is not yet reliable in the local fixture; use persistent sessions. Status: HOLD. */ }
}
