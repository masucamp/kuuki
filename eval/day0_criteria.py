"""Day 0, step 3: the weakest concrete probes again, now with `criteria` (what true and false look like).

    uv run python eval/day0_criteria.py

Same scenes as eval/day0_probe.py, Japanese state and English translation. Output: results/day0_criteria.txt
"""
import json, sys, urllib.request
sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from day0_probe import SCENES

Q = {
    "has_error": {"type": "noul",
        "instructions": "Compare the stated facts with the latest utterance. Does the latest utterance contradict the stated facts?",
        "criteria": {"true": "The utterance states a number, version or fact that differs from the stated facts.",
                     "false": "The utterance is consistent with the stated facts, or no facts are stated."}},
    "polite_refusal": {"type": "noul",
        "instructions": "In Japanese business culture, is the latest utterance actually a soft 'no' even though it sounds positive?",
        "criteria": {"true": "Indirect refusal: e.g. 'we will consider it positively' (前向きに検討します) together with hedges like 'it will take time' or 'it is difficult'. No commitment, owner or date.",
                     "false": "Genuine agreement with a concrete commitment such as who will do it and by when."}},
    "is_vague": {"type": "noul",
        "instructions": "Does the latest instruction use vague references such as 'that matter' or 'the usual way' (例の件, 例のやり方) that the listener may not understand?",
        "criteria": {"true": "The instruction relies on unexplained shared context, and the listener seems unsure.",
                     "false": "The instruction is concrete and self-explanatory."}},
}
URL = "http://127.0.0.1:8000/v1/systemone"
pos = {k: 0 for k in Q}; neg = {k: 0 for k in Q}; npos = {k: 0 for k in Q}; nneg = {k: 0 for k in Q}
for sid, ja, en, positives in SCENES:
    for lang, st in (("ja", ja), ("en", en)):
        req = urllib.request.Request(URL, data=json.dumps({"state": st, "questions": Q}).encode(), headers={"Content-Type": "application/json"})
        a = json.load(urllib.request.urlopen(req))["answers"]
        print(f"{sid:20s} {lang} " + "  ".join(f"{k}={a[k]['noul']:.2f}{'*' if k in positives else ''}" for k in Q))
        for k in Q:
            ok = (a[k]["noul"] >= 0.5) == (k in positives)
            if k in positives: pos[k] += ok; npos[k] += 1
            else: neg[k] += ok; nneg[k] += 1
for k in Q: print(f"-> {k}: positives {pos[k]}/{npos[k]}, negatives {neg[k]}/{nneg[k]}")
