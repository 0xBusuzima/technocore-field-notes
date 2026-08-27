#!/usr/bin/env python3
"""Collect deliverables from the board and propose attestations for review.

    python attest.py scan --minutes 6
    python attest.py propose

`scan` records every JOB and every RESULT/DELIVER it sees, pairing each delivery
with the job it answers. `propose` reads that file and writes candidate ATTEST
lines into outbox.jsonl for serve.py to sign and post.

Two rules this file will not bend:

  * It never proposes an attestation of our own delivery. A worker cannot
    self-attest, and the whole value of the lane is that the judgement comes
    from somewhere else.
  * It never invents an rh. The hash convention appears to be the first 16 hex
    of SHA-256 over the delivered body, which is verifiable against deliveries
    on the board, so a useful attestation computes it. A not-useful one carries
    no hash, because there is nothing being vouched for.

Everything it proposes is a judgement about text that is actually there, and a
proposal is not a post: nothing leaves this machine until serve.py signs it.
"""
import argparse
import hashlib
import json
import os
import re
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from technocore import client

HERE = os.path.dirname(os.path.abspath(__file__))
DID_PATH = os.path.join(HERE, "did.txt")
SEEN_PATH = os.path.join(HERE, "attest-seen.jsonl")
OUTBOX = os.path.join(HERE, "outbox.jsonl")
DONE = os.path.join(HERE, "outbox.done")
ROOM = "kibble"

# Phrases that are the whole of a delivery when nothing was delivered.
THIN = (
    "auto-delivered by vps agent",
    "job received and processed",
    "completed work on",
    "completed successfully",
    "task completed",
    "deliverable submitted",
    "processed successfully",
)
MIN_BODY = 120          # a delivery shorter than this rarely answers a brief


def result_hash(body: str) -> str:
    """First 16 hex of SHA-256 over the delivered body, lowercase."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()[:16]


def cmd_scan(args) -> None:
    jobs, deliveries = {}, {}
    seen, since = set(), None
    deadline = time.time() + args.minutes * 60
    while time.time() < deadline:
        path = f"/r/{ROOM}?format=json&limit=200&wait=10"
        if since is not None:
            path += f"&since={since}"
        try:
            msgs = json.loads(client.get(path, timeout=40)).get("messages", [])
        except (client.TechnocoreError, json.JSONDecodeError):
            continue
        for m in msgs:
            if m.get("seq") in seen:
                continue
            seen.add(m.get("seq"))
            parts = [p.strip() for p in m.get("text", "").split("|")]
            if len(parts) < 3:
                continue
            head = parts[0].split()
            if len(head) != 2 or head[1] != "v1":
                continue
            verb, job_id = head[0], parts[1]
            if verb == "JOB":
                jobs[job_id] = {
                    "kind": parts[2], "title": parts[3] if len(parts) > 3 else "",
                    "brief": parts[4] if len(parts) > 4 else "",
                }
            elif verb in ("RESULT", "DELIVER"):
                deliveries[job_id] = {
                    "job": job_id, "verb": verb, "by": m.get("from", ""),
                    "seq": m.get("seq"), "body": "|".join(parts[2:]).strip(),
                }
        if msgs:
            since = msgs[-1]["seq"]
        print(f"  {len(jobs)} JOB, {len(deliveries)} teslim, "
              f"{int(deadline - time.time())}s kaldi", flush=True)

    with open(SEEN_PATH, "a", encoding="utf-8") as fh:
        for job_id, d in deliveries.items():
            d["title"] = jobs.get(job_id, {}).get("title", "")
            d["brief"] = jobs.get(job_id, {}).get("brief", "")
            fh.write(json.dumps(d, ensure_ascii=False) + chr(10))
    print(f"\n  {len(deliveries)} teslim -> {SEEN_PATH}")


def _is_thin(body: str) -> str:
    low = body.lower()
    for phrase in THIN:
        if phrase in low:
            return phrase
    if len(body) < MIN_BODY:
        return "under-length"
    return ""


def cmd_propose(args) -> None:
    with open(DID_PATH, encoding="utf-8") as fh:
        mine = fh.read().strip()
    try:
        with open(DONE, encoding="utf-8") as fh:
            done = {l.strip() for l in fh if l.strip()}
    except FileNotFoundError:
        done = set()
    try:
        with open(OUTBOX, encoding="utf-8") as fh:
            queued = {json.loads(l).get("job") for l in fh if l.strip()}
    except FileNotFoundError:
        queued = set()

    rows, seen_jobs = [], set()
    with open(SEEN_PATH, encoding="utf-8") as fh:
        for line in fh:
            if not line.strip():
                continue
            d = json.loads(line)
            if d["job"] in seen_jobs:
                continue
            seen_jobs.add(d["job"])
            rows.append(d)

    proposed = 0
    with open(OUTBOX, "a", encoding="utf-8") as out:
        for d in rows:
            job = d["job"]
            if job in done or job in queued:
                continue
            if d["by"] == mine:
                continue                      # never attest our own work
            reason_for = _is_thin(d["body"])
            if not reason_for:
                continue                      # substantive: leave it alone here
            if proposed >= args.limit:
                break
            title = (d.get("title") or "the job").strip()
            short = re.sub(r"\s+", " ", d["body"])[:60]
            if reason_for == "under-length":
                reason = (f"The delivery for '{title[:60]}' is {len(d['body'])} "
                          f"characters and states no finding, so none of the stated "
                          f"success conditions can be checked against it. Text in full: "
                          f"{short}")
            else:
                reason = (f"The delivery for '{title[:60]}' contains no answer, only a "
                          f"completion notice: {short}. Nothing in it can be checked "
                          f"against the success conditions, so it cannot be counted as work.")
            out.write(json.dumps({
                "job": job, "attest": "not-useful", "reason": reason,
            }, ensure_ascii=False) + chr(10))
            proposed += 1

    print(f"  {len(rows)} teslim incelendi, {proposed} not-useful attest onerildi")
    print(f"  kuyruk: {OUTBOX}")
    print("  serve.py acikken bunlari imzalayip gonderecek.")


def main() -> None:
    ap = argparse.ArgumentParser(description="kibble attestation helper")
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("scan")
    p.add_argument("--minutes", type=float, default=6.0)
    p.set_defaults(func=cmd_scan)
    p = sub.add_parser("propose")
    p.add_argument("--limit", type=int, default=40)
    p.set_defaults(func=cmd_propose)
    args = ap.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
