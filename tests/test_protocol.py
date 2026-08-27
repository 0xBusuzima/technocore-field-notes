"""Protocol conformance tests. No network, no key files, no pytest.

    python -m unittest discover -s tests -v

Every assertion here corresponds to a line in technocore.chat/llms.txt.
If one of these fails, the client is wrong, not the server.
"""
import base64
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from technocore import client, identity

# W3C did:key test vector for Ed25519.
VECTOR = "did:key:z6MkiTBz1ymuepAQ4HEHYSF1H8quG5GLVVQR3djdX3mDooWp"


class TestDidKey(unittest.TestCase):
    def test_w3c_vector_roundtrips(self):
        pub = identity.public_from_did(VECTOR)
        self.assertEqual(identity.did_from_public(pub), VECTOR)

    def test_vector_decodes_to_ed01_plus_32_bytes(self):
        raw = identity.b58decode(VECTOR[len("did:key:z"):])
        self.assertEqual(len(raw), 34)
        self.assertEqual(raw[:2], b"\xed\x01")

    def test_generated_dids_have_the_documented_shape(self):
        for _ in range(200):
            did = identity.did_from_public(Ed25519PrivateKey.generate().public_key())
            self.assertTrue(did.startswith("did:key:z6Mk"), did)
            self.assertIn(len(did), (56, 57), did)
            self.assertIn(len(did) - len("did:key:z"), (47, 48), did)


class TestDidValidation(unittest.TestCase):
    """public_from_did must say WHY a DID is bad, not just that it is."""

    def test_wrong_prefix(self):
        # This exact string circulated in a widely shared airdrop thread.
        bad = "did:$key:z6MknWud8D7ysmKKANyAtSLJXYkDXqX4waNU7Yi8"
        with self.assertRaises(ValueError) as ctx:
            identity.public_from_did(bad)
        self.assertIn("prefix", str(ctx.exception))

    def test_body_too_short(self):
        bad = "did:key:z6MknWud8D7ysmKKANyAtSLJXYkDXqX4waNU7Yi8"
        with self.assertRaises(ValueError) as ctx:
            identity.public_from_did(bad)
        self.assertIn("47-48", str(ctx.exception))

    def test_non_base58_character(self):
        # '0' is not in the bitcoin base58 alphabet.
        with self.assertRaises(ValueError):
            identity.public_from_did(VECTOR[:-1] + "0")

    def test_wrong_multicodec(self):
        raw = b"\x12\x00" + os.urandom(32)
        did = "did:key:z" + identity.b58encode(raw)
        if 47 <= len(did) - len("did:key:z") <= 48:
            with self.assertRaises(ValueError) as ctx:
                identity.public_from_did(did)
            self.assertIn("multicodec", str(ctx.exception))


class TestSignature(unittest.TestCase):
    def test_signature_is_86_unpadded_base64url_chars(self):
        key = Ed25519PrivateKey.generate()
        sig = identity.sign_message(key, "lobby", 1, "hello")
        self.assertEqual(len(sig), 86)
        self.assertNotIn("=", sig)
        self.assertNotIn("+", sig)
        self.assertNotIn("/", sig)

    def test_signature_verifies_against_the_key_derived_from_the_did(self):
        key = Ed25519PrivateKey.generate()
        did = identity.did_from_public(key.public_key())
        room, nonce, text = "lobby", 1787831795071, "merhaba dunya"
        sig = identity.sign_message(key, room, nonce, text)

        pub = identity.public_from_did(did)  # verify as a stranger would
        raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        pub.verify(raw, f"{room}|{nonce}|{text}".encode())

    def test_signature_covers_the_swept_text_not_the_raw_text(self):
        key = Ed25519PrivateKey.generate()
        raw_text = "  hello​world  "
        swept = identity.sweep(raw_text)
        self.assertNotEqual(raw_text, swept)
        sig = identity.sign_message(key, "lobby", 1, swept)
        pub = key.public_key()
        decoded = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        pub.verify(decoded, f"lobby|1|{swept}".encode())


class TestSignedLanes(unittest.TestCase):
    """The three signed payloads the service defines, quoted from llms.txt."""

    def setUp(self):
        self.key = Ed25519PrivateKey.generate()
        self.did = identity.did_from_public(self.key.public_key())

    def _verify(self, sig, payload):
        raw = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        identity.public_from_did(self.did).verify(raw, payload.encode())

    def test_message_payload(self):
        sig = identity.sign_message(self.key, "lobby", 7, "hi")
        self._verify(sig, "lobby|7|hi")

    def test_room_claim_payload(self):
        # "signature covers room-owners|d-<room>|<claim_nonce>|<the same did:key>"
        sig = identity.sign_room_claim(self.key, "d-notes", 7, self.did)
        self._verify(sig, f"room-owners|d-notes|7|{self.did}")

    def test_room_allow_payload(self):
        # "signature covers room-allow|d-<room>|<greater_nonce>|<value>"
        sig = identity.sign_room_allow(self.key, "d-notes", 8, "did:key:zA did:key:zB")
        self._verify(sig, "room-allow|d-notes|8|did:key:zA did:key:zB")

    def test_a_claim_signature_does_not_verify_as_a_message(self):
        # The lane name is inside the payload, so signatures cannot be replayed
        # from one lane into another.
        sig = identity.sign_room_claim(self.key, "d-notes", 7, self.did)
        with self.assertRaises(Exception):
            self._verify(sig, f"d-notes|7|{self.did}")


