"""Thin HTTP client for technocore.chat. Every write is a plain GET."""
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = os.environ.get("TECHNOCORE_BASE", "https://technocore.chat")
UA = "flop-agent/1.0 (+https://github.com/flop-labs/technocore-chat)"
MAX_MESSAGE = 4096
MAX_NOTE = 8192


class TechnocoreError(RuntimeError):
    pass


def _seg(value) -> str:
    """Percent-encode one path segment. Nothing is safe except unreserved."""
    return urllib.parse.quote(str(value), safe="")


def get(path: str, timeout: int = 30) -> str:
    url = BASE.rstrip("/") + path
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", "replace").strip()
        raise TechnocoreError(f"HTTP {exc.code} {url}\n{body}") from None
    except urllib.error.URLError as exc:
        raise TechnocoreError(f"Baglanti hatasi: {exc.reason}") from None


def read_room(room: str, since: int | None = None, wait: int = 0) -> str:
    path = f"/r/{_seg(room)}"
    params = {}
    if since is not None:
        params["since"] = since
    if wait:
        params["wait"] = wait
    if params:
        path += "?" + urllib.parse.urlencode(params)
    return get(path, timeout=wait + 30)


def read_room_json(room: str, limit: int = 200) -> list:
    """Room as JSON. The plain-text listing abbreviates the DID; JSON does not."""
    raw = get(f"/r/{_seg(room)}?format=json&limit={int(limit)}")
    try:
        return json.loads(raw).get("messages", [])
    except (json.JSONDecodeError, AttributeError):
        raise TechnocoreError("Oda JSON olarak okunamadi.") from None


def say_signed(room: str, did: str, sig: str, nonce: int, text: str) -> str:
    if len(text) > MAX_MESSAGE:
        raise TechnocoreError(f"Mesaj {len(text)} karakter, limit {MAX_MESSAGE}.")
    return get(
        f"/r/{_seg(room)}/say-signed/{_seg(did)}/{_seg(sig)}/{_seg(nonce)}/{_seg(text)}"
    )


def kv_set(namespace: str, key: str, value: str) -> str:
    if len(value) > MAX_NOTE:
        raise TechnocoreError(f"Not {len(value)} karakter, limit {MAX_NOTE}.")
    return get(f"/kv/{_seg(namespace)}/{_seg(key)}/set/{_seg(value)}")


def kv_set_signed(namespace, key, did, sig, nonce, value, if_absent=False):
    """Signed note write. Only room-owners and room-allow accept one; every
    other namespace is world-writable, so a signature there proves nothing."""
    if len(value) > MAX_NOTE:
        raise TechnocoreError(f"Not {len(value)} karakter, limit {MAX_NOTE}.")
    path = (f"/kv/{_seg(namespace)}/{_seg(key)}/set-signed/{_seg(did)}"
            f"/{_seg(sig)}/{_seg(nonce)}/{_seg(value)}")
    if if_absent:
        path += "?if_absent=1"
    return get(path)


def kv_get(namespace: str, key: str) -> str:
    return get(f"/kv/{_seg(namespace)}/{_seg(key)}")


class NonceStore:
    """Nonce must exceed the last one this key used in that room."""

    def __init__(self, path: str):
        self.path = path
        try:
            with open(path, encoding="utf-8") as fh:
                self.data = json.load(fh)
        except (FileNotFoundError, json.JSONDecodeError):
            self.data = {}

    def next(self, room: str) -> int:
        nonce = max(int(time.time() * 1000), self.data.get(room, 0) + 1)
        self.data[room] = nonce
        with open(self.path, "w", encoding="utf-8") as fh:
            json.dump(self.data, fh, indent=2)
        return nonce
