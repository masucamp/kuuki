"""Decider vs LLM judge vs hybrid on the labeled eval scenes.

- decider: probe probabilities (cached by run_eval.py) + threshold table
- llm:     one Bedrock call per scene on the final state, deciding move/delivery/tatemae directly
- hybrid:  decider; when it says "stay silent" but a probe is in its gray zone, the LLM decides

    uv run python eval/run_eval.py --split all        # once, to fill the probability cache
    uv run --env-file .env python eval/compare_llm.py --split all [--model haiku|sonnet]
    # published results, no model calls
    uv run python eval/compare_llm.py --model sonnet --rescore results/compare_sonnet.jsonl
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
from pathlib import Path

import yaml
from botocore.exceptions import BotoCoreError, ClientError

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from patterns.interjection_policy.decider import DeciderClient  # noqa: E402
from agent.llm_judge import LLMJudge  # noqa: E402
from agent.room_state import Meeting, RoomState  # noqa: E402
from patterns.interjection_policy.policy import InterjectionPolicy  # noqa: E402

SCENES = Path("scenarios/eval_scenes_ja.yaml")
CACHE = Path("logs/eval_probs.json")
# USD per 1M tokens (Anthropic list price; Bedrock regional pricing may differ slightly)
PRICE = {"haiku": (0.10, 0.50), "sonnet": (2.00, 10.00)}
MOVES = ["stay_silent", "correct", "clarify", "invite_quiet", "refocus"]


def correct(dec: dict, exp: dict) -> bool:
    want_move = exp.get("move", "stay_silent")
    if dec["move"] != want_move or dec["tatemae"] != exp.get("tatemae", False):
        return False
    want = exp.get("delivery")
    return want is None or dec["delivery"] in (want if isinstance(want, list) else [want])


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--split", choices=["tune", "test", "test2", "all"], default="all")
    ap.add_argument("--model", choices=["haiku", "sonnet"], default="haiku")
    ap.add_argument("--out", type=Path, default=Path("logs/compare_llm.jsonl"))
    ap.add_argument("--rescore", type=Path, help="re-score a saved .jsonl against the current labels; no model calls")
    args = ap.parse_args()

    scenes = yaml.safe_load(SCENES.read_text())["scenes"]
    if args.rescore:
        labels = {s["id"]: s["utterances"][-1]["expect"] for s in scenes}
        rows = [json.loads(line) for line in args.rescore.read_text().splitlines() if line.strip()]
        for r in rows:
            r["want"] = labels[r["id"]]
            r["ok"] = {m: correct(r[m], r["want"]) for m in ("decider", "llm", "hybrid")}
        report(rows, args.model)
        return 0

    if args.split != "all":
        scenes = [s for s in scenes if s["split"] == args.split]
    cache = json.loads(CACHE.read_text())
    policy, judge, decider = InterjectionPolicy(), LLMJudge(args.model), DeciderClient()

    rows = []
    try:
        for s in scenes:
            meeting = Meeting.from_dict(s)
            room = RoomState(meeting)
            policy.reset()
            for u, p in zip(meeting.utterances, cache[s["id"]], strict=True):
                room.add(u)
                d = policy.decide(p, recent_senior=room.recent_senior())
            dec = {"move": d.move, "delivery": d.delivery, "tatemae": d.tatemae}
            decider_ms = decider.ask(room.render(), policy.questions).server_ms  # latency of one decision

            j = judge.judge(room.render(mark_senior=True))
            llm = {"move": j.move, "delivery": j.delivery, "tatemae": j.tatemae}

            gray = policy.should_escalate(d, has_facts=bool(meeting.facts))
            hyb = llm if gray else dec
            exp = meeting.utterances[-1].expect
            rows.append({
                "id": s["id"], "split": s["split"], "want": exp, "note": s.get("note", ""),
                "decider": dec, "llm": llm, "hybrid": hyb, "gray": gray, "llm_reason": j.reason,
                "ok": {"decider": correct(dec, exp), "llm": correct(llm, exp), "hybrid": correct(hyb, exp)},
                "decider_ms": decider_ms, "llm_ms": j.latency_ms,
                "hybrid_ms": decider_ms + (j.latency_ms if gray else 0.0),
                "tokens": [j.input_tokens, j.output_tokens],
            })
            mark = "".join("o" if rows[-1]["ok"][k] else "x" for k in ("decider", "llm", "hybrid"))
            print(f"{s['id']} [{mark}] decider={dec['move']:12s} llm={j.move:12s} gray={','.join(gray) or '-'}", flush=True)
    except (ClientError, BotoCoreError) as e:
        print(f"Bedrock error: {e}", file=sys.stderr)
        return 1

    args.out.parent.mkdir(exist_ok=True)
    args.out.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
    report(rows, args.model)
    return 0


def report(rows: list[dict], model: str) -> None:
    methods = ["decider", "llm", "hybrid"]
    print(f"\n{'':16s}" + "".join(f"{m:>12s}" for m in methods))
    for split in ["tune", "test", "test2", None]:
        rs = [r for r in rows if split is None or r["split"] == split]
        if rs:
            label = f"{split or 'all'} ({len(rs)})"
            print(f"{label:16s}" + "".join(f"{sum(r['ok'][m] for r in rs):>9d}/{len(rs):<2d}" for m in methods))
    print("\nby expected move (all scenes)")
    for mv in MOVES:
        rs = [r for r in rows if r["want"].get("move", "stay_silent") == mv]
        print(f"  {mv:14s}" + "".join(f"{sum(r['ok'][m] for r in rs):>9d}/{len(rs):<2d}" for m in methods))
    rs = [r for r in rows if r["want"].get("tatemae")]
    print(f"  {'tatemae':14s}" + "".join(f"{sum(r['ok'][m] for r in rs):>9d}/{len(rs):<2d}" for m in methods))
    for m in methods:
        fp = sum(r["want"].get("move", "stay_silent") == "stay_silent" and r[m]["move"] != "stay_silent" for r in rows)
        print(f"  interjected when it should stay silent, {m}: {fp}")

    n = len(rows)
    tin = sum(r["tokens"][0] for r in rows)
    tout = sum(r["tokens"][1] for r in rows)
    pin, pout = PRICE[model]
    llm_cost = (tin * pin + tout * pout) / 1e6 / n
    esc = sum(bool(r["gray"]) for r in rows)
    print("\nper decision")
    for m in methods:
        ms = [r[f"{m}_ms"] for r in rows]
        cost = {"decider": 0.0, "llm": llm_cost, "hybrid": llm_cost * esc / n}[m]
        print(f"  {m:8s} median {statistics.median(ms):6.0f}ms  p95 {sorted(ms)[int(len(ms) * 0.95) - 1]:6.0f}ms"
              f"  LLM cost ${cost * 1000:.3f} / 1000 decisions")
    print(f"  hybrid escalation rate {esc}/{n} ({esc / n:.0%})")
    print(f"  LLM tokens: in {tin / n:.0f} / out {tout / n:.0f} per call ({model})")


if __name__ == "__main__":
    sys.exit(main())
