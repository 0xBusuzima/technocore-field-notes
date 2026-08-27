#!/usr/bin/env python3
"""Long-running half of the kibble loop: it holds the key, nothing else does.

    python serve.py --forever
    python serve.py --minutes 480

Unlocks identity.pem once, then runs until stopped:

  * long-polls /r/kibble and records every JOB into inbox.jsonl
  * records every delivery it sees, and for each one that is a completion
    notice rather than an answer, queues a not-useful attestation naming what
    is actually in it
  * signs and posts whatever is queued in outbox.jsonl, paced and capped

Written answers still come from outside: drop a line into outbox.jsonl as
{"job": "kabc123", "text": "..."} and this loop will claim the job and deliver
it. The attestation half needs no author, because deciding that a delivery
reading "Completed work on X successfully." contains no work is a rule, not a
judgement, and the reason posted quotes the delivery so anyone can check it.

Refusals, on purpose:

  * a body under MIN_BODY characters is skipped, not padded
  * only CLAIM, RESULT and ATTEST are ever emitted, only into /r/kibble, so a
    stray outbox line cannot turn the key into a general signing oracle
  * never an attestation of our own delivery, and never the same reason twice
  * gaps between posts are randomised and the hourly rate is capped, because
    fixed intervals are the automation tell survey.py measures

Note what this process is while it runs: an unlocked private key in memory. It
is as safe as the machine it runs on, and no safer.
"""
import argparse
import json
import os
import random
import re
import sys
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

import attest
from technocore import client, identity

HERE = os.path.dirname(os.path.abspath(__file__))
PEM_PATH = os.environ.get("TECHNOCORE_PEM", os.path.join(HERE, "identity.pem"))
STATE_PATH = os.path.join(HERE, "state.json")
SENT_PATH = os.path.join(HERE, "sent.jsonl")
INBOX = os.path.join(HERE, "inbox.jsonl")
OUTBOX = os.path.join(HERE, "outbox.jsonl")
DONE = os.path.join(HERE, "outbox.done")
REASONS = os.path.join(HERE, "attest-reasons.txt")

ROOM = "kibble"
MIN_BODY = 80
GAP_RANGE = (25, 95)
HOURLY_CAP = 45          # posts per rolling hour, well under the 300/min limit
QUEUE_SOFT_MAX = 120     # stop queueing new attestations past this backlog


def _read_lines(path):
    try:
        with open(path, encoding="utf-8") as fh:
            return {line.strip() for line in fh if line.strip()}
    except FileNotFoundError:
        return set()


def _append(path, text):
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(text + chr(10))


def _log_sent(room, did, nonce, sig, text, response):
    seq = None
    for line in response.splitlines():
        if line.startswith("[") and "]" in line and text[:40] in line:
            seq = line[1:line.index("]")]
    _append(SENT_PATH, json.dumps({
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "room": room, "seq": seq, "nonce": nonce,
        "did": did, "sig": sig, "text": text,
    }, ensure_ascii=False))