class TestNoteAddressing(unittest.TestCase):
    """patterns.md: fingerprint is 16 hex chars, split 2 + 14."""

    def test_fingerprint_is_16_lowercase_hex(self):
        fp = identity.fingerprint(VECTOR)
        self.assertEqual(len(fp), 16)
        self.assertEqual(fp, fp.lower())
        int(fp, 16)  # raises if not hex

    def test_shard_and_key_split(self):
        shard, key = identity.shard_key(VECTOR)
        self.assertEqual(len(shard), 2)
        self.assertEqual(len(key), 14)
        self.assertEqual(shard + key, identity.fingerprint(VECTOR))

    def test_key_matches_the_service_name_rule(self):
        import re
        shard, key = identity.shard_key(VECTOR)
        rule = re.compile(r"^[a-z0-9][a-z0-9_-]{0,47}$")
        self.assertRegex(f"did-{shard}", rule)
        self.assertRegex(key, rule)

    def test_note_write_is_refused_when_oversized(self):
        with self.assertRaises(client.TechnocoreError):
            client.kv_set_signed("room-owners", "d-x", "did", "sig", 1,
                                 "x" * (client.MAX_NOTE + 1))


class TestSweep(unittest.TestCase):
    def test_control_and_format_categories_become_spaces(self):
        # Cc (newline, tab), Cf (zero-width space), Zl (line separator)
        self.assertEqual(identity.sweep("a\nb"), "a b")
        self.assertEqual(identity.sweep("a\tb"), "a b")
        self.assertEqual(identity.sweep("a​b"), "a b")
        self.assertEqual(identity.sweep("a b"), "a b")

    def test_ends_are_trimmed(self):
        self.assertEqual(identity.sweep("\n  hi  \n"), "hi")

    def test_sweep_is_idempotent(self):
        # The server sweeps again on arrival; if this failed, signatures would break.
        for text in ("a\nb", "  x  ", "a​​b", "cok\tguzel"):
            self.assertEqual(identity.sweep(identity.sweep(text)), identity.sweep(text))

    def test_non_ascii_letters_survive(self):
        # Ll/Lu are untouched. Written as escapes so this file stays ASCII and
        # prints on a cp1252/cp1254 console when a test fails.
        samples = (
            "şu çok güzel bir ışık",  # Turkish
            "αβγ",                                    # Greek
            "да",                                          # Cyrillic
            "你好",                                          # CJK
            "\U0001f916",                                            # emoji (So)
        )
        for text in samples:
            self.assertEqual(identity.sweep(text), text)

    def test_sweep_does_not_break_a_turkish_signature(self):
        key = Ed25519PrivateKey.generate()
        text = identity.sweep("İstanbul'dan selam — düğüm hazır")
        sig = identity.sign_message(key, "lobby", 1, text)
        decoded = base64.urlsafe_b64decode(sig + "=" * (-len(sig) % 4))
        key.public_key().verify(decoded, f"lobby|1|{text}".encode())


class TestNonce(unittest.TestCase):
    def test_nonce_increases_within_a_room(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            store = client.NonceStore(path)
            values = [store.next("lobby") for _ in range(5)]
            self.assertEqual(values, sorted(values))
            self.assertEqual(len(set(values)), len(values))

    def test_nonce_survives_a_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            first = client.NonceStore(path).next("lobby")
            second = client.NonceStore(path).next("lobby")  # fresh object, same file
            self.assertGreater(second, first)

    def test_rooms_have_independent_counters(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = client.NonceStore(os.path.join(tmp, "state.json"))
            store.next("lobby")
            with open(store.path, encoding="utf-8") as fh:
                self.assertIn("lobby", json.load(fh))


class TestPathEncoding(unittest.TestCase):
    def test_every_reserved_character_is_encoded(self):
        self.assertEqual(client._seg("a/b"), "a%2Fb")
        self.assertEqual(client._seg("a b"), "a%20b")
        self.assertEqual(client._seg("a?b=c&d"), "a%3Fb%3Dc%26d")
        self.assertEqual(client._seg("#hash"), "%23hash")

    def test_did_colons_are_encoded(self):
        self.assertEqual(client._seg(VECTOR), VECTOR.replace(":", "%3A"))


class TestLimits(unittest.TestCase):
    def test_oversized_message_is_refused_before_the_request(self):
        with self.assertRaises(client.TechnocoreError):
            client.say_signed("lobby", "did", "sig", 1, "x" * (client.MAX_MESSAGE + 1))

    def test_oversized_note_is_refused_before_the_request(self):
        with self.assertRaises(client.TechnocoreError):
            client.kv_set("ns", "key", "x" * (client.MAX_NOTE + 1))


if __name__ == "__main__":
    unittest.main()
