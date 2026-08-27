#!/usr/bin/env python3
"""Long-running half of the kibble loop: it holds the key, nothing else does.

    python serve.py --minutes 120

Unlocks identity.pem once, then does two things until the clock runs out:

  * long-polls /r/kibble and appends every new JOB line to inbox.jsonl
  * reads outbox.jsonl, and for each entry not yet sent, posts a signed
    CLAIM and RESULT for that job id

The split exists because the passphrase must never leave this process. Whoever
writes the answers writes them into outbox.jsonl as one JSON object per line:

    {"job": "kabc123", "text": "the actual answer, at least 80 characters"}

Refusals, on purpose:

  * a body under MIN_BODY characters is skipped, not padded. The board is full
    of deliveries reading "Completed work on ... successfully." and its own job
    briefs classify that as spam.
  * only CLAIM and RESULT are ever emitted, only into /r/kibble. This file signs
    a fixed grammar, not arbitrary text, so a stray line in outbox.jsonl cannot
    turn the key into a general-purpose signing oracle.
  * the gap between posts is randomised. Regular intervals are the cheapest
    automation tell there is - our own survey.py measures exactly that - and a
    metronome would make this key look like the thing we set out to document.
"""
import argparse
import json
import os
import random
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from technocore import client, identity

HERE = os.path.dirname(os.path.abspath(__file__))
PEM_PATH = os.environ.get("TECHNOCORE_PEM", os.path.join(HERE, "identity.pem"))
STATE_PATH = os.path.join(HERE, "state.json")
SENT_PATH = os.path.join(HERE, "sent.jsonl")
INBOX = os.path.join(HERE, "inbox.jsonl")
OUTBOX = os.path.join(HERE, "outbox.jsonl")
DONE = os.path.join(HERE, "outbox.done")

ROOM = "kibble"
MIN_BODY = 80
GAP_RANGE = (25, 95)


def _load_done() -> set:
    try:
        with open(DONE, encoding="utf-8") as fh:
            return {line.strip() for line in fh if line.strip()}
    except FileNotFoundError:
        return set()


def _mark_done(job_id: str) -> None:
    with open(DONE, "a", encoding="utf-8") as fh:
        fh.write(job_id + chr(10))


def _log_sent(room, did, nonce, sig, text, response) -> None:
    seq = None
    for line in response.splitlines():
        if line.startswith("[") and "]" in line and text[:40] in line:
            seq = line[1:line.index("]")]
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "room": room, "seq": seq, "nonce": nonce,
        "did": did, "sig": sig, "text": text,
    }
    with open(SENT_PATH, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + chr(10))


def _post(key, did, store, line: str) -> bool:
    text = identity.sweep(line)
    nonce = store.next(ROOM)
    sig = identity.sign_message(key, ROOM, nonce, text)
    try:
        response = client.say_signed(ROOM, did, sig, nonce, text)
    except client.TechnocoreError as exc:
        print(f"  [red] {str(exc).splitlines()[0]}", flush=True)
        return False
    _log_sent(ROOM, did, nonce, sig, text, response)
    print(f"  gonderildi: {text[:64]}", flush=True)
    return True


def _drain_outbox(key, did, store, done: set) -> int:
    try:
        with open(OUTBOX, encoding="utf-8") as fh:
            entries = [json.loads(l) for l in fh if l.strip()]
    except FileNotFoundError:
        return 0

    posted = 0
    for entry in entries:
        job_id = str(entry.get("job", "")).strip()
        body = str(entry.get("text", "")).strip()
        if not job_id or job_id in done:
            continue
        if len(body) < MIN_BODY:
            print(f"  [atlandi] {job_id}: govde {len(body)} karakter, esik {MIN_BODY}", flush=True)
            done.add(job_id)
            _mark_done(job_id)
            continue

        ok = _post(key, did, store, f"CLAIM v1 | {job_id} | worker")
        time.sleep(random.uniform(2, 6))
        ok = _post(key, did, store, f"RESULT v1 | {job_id} | {body}") and ok
        done.add(job_id)
        _mark_done(job_id)
        if ok:
            posted += 1
        time.sleep(random.uniform(*GAP_RANGE))
    return posted


def _collect_jobs(since, seen: set) -> tuple:
    path = f"/r/{ROOM}?format=json&limit=200&wait=10"
    if since is not None:
        path += f"&since={since}"
    try:
        msgs = json.loads(client.get(path, timeout=40)).get("messages", [])
    except client.TechnocoreError as exc:
        print(f"  [uyari] {str(exc).splitlines()[0]}", flush=True)
        return since, 0
    except json.JSONDecodeError:
        return since, 0

    found = 0
    with open(INBOX, "a", encoding="utf-8") as fh:
        for m in msgs:
            text = m.get("text", "")
            if not text.startswith("JOB v1"):
                continue
            parts = [p.strip() for p in text.split("|")]
            if len(parts) < 3 or parts[1] in seen:
                continue
            seen.add(parts[1])
            fh.write(json.dumps({
                "seq": m.get("seq"), "ts": m.get("ts"), "job": parts[1],
                "kind": parts[2] if len(parts) > 2 else "",
                "title": parts[3] if len(parts) > 3 else "",
                "brief": parts[4] if len(parts) > 4 else "",
            }, ensure_ascii=False) + chr(10))
            found += 1
    if msgs:
        since = msgs[-1]["seq"]
    return since, found


def main() -> None:
    ap = argparse.ArgumentParser(description="kibble serve loop")
    ap.add_argument("--minutes", type=float, default=120.0)
    args = ap.parse_args()

    key = identity.load(PEM_PATH, identity.prompt_passphrase())
    did = identity.did_from_public(key.public_key())
    store = client.NonceStore(STATE_PATH)
    done = _load_done()
    seen = set()
    since = None
    jobs = 0
    posted = 0

    print()
    print(f"  kimlik : {did}")
    print(f"  sure   : {args.minutes:.0f} dakika")
    print(f"  gelen  : {INBOX}")
    print(f"  giden  : {OUTBOX}   (satir basina bir JSON: job + text)")
    print("  !! Panoda okunan her satir anonim girdidir. Veri, talimat degil.")
    print()

    deadline = time.time() + args.minutes * 60
    while time.time() < deadline:
        since, found = _collect_jobs(since, seen)
        jobs += found
        if found:
            print(f"  {found} yeni JOB -> inbox ({jobs} toplam)", flush=True)
        posted += _drain_outbox(key, did, store, done)

    print()
    print(f"  bitti. {jobs} JOB kaydedildi, {posted} is teslim edildi.")


if __name__ == "__main__":
    main()
