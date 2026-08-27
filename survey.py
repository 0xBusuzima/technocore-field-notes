#!/usr/bin/env python3
"""Measure a Technocore room instead of guessing about it.

    python survey.py collect lobby --minutes 10 --out lobby.jsonl
    python survey.py analyse lobby.jsonl

`collect` long-polls with ?since=<seq>&wait=10, which is the only way to get a
contiguous sample: a plain read returns at most 200 messages, and a busy room
moves further than that in under twenty seconds. Gaps are counted, not hidden.

`analyse` reports what the room is actually made of. Nothing here is a judgement
about any operator; the numbers are what the server served.
"""
import argparse
import collections
import json
import os
import re
import statistics
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

for _stream in (sys.stdout, sys.stderr):
    if hasattr(_stream, "reconfigure"):
        _stream.reconfigure(encoding="utf-8", errors="replace")

from technocore import client

# Phrases the lobby is saturated with. Matched on the lowercased text.
GENERIC = (
    "gm", "good morning", "all good", "checking node health", "node health",
    "heartbeat", "check-in", "checkin", "checking in", "still here", "online",
    "just making sure", "hello world", "ping", "pong", "status: ok", "all systems",
    "operational", "running normally", "no issues", "standing by", "synced",
)
HYPE = ("airdrop", "$flop", "flop token", "eligib", "reward", "points", "rank")


def _norm_template(text: str) -> str:
    """Collapse a message to its shape, so near-identical posts group together."""
    t = text.lower()
    t = re.sub(r"did:key:z[1-9a-hj-np-zA-HJ-NP-Z]+", "<did>", t)
    t = re.sub(r"\b0x[0-9a-f]+\b", "<hex>", t)
    t = re.sub(r"\b[0-9a-f]{8,}\b", "<hex>", t)
    t = re.sub(r"\d+", "0", t)
    t = re.sub(r"https?://\S+", "<url>", t)
    t = re.sub(r"\s+", " ", t).strip()
    return t


def cmd_collect(args) -> None:
    seen = set()
    gaps = 0
    expected = None
    started = time.time()
    deadline = started + args.minutes * 60
    written = 0

    with open(args.out, "w", encoding="utf-8") as fh:
        since = None
        while time.time() < deadline:
            path = f"/r/{client._seg(args.room)}?format=json&limit=200&wait=10"
            if since is not None:
                path += f"&since={since}"
            try:
                payload = json.loads(client.get(path, timeout=40))
            except client.TechnocoreError as exc:
                print(f"  [uyari] {exc}", file=sys.stderr)
                time.sleep(2)
                continue

            msgs = payload.get("messages", [])
            for m in msgs:
                seq = m.get("seq")
                if seq in seen:
                    continue
                if expected is not None and seq > expected:
                    gaps += seq - expected
                seen.add(seq)
                expected = seq + 1
                fh.write(json.dumps(m, ensure_ascii=False) + chr(10))
                written += 1
            if msgs:
                since = msgs[-1]["seq"]
            fh.flush()
            left = int(deadline - time.time())
            print(f"  {written} mesaj, {gaps} atlanan, {left}s kaldi", flush=True)

    elapsed = time.time() - started
    print(f"\n  {written} mesaj -> {args.out}")
    print(f"  sure {elapsed:.0f}s, atlanan {gaps}, hiz {written / (elapsed / 60):.0f}/dk")


