package StreamingGovernance {
    private import ScalarValues::*;

    doc /* Functional reference model for governance_core.py.
       Payloads are UTF-8 strings; CDC chunks are byte sequences encoded as
       base64 in canonical JSON. previousHash names a verified BLOCK hash.
       SHA-256 and durable storage are allocated external implementation
       functions, not cryptographic algorithms implemented in this model. */

    attribute def Digest :> String;

    item def RawPacket {
        attribute bytesBase64 : String;
        attribute byteLength : Integer;
    }
    item def ContentChunk {
        attribute chunkIndex : Integer;
        attribute startByte : Integer;
        attribute endByte : Integer;
        attribute bytesBase64 : String;
        attribute fingerprint : Integer;
    }
    item def Transaction {
        attribute sessionId : String;
        attribute sequenceNumber : Integer;
        attribute sourceAgent : String;
        attribute targetEntity : String;
        attribute payload : String;
        attribute previousHash : Digest;
    }
    item def QueueReceipt {
        attribute trackingToken : String;
        attribute pending : Boolean;
    }
    item def VerifiedRecord {
        item transaction : Transaction;
        attribute payloadHash : Digest;
        attribute blockHash : Digest;
        attribute verified : Boolean;
    }
    item def AssuranceResult {
        attribute sequenceValid : Boolean;
        attribute predecessorValid : Boolean;
        attribute payloadValid : Boolean;
        attribute sessionActive : Boolean;
        attribute availabilityValid : Boolean;
        attribute deadlineValid : Boolean;
        attribute capacityValid : Boolean;
        attribute operational : Boolean = availabilityValid and deadlineValid and capacityValid;
        attribute allValid : Boolean = operational and sequenceValid and predecessorValid and
            payloadValid and sessionActive;
    }

    port def PacketPort { out item packet : RawPacket; }
    port def ChunkPort { out item chunk : ContentChunk; }
    port def TransactionPort { out item transaction : Transaction; }
    port def RecordPort { out item record : VerifiedRecord; }

    calc def NextSequence {
        in headSequence : Integer;
        return : Integer = headSequence + 1;
    }
    calc def SequenceMatches {
        in incomingSequence : Integer;
        in headSequence : Integer;
        return : Boolean = incomingSequence == headSequence + 1;
    }
    calc def BoundaryEligible {
        in chunkBytes : Integer;
        in minimumBytes : Integer;
        in maximumBytes : Integer;
        in remainder : Integer;
        in target : Integer;
        return : Boolean = chunkBytes >= minimumBytes and
            (remainder == target or chunkBytes >= maximumBytes);
    }
    calc def TrustDecision {
        in sequenceValid : Boolean;
        in predecessorValid : Boolean;
        in payloadValid : Boolean;
        in sessionActive : Boolean;
        return : Boolean = sequenceValid and predecessorValid and
            payloadValid and sessionActive;
    }
    calc def VerificationAvailable {
        in engineOpen : Boolean;
        in workerAlive : Boolean;
        in engineUnheld : Boolean;
        in withinCapacity : Boolean;
        return : Boolean = engineOpen and workerAlive and engineUnheld and withinCapacity;
    }

    action def ScanPacket {
        doc /* RabinCDCScanner.feed: base 256 modulo 2^61-1;
           continuing 64-byte rolling window, minimum and maximum cut sizes.
           finish() emits a nonempty final tail. Packet boundaries do not cut. */
        in item packet : RawPacket;
        out item chunks : ContentChunk[0..*];
    }
    action def AdmitTransaction {
        doc /* put_nowait returns pending or busy. A busy admission MUST be
           retried without advancing the session sequence; it is not a commit. */
        in item transaction : Transaction;
        out item receipt : QueueReceipt;
    }
    action def FrameChunk {
        doc /* Serialize base64 chunk data with byte offsets. Wait for the
           verified session head, then use head.sequence+1 and head.blockHash.
           Before any commit use sequence 0 and the 64-zero genesis parent. */
        in item chunk : ContentChunk;
        out item transaction : Transaction;
    }
    action def VerifyTransaction {
        doc /* Background worker checks exact next sequence, predecessor,
           optional expected payload hash, and active session. It offloads
           payloads >= 65536 bytes to one thread job for both hashes.
           Small hashes remain inline. Recheck quarantine and the absolute
           monotonic transaction deadline before ledger insertion. */
        in item transaction : Transaction;
        out item assurance : AssuranceResult;
    }
    action def AppendVerifiedRecord {
        doc /* Append a frozen record; never overwrite an existing sequence.
           Update the session head only after successful verification. */
        in item transaction : Transaction;
        out item record : VerifiedRecord;
    }
    action def VerifyHandoff {
        doc /* governance_verify_payload plus committed envelope comparison,
           expected session, intended target, and active-session checks.
           Check VerificationAvailable before and after the hashing await.
           Closed engine, verifier fault or cancellation gives HOLD and clears
           receiver context; it cannot release an already committed envelope. */
        in item record : VerifiedRecord;
        out permitted : Boolean;
        out operational : Boolean;
    }
    action def ExecuteTool {
        in item record : VerifiedRecord;
    }
    action def QuarantineSession {
        doc /* Halt execution, clear receiver working context, emit
           SYSTEM_QUARANTINE, reject subsequent session submissions. */
    }
    action def HoldSession {
        doc /* Availability, worker failure, capacity exhaustion, cancellation,
           or deadline loss grants no trust. Halt release without classifying
           the input as malicious. No tool retry or automatic recovery. */
    }
    action def CloseEngine {
        doc /* A single shielded task owns bounded queue drain and worker
           cancellation. All concurrent close callers await that same task.
           Caller cancellation cannot abandon cleanup. Closing denies new
           verification immediately while already admitted queue work drains. */
    }

    action def ProcessQueuedTransaction {
        in item transaction : Transaction;
        first start;
        then action checkTransaction : VerifyTransaction {
            in item transaction = ProcessQueuedTransaction::transaction;
        }
        then decide;
            if checkTransaction.assurance.allValid then appendRecord;
            if not checkTransaction.assurance.operational then holdWork;
            if checkTransaction.assurance.operational and
                not checkTransaction.assurance.allValid then quarantine;

        action appendRecord : AppendVerifiedRecord {
            in item transaction = ProcessQueuedTransaction::transaction;
        }
        then gate;
        flow from appendRecord.record to gate.record;

        action gate : VerifyHandoff;
        then decide;
            if gate.operational and gate.permitted then execute;
            if not gate.operational then holdWork;
            if gate.operational and not gate.permitted then quarantine;
        flow from appendRecord.record to execute.record;

        action execute : ExecuteTool;
        then done;

        action quarantine : QuarantineSession;
        then done;
        action holdWork : HoldSession;
        then done;
    }

    attribute def SubmitSignal;
    attribute def CommitSignal;
    attribute def MismatchSignal;
    attribute def DeliveryVerifiedSignal;
    attribute def HoldSignal;

    state def TransactionLifecycle {
        first start then ready;
        state ready;
        state pending;
        state verified;
        state delivered;
        state quarantined;
        state held;

        transition enqueue first ready accept SubmitSignal then pending;
        transition commit first pending accept CommitSignal then verified;
        transition reject first pending accept MismatchSignal then quarantined;
        transition deliver first verified accept DeliveryVerifiedSignal then delivered;
        transition corrupt first verified accept MismatchSignal then quarantined;
        transition pendingHold first pending accept HoldSignal then held;
        transition verifiedHold first verified accept HoldSignal then held;
        doc /* Quarantined is terminal in this reference lifecycle. New work
           requires a new session. Each transaction has its own lifecycle. */
    }

    part def StreamingCDC {
        attribute windowBytes : Integer = 64;
        attribute base : Integer = 256;
        attribute nominalBytes : Integer = 512;
        attribute minimumBytes : Integer = 128;
        attribute maximumBytes : Integer = 2048;
        port inputPackets : ~PacketPort;
        port outputChunks : ChunkPort;
        perform action scan : ScanPacket;
        assert constraint configurationValid {
            windowBytes > 0 and windowBytes <= minimumBytes and
            minimumBytes <= maximumBytes
        }
    }
    part def BoundedQueue {
        attribute capacity : Integer = 64;
        attribute maximumPendingBytes : Integer = 4000000;
        port incoming : ~TransactionPort;
        port outgoing : TransactionPort;
        perform action admit : AdmitTransaction;
    }
    part def ChunkSequencer {
        attribute stagingCapacity : Integer = 8;
        attribute maximumPacketBytes : Integer = 65536;
        attribute streamDeadlineSeconds : Real = 30.0;
        port incoming : ~ChunkPort;
        port verifiedHeads : ~RecordPort;
        port outgoing : TransactionPort;
        perform action frameChunk : FrameChunk;
    }
    part def GovernanceWorker {
        attribute maximumRecords : Integer = 10000;
        attribute maximumSessions : Integer = 1024;
        attribute receiptCacheCapacity : Integer = 1024;
        attribute eventCacheCapacity : Integer = 1024;
        attribute inlineHashCutoffBytes : Integer = 65536;
        attribute transactionDeadlineSeconds : Real = 10.0;
        attribute hashTimeoutSeconds : Real = 5.0;
        port incoming : ~TransactionPort;
        port outgoing : RecordPort;
        perform action process : ProcessQueuedTransaction;
        perform action close : CloseEngine;
    }
    part def AgentBoundary {
        attribute maximumContextEntries : Integer = 64;
        port incoming : ~RecordPort;
        perform action gate : VerifyHandoff;
        perform action invoke : ExecuteTool;
    }
    part def GovernancePipeline {
        part scanner : StreamingCDC;
        part sequencer : ChunkSequencer;
        part queue : BoundedQueue;
        part worker : GovernanceWorker;
        part boundary : AgentBoundary;
        connect scanner.outputChunks to sequencer.incoming;
        connect sequencer.outgoing to queue.incoming;
        connect queue.outgoing to worker.incoming;
        connect worker.outgoing to boundary.incoming;
        connect worker.outgoing to sequencer.verifiedHeads;
        doc /* A sequencer wraps each scanned chunk in a transaction after
           the prior verified receipt supplies previousHash. Scanning may
           continue into a bounded staging queue; admission never grants trust. */
    }

    requirement <'CDC-01'> packetIndependent {
        doc /* Equal ordered bytes shall produce equal chunk boundaries for
           every transport packetization and reconstruct the exact byte stream. */
    }
    requirement <'GOV-01'> strictLineage {
        doc /* Commit only sequence=head+1 with previousHash=head.blockHash;
           genesis uses sequence 0 and 64 zeroes. Never overwrite a sequence. */
    }
    requirement <'GOV-02'> verifyBeforeExecute {
        doc /* Invoke a tool only after registered payload, envelope, session,
           and destination verification; pending receipts do not authorize it. */
    }
    requirement <'GOV-03'> failClosed {
        doc /* Integrity or lineage failure halts ingestion, clears working
           context, emits SYSTEM_QUARANTINE and makes the session terminal. */
    }
    requirement <'GOV-04'> boundedAdmission {
        doc /* Queue overflow returns busy without dropping an accepted frame
           or advancing a sequence. Worker accounting completes on all paths. */
    }
    requirement <'GOV-05'> availabilityIsNotIntegrity {
        doc /* Availability loss produces HOLD; integrity mismatch produces
           quarantine. Neither path grants trust or automatically retries tools. */
    }
    requirement <'GOV-06'> boundedRetention {
        doc /* Bound receipt and event caches, session count, pending bytes and
           context references. Refuse ledger-capacity exhaustion; never evict
           verified records to create admission capacity. */
    }
    requirement <'GOV-07'> snapshotBeforeAwait {
        doc /* Invoke the tool with the immutable transaction payload captured
           before asynchronous verification. Caller mutation cannot swap bytes. */
    }
    requirement <'GOV-08'> streamBackpressure {
        doc /* Concurrent scanning and sequencing use bounded staging, close
           sources on every exit, cancel siblings on failure, and never retain
           the entire stream or replay a failed sink operation. */
    }
    requirement <'VV-01'> lifecycleOwnership {
        doc /* Concurrent shutdown and cancellation shall retain one cleanup
           owner, settle receipts and release pending accounting. */
    }
    requirement <'VV-02'> executableComplexityBudgets {
        doc /* Check modules <=180 physical lines, functions <=60 lines,
           decision depth <=5 and the documented AST branch proxy <=20.
           Model calculations shall agree with independent truth-table oracles. */
    }
    requirement <'VV-03'> strictWireContract {
        doc /* Reject bool, float and numeric strings as sequence numbers at
           the actual MCP transport; verify the complete stdio handoff. */
    }
    requirement <'VV-04'> measuredResourceBudgets {
        doc /* Measure declared local speed, scaling and traced-memory budgets
           with environment provenance and no portable timing guarantee. */
    }
    requirement <'VV-05'> retainedAssuranceEvidence {
        doc /* Retain all finite CROSS JOIN tuples and link assertions to
           requirements. Preserve failed runs and mutation survivors alongside
           requirement-preserving supersession and current source provenance. */
    }

    part def AssuranceCampaign {
        doc /* Allocated to run_vv.py, SQL catalog, separate pytest/unittest
           suites, mutation campaign and exported historical evidence. */
        attribute moduleLineLimit : Integer = 180;
        attribute functionLineLimit : Integer = 60;
        attribute decisionDepthLimit : Integer = 5;
        attribute branchProxyLimit : Integer = 20;
    }

    part referenceSystem : GovernancePipeline;
    part qualification : AssuranceCampaign;
    satisfy packetIndependent by referenceSystem.scanner;
    satisfy strictLineage by referenceSystem.worker;
    satisfy verifyBeforeExecute by referenceSystem.boundary;
    satisfy failClosed by referenceSystem.boundary;
    satisfy boundedAdmission by referenceSystem.queue;
    satisfy availabilityIsNotIntegrity by referenceSystem.worker;
    satisfy boundedRetention by referenceSystem.worker;
    satisfy snapshotBeforeAwait by referenceSystem.boundary;
    satisfy streamBackpressure by referenceSystem.sequencer;
    satisfy lifecycleOwnership by referenceSystem.worker;
    satisfy executableComplexityBudgets by qualification;
    satisfy strictWireContract by referenceSystem.boundary;
    satisfy measuredResourceBudgets by qualification;
    satisfy retainedAssuranceEvidence by qualification;
}
