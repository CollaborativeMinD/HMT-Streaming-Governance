"""Public facade for the bounded streaming-governance reference implementation."""
import asyncio as asyncio
import hashlib as hashlib
import time as time
from dataclasses import asdict as asdict

from governance_cdc import RabinCDCScanner as RabinCDCScanner
from governance_contracts import (
    GENESIS_HASH as GENESIS_HASH,
)
from governance_contracts import (
    Chunk as Chunk,
)
from governance_contracts import (
    GovernanceError as GovernanceError,
)
from governance_contracts import (
    HoldError as HoldError,
)
from governance_contracts import (
    LedgerRecord as LedgerRecord,
)
from governance_contracts import (
    QuarantineError as QuarantineError,
)
from governance_contracts import (
    Transaction as Transaction,
)
from governance_contracts import (
    block_material as block_material,
)
from governance_contracts import (
    canonical_json as canonical_json,
)
from governance_contracts import (
    sha256_text as sha256_text,
)
from governance_gateway import AgentGateway as AgentGateway
from governance_receipts import ReceiptFlow
from governance_state import EngineState
from governance_stream import chunk_payload as chunk_payload
from governance_stream import stream_to_ledger as stream_to_ledger
from governance_verification import VerificationBoundary
from governance_worker import WorkerLifecycle


class GovernanceLedgerEngine(EngineState, ReceiptFlow, WorkerLifecycle, VerificationBoundary):
    """Compose bounded state, admission, worker lifecycle, and verification seams.

    Small digests run inline; large hashes use one supervised thread job.
    Pending receipts grant no trust. Authoritative records never evict.
    """