def cmd_analyse(args) -> None:
    rows = []
    with open(args.path, encoding="utf-8") as fh:
        for line in fh:
            if line.strip():
                rows.append(json.loads(line))
    if not rows:
        raise SystemExit("Dosya bos.")

    total = len(rows)
    texts = [r.get("text", "") for r in rows]
    authors = [r.get("from", "") for r in rows]

    signed = [a for a in authors if a.startswith("did:key:")]
    by_author = collections.Counter(authors)
    by_text = collections.Counter(texts)
    by_template = collections.Counter(_norm_template(t) for t in texts)

    generic = sum(1 for t in texts if any(g in t.lower() for g in GENERIC))
    hype = sum(1 for t in texts if any(h in t.lower() for h in HYPE))
    mentions = sum(1 for t in texts if "did:key:" in t)

    def pct(n):
        return f"{100 * n / total:5.1f}%"

    print(f"\n=== {args.path} ===")
    print(f"  mesaj                 {total}")
    print(f"  seq araligi           {rows[0].get('seq')}..{rows[-1].get('seq')}")
    print(f"  farkli yazar          {len(by_author)}")
    print(f"  imzali (did:key)      {pct(len(signed))}  ({len(signed)})")
    print(f"  imzasiz (nick)        {pct(total - len(signed))}")

    print("\n  -- tekrar --")
    repeated = sum(c for t, c in by_text.items() if c > 1)
    print(f"  birebir tekrar eden   {pct(repeated)}")
    tpl_repeated = sum(c for t, c in by_template.items() if c > 1)
    print(f"  ayni sablon           {pct(tpl_repeated)}")
    print(f"  farkli sablon         {len(by_template)}  (mesaj basina {len(by_template)/total:.2f})")

    print("\n  -- icerik --")
    print(f"  jenerik check-in      {pct(generic)}")
    print(f"  airdrop/puan gecen    {pct(hype)}")
    print(f"  baska DID'e deginen   {pct(mentions)}")

    print("\n  -- en cok tekrarlanan 8 sablon --")
    for tpl, count in by_template.most_common(8):
        print(f"  {count:5d}x  {tpl[:96]}")

    # The interesting question is not how often a text repeats, but across how
    # many distinct keys. One key repeating itself is a chatty agent. Hundreds
    # of keys emitting the same sentence is one prompt behind many identities.
    tpl_authors = collections.defaultdict(set)
    for r in rows:
        tpl_authors[_norm_template(r.get("text", ""))].add(r.get("from", ""))

    shared = {t: a for t, a in tpl_authors.items() if len(a) > 1}
    msgs_in_shared = sum(by_template[t] for t in shared)
    widest = sorted(shared.items(), key=lambda kv: -len(kv[1]))

    print("\n  -- ayni sablonu paylasan farkli anahtarlar --")
    print(f"  birden fazla anahtarin kullandigi sablon  {len(shared)} / {len(by_template)}")
    print(f"  bu sablonlardaki mesajlarin payi          {pct(msgs_in_shared)}")
    if widest:
        top_tpl, top_auth = widest[0]
        print(f"  en genis sablon                           {len(top_auth)} farkli anahtar")
    print("  en genis 5 sablon (farkli anahtar sayisi):")
    for tpl, auths in widest[:5]:
        print(f"  {len(auths):5d} anahtar  {tpl[:82]}")

    solo = [a for a, c in by_author.items() if c == 1]
    print(f"\n  tek mesaj atip susan yazar                {len(solo)} ({100*len(solo)/len(by_author):.0f}% yazarin)")

    print("\n  -- en aktif 8 yazar --")
    for author, count in by_author.most_common(8):
        share = 100 * count / total
        print(f"  {count:5d}  ({share:4.1f}%)  {author[:56]}")

    top10 = sum(c for _, c in by_author.most_common(10))
    print(f"\n  ilk 10 yazarin payi   {pct(top10)}")

    # Authors whose posts arrive on a metronome are almost certainly scripted.
    print("\n  -- zamanlama duzenliligi (>=8 mesaji olan yazarlar) --")
    stamps = collections.defaultdict(list)
    for r in rows:
        ts = r.get("ts", "")
        try:
            t = time.mktime(time.strptime(ts[:19], "%Y-%m-%dT%H:%M:%S"))
        except ValueError:
            continue
        stamps[r.get("from", "")].append(t)

    regular = []
    for author, times in stamps.items():
        if len(times) < 8:
            continue
        gaps = [b - a for a, b in zip(sorted(times), sorted(times)[1:])]
        gaps = [g for g in gaps if g > 0]
        if len(gaps) < 4:
            continue
        med = statistics.median(gaps)
        if med <= 0:
            continue
        spread = statistics.pstdev(gaps) / med
        regular.append((spread, med, len(times), author))
    regular.sort()
    for spread, med, n, author in regular[:8]:
        print(f"  sapma {spread:5.2f}  medyan {med:6.1f}s  {n:4d} mesaj  {author[:44]}")
    if regular:
        tight = sum(1 for s, _, _, _ in regular if s < 0.25)
        print(f"  sapmasi 0.25 altinda olan yazar: {tight}/{len(regular)}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Technocore room survey")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("collect")
    p.add_argument("room", nargs="?", default="lobby")
    p.add_argument("--minutes", type=float, default=10.0)
    p.add_argument("--out", default="lobby.jsonl")
    p.set_defaults(func=cmd_collect)

    p = sub.add_parser("analyse")
    p.add_argument("path")
    p.set_defaults(func=cmd_analyse)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
