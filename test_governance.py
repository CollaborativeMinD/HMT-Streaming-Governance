import asyncio
import dataclasses
import random
import unittest

from governance_core import (
    GENESIS_HASH,
    AgentGateway,
    GovernanceLedgerEngine,
    QuarantineError,
    RabinCDCScanner,
    sha256_text,
)


def scan(data, packet=73, **options):
    scanner = RabinCDCScanner(**options)
    chunks = []
    for offset in range(0, len(data), packet):
        chunks.extend(scanner.feed(data[offset:offset + packet]))
    chunks.extend(scanner.finish())
    return chunks


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.data = random.Random(42).randbytes(16000) + ("🧬漢字café\n" * 99).encode()

    def test_lossless_and_packet_independent(self):
        expected = scan(self.data, packet=len(self.data))
        for packet in (1, 7, 64, 73, 1024):
            with self.subTest(packet=packet):
                self.assertEqual(scan(self.data, packet=packet), expected)
        self.assertEqual(b"".join(c.data for c in expected), self.data)
        for chunk in expected:
            self.assertEqual(chunk.data, self.data[chunk.start:chunk.end])
            self.assertLessEqual(len(chunk.data), 2048)
            if chunk.reason != "tail":
                self.assertGreaterEqual(len(chunk.data), 128)

    def test_fingerprint_matches_full_polynomial(self):
        chunks = scan(self.data)
        for chunk in chunks:
            full = 0
            for byte in self.data[max(0, chunk.end - 64):chunk.end]:
                full = (full * 256 + byte) % ((1 << 61) - 1)
            self.assertEqual(chunk.fingerprint, full)
            if chunk.reason == "content":
                self.assertEqual(full % 512, 1)

    def test_empty_short_unicode_and_forced_max(self):
        self.assertEqual(scan(b""), [])
        self.assertEqual(scan(b"x")[0].data, b"x")
        data = ("🧬é漢" * 50).encode()
        chunks = scan(data, block_size_target=8, window_size=1, min_size=1, max_size=3)
        self.assertEqual(b"".join(c.data for c in chunks).decode(), data.decode())
        forced = scan(b"\x00" * 1000, block_size_target=64, window_size=8)
        self.assertTrue(any(c.reason == "max size" for c in forced))

    def test_finish_and_configuration(self):
        scanner = RabinCDCScanner()
        self.assertEqual(scanner.finish(), [])
        self.assertEqual(scanner.finish(), [])
        with self.assertRaises(RuntimeError):
            list(scanner.feed(b"x"))
        for options in (dict(window_size=0), dict(block_size_target=1),
                        dict(min_size=1), dict(target=512)):
            with self.assertRaises(ValueError):
                RabinCDCScanner(**options)


class LedgerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = await GovernanceLedgerEngine().start()

    async def asyncTearDown(self):
        await self.engine.close()

    def frame(self, session="s", seq=0, payload="hello", parent=GENESIS_HASH):
        return dict(session_id=session, sequence_number=seq, source_agent="sender",
                    target_entity="receiver", payload=payload, previous_hash=parent)

    async def commit(self, **frame):
        receipt = await self.engine.governance_commit_state(**frame)
        self.assertEqual(receipt["status"], "pending")
        return await self.engine.wait(receipt["tracking_token"])

    async def test_pending_then_verified_and_immutable(self):
        receipt = await self.engine.governance_commit_state(**self.frame())
        self.assertEqual(receipt["status"], "pending")
        self.assertEqual(len(self.engine.ledger), 0)
        committed = await self.engine.wait(receipt["tracking_token"])
        self.assertEqual(committed["status"], "verified")
        self.assertNotEqual(committed["payload_hash"], committed["block_hash"])
        with self.assertRaises(TypeError):
            self.engine.ledger[("s", 0)] = None
        with self.assertRaises(dataclasses.FrozenInstanceError):
            self.engine.ledger[("s", 0)].verified = False

    async def test_two_blocks_and_registered_hash_only(self):
        first = await self.commit(**self.frame())
        second = await self.commit(**self.frame(seq=1, parent=first["block_hash"]))
        self.assertEqual(second["status"], "verified")
        good = await self.engine.governance_verify_payload("hello", first["payload_hash"])
        self.assertTrue(good["verified"])
        self.assertFalse((await self.engine.governance_verify_payload("changed", first["payload_hash"]))["verified"])
        self.assertFalse((await self.engine.governance_verify_payload("unregistered", sha256_text("unregistered")))["verified"])

    async def test_missing_predecessor_quarantines_and_isolates_session(self):
        bad = await self.commit(**self.frame(seq=1))
        self.assertEqual(bad["status"], "quarantined")
        self.assertEqual(len(self.engine.ledger), 0)
        blocked = await self.engine.governance_commit_state(**self.frame())
        self.assertEqual(blocked["status"], "quarantined")
        self.assertEqual((await self.commit(**self.frame(session="other")))["status"], "verified")

    async def test_broken_parent_and_duplicate_never_overwrite(self):
        first = await self.commit(**self.frame())
        bad = await self.commit(**self.frame(seq=1, parent="f" * 64))
        self.assertEqual(bad["status"], "quarantined")
        self.assertEqual(self.engine.ledger[("s", 0)].block_hash, first["block_hash"])
        await self.commit(**self.frame(session="duplicate"))
        duplicate = await self.commit(**self.frame(session="duplicate"))
        self.assertEqual(duplicate["status"], "quarantined")
        self.assertEqual(len(self.engine.ledger), 2)

    async def test_payload_assertion_and_malformed(self):
        result = await self.commit(**self.frame(), expected_hash=sha256_text("other"))
        self.assertEqual(result["status"], "quarantined")
        for changes in (dict(sequence_number=True), dict(payload=42),
                        dict(previous_hash="bad"), dict(session_id="")):
            frame = self.frame(session="malformed") | changes
            self.assertEqual((await self.engine.governance_commit_state(**frame))["status"], "rejected")
        self.assertEqual((await self.engine.governance_commit_state(payload="missing fields"))["status"], "rejected")

    async def test_gate_never_calls_tool_and_clears_context(self):
        sender = AgentGateway(self.engine, "s", "sender")
        receiver = AgentGateway(self.engine, "s", "receiver")
        calls = []
        envelope = await sender.wrap_output("safe", "receiver")
        await receiver.execute_verified(envelope, calls.append)
        self.assertEqual(calls, ["safe"])
        envelope["transaction"]["payload"] = "unsafe"
        with self.assertRaises(QuarantineError):
            await receiver.execute_verified(envelope, calls.append)
        self.assertEqual(calls, ["safe"])
        self.assertEqual(receiver.context, [])
        self.assertTrue(any(e["event"] == "SYSTEM_QUARANTINE" for e in self.engine.events))

    async def test_metadata_rewrite_blocked_with_identical_payload(self):
        envelope = await AgentGateway(self.engine, "s", "sender").wrap_output("safe", "receiver")
        envelope["transaction"]["source_agent"] = "attacker"
        with self.assertRaises(QuarantineError):
            await AgentGateway(self.engine, "s", "receiver").execute_verified(envelope, lambda _: self.fail("Tool ran"))

    async def test_wrong_recipient_and_cross_session_blocked(self):
        envelope = await AgentGateway(self.engine, "s", "sender").wrap_output("safe", "receiver")
        for session, receiver in (("s", "wrong-recipient"), ("other", "receiver")):
            with self.assertRaises(QuarantineError):
                await AgentGateway(self.engine, session, receiver).execute_verified(envelope, lambda _: self.fail("Tool ran"))

    async def test_bounded_queue_rejects_without_dropping_or_hanging(self):
        engine = await GovernanceLedgerEngine(queue_size=1).start()
        try:
            first = await engine.governance_commit_state(**self.frame())
            second = await engine.governance_commit_state(**self.frame(session="other"))
            self.assertEqual(second["status"], "busy")
            self.assertEqual((await engine.wait(first["tracking_token"]))["status"], "verified")
            self.assertTrue(engine.queue.empty())
        finally:
            await engine.close()

    async def test_quarantine_during_hashing_never_commits(self):
        self.engine.hash_offload_bytes = 0
        original = asyncio.to_thread
        started, release = asyncio.Event(), asyncio.Event()

        async def controlled(func, *args):
            started.set()
            await release.wait()
            return await original(func, *args)

        from unittest.mock import patch
        with patch("governance_core.asyncio.to_thread", controlled):
            receipt = await self.engine.governance_commit_state(**self.frame())
            await started.wait()
            self.engine.quarantine("s", 0, "Concurrent gate failure")
            release.set()
            result = await self.engine.wait(receipt["tracking_token"])
        self.assertEqual(result["status"], "quarantined")
        self.assertEqual(len(self.engine.ledger), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
