"""Score the interjection policy on the labeled eval scenes.

Each scene is replayed utterance by utterance (so `consecutive` rules see the history),
and the decision at the last utterance is compared with its `expect`.

    # call Decider and cache its probabilities
    uv run python eval/run_eval.py --split tune
    # re-score from the cache after editing thresholds.yaml (no Decider calls)
    uv run python eval/run_eval.py --split tune --from-cache
    # held-out scenes: run once at the end
    uv run python eval/run_eval.py --split test
    # published probabilities, no GPU needed
    uv run python eval/run_eval.py --split all --from-cache --cache results/eval_probs.json
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from collections import Counter
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patterns.interjection_policy.decider import DeciderClient  # noqa: E402
from agent.room_state import Meeting, RoomState  # noqa: E402
from patterns.interjection_policy.policy import InterjectionPolicy  # noqa: E402

SCENES = Path("scenarios/eval_scenes_ja.yaml")
CACHE = Path("logs/eval_probs.json")


def collect_probs(scenes: list[dict], decider: DeciderClient, policy: InterjectionPolicy) -> tuple[dict, list[float]]:
    """{scene_id: [probs per utterance]} from Decider, plus server latencies."""
    out, latencies = {}, []
    for s in scenes:
        room = RoomState(Meeting.from_dict(s))
        per_utt = []
        for u in room.meeting.utterances:
            room.add(u)
            r = decider.ask(room.render(), policy.questions)
            per_utt.append(r.probs)
            latencies.append(r.server_ms)
        out[s["id"]] = per_utt
    return out, latencies


def _as_list(v) -> list:
    return v if isinstance(v, list) else [v]


def score(scenes: list[dict], probs: dict, policy: InterjectionPolicy, verbose: bool) -> dict:
    rows = []
    for s in scenes:
        meeting = Meeting.from_dict(s)
        room = RoomState(meeting)
        policy.reset()
        for u, p in zip(meeting.utterances, probs[s["id"]], strict=True):
            room.add(u)
            d = policy.decide(p, recent_senior=room.recent_senior())
        exp = meeting.utterances[-1].expect
        want_move = exp.get("move", "stay_silent")
        want_tatemae = exp.get("tatemae", False)
        want_delivery = exp.get("delivery")
        rows.append({
            "id": s["id"], "note": s.get("note", ""),
            "want_move": want_move, "got_move": d.move,
            "want_delivery": want_delivery, "got_delivery": d.delivery,
            "want_tatemae": want_tatemae, "got_tatemae": d.tatemae,
            "move_ok": d.move == want_move,
            "delivery_ok": want_delivery is None or (d.move == want_move and d.delivery in _as_list(want_delivery)),
            "tatemae_ok": d.tatemae == want_tatemae,
            "probs": p, "reasons": d.reasons,
        })

    n = len(rows)
    all_ok = [r for r in rows if r["move_ok"] and r["delivery_ok"] and r["tatemae_ok"]]
    print(f"\nexact match {len(all_ok)}/{n}  (move {sum(r['move_ok'] for r in rows)}/{n},"
          f" delivery {sum(r['delivery_ok'] for r in rows)}/{n}, tatemae {sum(r['tatemae_ok'] for r in rows)}/{n})")

    print("\nby expected move")
    for m in ["stay_silent", "correct", "clarify", "invite_quiet", "refocus"]:
        rs = [r for r in rows if r["want_move"] == m]
        if rs:
            got = Counter(r["got_move"] for r in rs if not r["move_ok"])
            miss = ", ".join(f"{k}x{v}" for k, v in got.items())
            print(f"  {m:13s} {sum(r['move_ok'] for r in rs)}/{len(rs)}" + (f"   wrong: {miss}" if miss else ""))
    fp = [r for r in rows if r["want_move"] == "stay_silent" and r["got_move"] != "stay_silent"]
    print(f"  interjected when it should stay silent: {len(fp)}")

    tp = sum(r["want_tatemae"] and r["got_tatemae"] for r in rows)
    pp = sum(r["got_tatemae"] for r in rows)
    ap = sum(r["want_tatemae"] for r in rows)
    print(f"\ntatemae  precision {tp}/{pp}  recall {tp}/{ap}")

    bad = [r for r in rows if not (r["move_ok"] and r["delivery_ok"] and r["tatemae_ok"])]
    if bad:
        print("\nmismatches")
        for r in bad:
            got = r["got_move"] + (f"/{r['got_delivery']}" if r["got_delivery"] else "") + (" +tatemae" if r["got_tatemae"] else "")
            want = r["want_move"] + (f"/{'|'.join(_as_list(r['want_delivery']))}" if r["want_delivery"] else "") + (" +tatemae" if r["want_tatemae"] else "")
            print(f"  {r['id']} want={want:28s} got={got:28s} {r['note']}")
            if verbose:
                print("        " + "  ".join(f"{k}={v:.2f}" for k, v in r["probs"].items()))
    return {"rows": rows, "exact": len(all_ok), "n": n}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["tune", "test", "test2", "all"], default="tune")
    ap.add_argument("--from-cache", action="store_true", help="re-score cached probabilities, no Decider calls")
    ap.add_argument("-v", "--verbose", action="store_true", help="print probabilities of mismatches")
    ap.add_argument("--decider-url", default="http://127.0.0.1:8000")
    ap.add_argument("--cache", type=Path, default=CACHE, help="probability cache (results/eval_probs.json is the published one)")
    args = ap.parse_args()

    scenes = yaml.safe_load(SCENES.read_text())["scenes"]
    if args.split != "all":
        scenes = [s for s in scenes if s["split"] == args.split]
    policy = InterjectionPolicy()

    cache = json.loads(args.cache.read_text()) if args.cache.exists() else {}
    if args.from_cache:
        missing = [s["id"] for s in scenes if s["id"] not in cache]
        if missing:
            print(f"cache has no probabilities for {missing}; run without --from-cache first", file=sys.stderr)
            return 1
    else:
        probs, latencies = collect_probs(scenes, DeciderClient(args.decider_url), policy)
        cache.update(probs)
        args.cache.parent.mkdir(exist_ok=True)
        args.cache.write_text(json.dumps(cache, ensure_ascii=False, indent=1))
        q = statistics.quantiles(latencies, n=20)
        print(f"Decider: {len(latencies)} calls, median {statistics.median(latencies):.0f}ms, p95 {q[18]:.0f}ms")

    print(f"split={args.split}  scenes={len(scenes)}")
    score(scenes, cache, policy, args.verbose)
    return 0


if __name__ == "__main__":
    sys.exit(main())
