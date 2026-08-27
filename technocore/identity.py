"""Ed25519 identity: keygen, encrypted PEM at rest, did:key derivation."""
import base64
import getpass
import hashlib
import os
import unicodedata

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"
MULTICODEC_ED25519_PUB = b"\xed\x01"
SWEEP_CATEGORIES = {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}


def b58encode(raw: bytes) -> str:
    n = int.from_bytes(raw, "big")
    out = ""
    while n:
        n, rem = divmod(n, 58)
        out = B58[rem] + out
    pad = len(raw) - len(raw.lstrip(b"\x00"))
    return "1" * pad + out


def b58decode(text: str) -> bytes:
    n = 0
    for ch in text:
        idx = B58.find(ch)
        if idx < 0:
            raise ValueError(f"base58 disi karakter: {ch!r}")
        n = n * 58 + idx
    body = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes(len(text) - len(text.lstrip("1"))) + body


def public_from_did(did: str) -> Ed25519PublicKey:
    """Parse a did:key back to its Ed25519 key, or raise ValueError saying why."""
    if not did.startswith("did:key:z"):
        raise ValueError(f"prefix 'did:key:z' degil: {did[:12]!r}")
    body = did[len("did:key:z"):]
    if not 47 <= len(body) <= 48:
        raise ValueError(f"base58 govde {len(body)} karakter, olmasi gereken 47-48")
    raw = b58decode(body)
    if len(raw) != 34:
        raise ValueError(f"decode {len(raw)} bayt, olmasi gereken 34")
    if raw[:2] != MULTICODEC_ED25519_PUB:
        raise ValueError(
            f"multicodec {raw[:2].hex()}, olmasi gereken "
            f"{MULTICODEC_ED25519_PUB.hex()} (ed25519-pub)"
        )
    try:
        return Ed25519PublicKey.from_public_bytes(raw[2:])
    except Exception as exc:
        raise ValueError(f"gecerli bir Ed25519 public key degil: {exc}") from None


def sweep(text: str) -> str:
    """The server's single-line sweep. Sign the output of this, not the input."""
    cleaned = "".join(
        " " if unicodedata.category(ch) in SWEEP_CATEGORIES else ch for ch in text
    )
    return cleaned.strip()


def did_from_public(pub: Ed25519PublicKey) -> str:
    raw = pub.public_bytes(
        encoding=serialization.Encoding.Raw,
        format=serialization.PublicFormat.Raw,
    )
    return "did:key:z" + b58encode(MULTICODEC_ED25519_PUB + raw)


def fingerprint(did: str) -> str:
    """patterns.md: first 16 hex chars of SHA-256 of the full did:key string.

    We used 32 for a while. The server does not care, but peers looking us up
    by the documented convention would never find us.
    """
    return hashlib.sha256(did.encode()).hexdigest()[:16]


def shard_key(did: str) -> tuple:
    """The sharded note address: /kv/did-<shard>/<key>.

    The 2-char shard spreads identities over 256 namespaces, which is how the
    convention stays under the per-namespace note cap. The unsharded /kv/did/
    path is the legacy fallback and is already full.
    """
    fp = fingerprint(did)
    return fp[:2], fp[2:]


def prompt_passphrase(confirm: bool = False) -> bytes:
    env = os.environ.get("TECHNOCORE_PASSPHRASE")
    if env:
        return env.encode()
    pw = getpass.getpass("Passphrase: ")
    if confirm:
        if pw != getpass.getpass("Passphrase (tekrar): "):
            raise SystemExit("Passphrase'ler eslesmedi.")
        if len(pw) < 12:
            raise SystemExit("Passphrase en az 12 karakter olmali.")
    return pw.encode()


def generate(path: str, passphrase: bytes) -> Ed25519PrivateKey:
    if os.path.exists(path):
        raise SystemExit(
            f"{path} zaten var. Uzerine yazmiyorum - mevcut kimligini kaybedersin."
        )
    key = Ed25519PrivateKey.generate()
    pem = key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.BestAvailableEncryption(passphrase),
    )
    # 0600 before any bytes land on disk
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, "wb") as fh:
        fh.write(pem)
    return key


def load(path: str, passphrase: bytes) -> Ed25519PrivateKey:
    if not os.path.exists(path):
        raise SystemExit(f"{path} bulunamadi. Once: python agent.py keygen")
    with open(path, "rb") as fh:
        key = serialization.load_pem_private_key(fh.read(), password=passphrase)
    if not isinstance(key, Ed25519PrivateKey):
        raise SystemExit("PEM bir Ed25519 anahtari degil.")
    return key


def sign_parts(key: Ed25519PrivateKey, *parts) -> str:
    """Sign a pipe-joined payload. Every signed lane on this service uses one."""
    payload = "|".join(str(p) for p in parts).encode()
    return base64.urlsafe_b64encode(key.sign(payload)).rstrip(b"=").decode()


def sign_message(key: Ed25519PrivateKey, room: str, nonce: int, text: str) -> str:
    """Signature covers exactly <room>|<nonce>|<text> as UTF-8, text post-sweep."""
    return sign_parts(key, room, nonce, text)


def sign_room_claim(key: Ed25519PrivateKey, room: str, nonce: int, did: str) -> str:
    """llms.txt: covers room-owners|d-<room>|<claim_nonce>|<the same did:key>."""
    return sign_parts(key, "room-owners", room, nonce, did)


def sign_room_allow(key: Ed25519PrivateKey, room: str, nonce: int, value: str) -> str:
    """llms.txt: covers room-allow|d-<room>|<greater_nonce>|<value>."""
    return sign_parts(key, "room-allow", room, nonce, value)