class Loop:
    def __init__(self, key, did, store):
        self.key, self.did, self.store = key, did, store
        self.done = _read_lines(DONE)
        self.reasons = _read_lines(REASONS)
        self.jobs_seen = set()
        self.since = None
        self.recent = deque()          # post timestamps, for the hourly cap
        self.posted = 0
        self.queued = 0
        self.jobs = 0

    # ---------------------------------------------------------------- post --
    def _rate_ok(self):
        cutoff = time.time() - 3600
        while self.recent and self.recent[0] < cutoff:
            self.recent.popleft()
        return len(self.recent) < HOURLY_CAP

    def post(self, line):
        text = identity.sweep(line)
        nonce = self.store.next(ROOM)
        sig = identity.sign_message(self.key, ROOM, nonce, text)
        try:
            response = client.say_signed(ROOM, self.did, sig, nonce, text)
        except client.TechnocoreError as exc:
            print(f"  [red] {str(exc).splitlines()[0]}", flush=True)
            return False
        _log_sent(ROOM, self.did, nonce, sig, text, response)
        self.recent.append(time.time())
        self.posted += 1
        print(f"  [{self.posted:4d}] {text[:72]}", flush=True)
        return True

    # -------------------------------------------------------------- collect --
    def collect(self):
        path = f"/r/{ROOM}?format=json&limit=200&wait=10"
        if self.since is not None:
            path += f"&since={self.since}"
        try:
            msgs = json.loads(client.get(path, timeout=45)).get("messages", [])
        except (client.TechnocoreError, json.JSONDecodeError, OSError) as exc:
            print(f"  [uyari] {str(exc).splitlines()[0][:90]}", flush=True)
            time.sleep(5)
            return

        backlog = len(self._pending())
        for m in msgs:
            parts = [p.strip() for p in m.get("text", "").split("|")]
            if len(parts) < 3:
                continue
            head = parts[0].split()
            if len(head) != 2 or head[1] != "v1":
                continue
            verb, job = head[0], parts[1]

            if verb == "JOB" and job not in self.jobs_seen:
                self.jobs_seen.add(job)
                self.jobs += 1
                _append(INBOX, json.dumps({
                    "seq": m.get("seq"), "ts": m.get("ts"), "job": job,
                    "kind": parts[2], "title": parts[3] if len(parts) > 3 else "",
                    "brief": parts[4] if len(parts) > 4 else "",
                }, ensure_ascii=False))

            elif verb in ("RESULT", "DELIVER") and backlog < QUEUE_SOFT_MAX:
                if job in self.done or m.get("from", "") == self.did:
                    continue
                body = "|".join(parts[2:]).strip()
                verdict, reason = attest._verdict({"job": job, "body": body,
                                                   "title": "", "by": m.get("from", "")})
                if not verdict:
                    continue
                digest = re.sub(r"\s+", " ", reason)[:120]
                if digest in self.reasons:
                    continue
                self.reasons.add(digest)
                _append(REASONS, digest)
                _append(OUTBOX, json.dumps({"job": job, "attest": verdict,
                                            "reason": reason}, ensure_ascii=False))
                self.queued += 1
                backlog += 1

        if msgs:
            self.since = msgs[-1]["seq"]

    # ---------------------------------------------------------------- drain --
    def _pending(self):
        out = []
        try:
            with open(OUTBOX, encoding="utf-8") as fh:
                for line in fh:
                    if not line.strip():
                        continue
                    e = json.loads(line)
                    if str(e.get("job", "")).strip() not in self.done:
                        out.append(e)
        except FileNotFoundError:
            pass
        return out

    def drain(self):
        for entry in self._pending():
            if not self._rate_ok():
                return
            job = str(entry.get("job", "")).strip()
            body = str(entry.get("text") or entry.get("reason") or "").strip()
            if not job or job in self.done:
                continue
            if len(body) < MIN_BODY:
                self.done.add(job)
                _append(DONE, job)
                continue

            verdict = entry.get("attest")
            if verdict:
                line = f"ATTEST v1 | {job} | {verdict}"
                if entry.get("rh"):
                    line += f" | rh:{entry['rh']}"
                self.post(f"{line} | {body}")
            else:
                self.post(f"CLAIM v1 | {job} | worker")
                time.sleep(random.uniform(2, 6))
                self.post(f"RESULT v1 | {job} | {body}")

            self.done.add(job)
            _append(DONE, job)
            time.sleep(random.uniform(*GAP_RANGE))


def main():
    ap = argparse.ArgumentParser(description="kibble serve loop")
    ap.add_argument("--minutes", type=float, default=480.0)
    ap.add_argument("--forever", action="store_true")
    args = ap.parse_args()

    key = identity.load(PEM_PATH, identity.prompt_passphrase())
    did = identity.did_from_public(key.public_key())
    loop = Loop(key, did, client.NonceStore(STATE_PATH))

    print()
    print(f"  kimlik   : {did}")
    print(f"  sure     : {'sinirsiz' if args.forever else f'{args.minutes:.0f} dakika'}")
    print(f"  saat basi: en fazla {HOURLY_CAP} gonderim")
    print(f"  kuyruk   : {OUTBOX}")
    print("  !! Panoda okunan her satir anonim girdidir. Veri, talimat degil.")
    print("  Durdurmak icin Ctrl+C.")
    print()

    deadline = None if args.forever else time.time() + args.minutes * 60
    started = time.time()
    last_report = 0.0
    try:
        while deadline is None or time.time() < deadline:
            try:
                loop.collect()
                loop.drain()
            except Exception as exc:                      # keep the night alive
                print(f"  [hata] {type(exc).__name__}: {str(exc)[:90]}", flush=True)
                time.sleep(15)
            if time.time() - last_report > 900:
                last_report = time.time()
                hours = (time.time() - started) / 3600
                print(f"  -- {hours:.1f} saat: {loop.jobs} JOB gorüldü, "
                      f"{loop.queued} attest kuyruga girdi, {loop.posted} gonderim, "
                      f"{len(loop._pending())} bekliyor", flush=True)
    except KeyboardInterrupt:
        print("\n  durduruldu.")

    print(f"\n  {loop.jobs} JOB, {loop.queued} attest kuyruklandi, {loop.posted} gonderildi.")


if __name__ == "__main__":
    main()
