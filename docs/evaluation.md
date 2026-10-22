# Evaluation

How Kuuki's decisions were tested, what the numbers are, and how much to trust them.
Raw outputs and re-scoring commands: [results/](../results/README.md).

## Setup

- **System 1:** `StrandsAgents/strands-decider-2B-qwen3.5-v1-2610` served locally with
  `strands-decider serve` on a laptop RTX 4060 (8 GB, WSL2).
- **System 2 / baselines:** Claude Sonnet 5.5 and Claude Haiku 5.5 on Amazon Bedrock
  (`ap-northeast-1`), thinking turned off.
- **Language:** meeting states in Japanese; probes, criteria and LLM rules in English.

## 1. Day 0: can strands-decider read a Japanese meeting?

Eight short meeting scenes with expected labels (seven of them for the concrete probes).

| Approach | Correct |
|---|---|
| Ask the decision directly (speak now? which move? how? is it a polite no?), English questions | 8/16 |
| Same, Japanese questions | 5/16 |
| Split into concrete yes/no probes, Japanese state | 38/42 |
| Same probes, English translation of the state | 38/42 |
| The three weakest probes again, with `criteria` | 6/8 positives, 28/34 negatives |

- Direct questions gave answers close to uniform. The polite-no score sat near the middle
  of the scale for every scene.
- Concrete probes separated well, and **the language of the state made no difference**.
- Adding `criteria` (what true and false look like) fixed the weakest probes. The polite
  refusal 「前向きに検討させていただきます。ただ…」 went from 0.23 to 0.91.
- Latency: a 4-probe batch took a median of 230 ms (p95 241 ms). Seven probes take about 330 ms.
  No cloud GPU was needed.

## 2. The 70 labeled scenes

`scenarios/eval_scenes_ja.yaml`: 70 small meetings (2 to 4 utterances each). The label is the
expected decision after the last utterance.

| Expected | Scenes |
|---|---|
| stay silent (genuine yeses, answered questions, on-topic chat, concrete instructions…) | 31 |
| correct a factual error (6 by a peer, 5 by a senior person) | 11 |
| clarify a vague instruction (5 from a peer, 4 from a senior person) | 9 |
| invite a silent expert | 10 |
| refocus after two off-agenda utterances | 9 |
| polite "no" (tatemae), expected move stay silent | 11 (included in the 31) |

Three splits, written and used in this order:

| Split | Scenes | Role |
|---|---|---|
| `tune` | 31 | Thresholds were fitted on these |
| `test` | 19 | Scored once; the gray zones were set **after** seeing its mismatches |
| `test2` | 20 | Written after thresholds, gray zones and probes were frozen. **The independent check** |

- **Scoring:** exact match on move, delivery (when labelled) and tatemae. Each scene is replayed
  utterance by utterance, so rules such as "two in a row" see the history.
- **Delivery labels:** senior people are corrected too. In the room or by private message
  depends on confidence, so both are accepted for a senior's factual error. A senior's vague
  instruction is expected to be clarified by private message.

## 3. strands-decider alone

| | tune (31) | test (19) | test2 (20) |
|---|---|---|---|
| Exact match | 26 | 13 | 16 |

Where strands-decider is weak (probabilities on the scenes it missed):

- **Vague references:** positives scored 0.46 to 0.66, negatives up to 0.56. They overlap, so
  no threshold separates them.
- **Errors that need reasoning:** an end-of-life runtime version, date arithmetic or "more than
  half" vs "4 of 12" scored 0.17 to 0.22.
- **Work-adjacent drift:** talk about another project or a house move scored 0.1 to 0.2 for "off agenda".
- **Short set phrases:** 「善処します」 ("I'll do what I can") scored 0.26 as a polite refusal.

These overlaps became the **gray zones** in `thresholds.yaml`. In a gray zone the threshold is
not trusted and the decision goes to the LLM.

## 4. strands-decider vs LLM judge vs hybrid

- **LLM judge:** Claude gets the final meeting state, with senior people marked, and the same
  rules written as instructions (`patterns/interjection_policy/prompts.py`). It returns
  move, delivery and tatemae as JSON. One call per decision.
- **Hybrid (Kuuki):** strands-decider plus the thresholds. When they say "stay silent" but a
  probe is in its gray zone, the LLM decides instead. 16 of 70 decisions (23%) escalated.

### Accuracy (exact match)

| | decider | Haiku 5.5 | decider + Haiku | Sonnet 5.5 | **decider + Sonnet** |
|---|---|---|---|---|---|
| tune (31) | 26 | 28 | 29 | 31 | **31** |
| test (19) | 13 | 16 | 16 | 18 | **17** |
| **test2 (20, independent)** | **16** | 16 | 17 | 20 | **18** |
| All (70) | 55 (79%) | 60 (86%) | 62 (89%) | 69 (99%) | **66 (94%)** |
| Interjected when it should stay silent | 2 | 3 | 3 | 0 | 2 |

By expected move (all 70):

| Expected | decider | Haiku | decider + Haiku | Sonnet | decider + Sonnet |
|---|---|---|---|---|---|
| stay silent (31) | 27 | 28 | 27 | 31 | 28 |
| correct (11) | 7 | 11 | 11 | 11 | 11 |
| clarify (9) | 4 | 4 | 6 | 9 | 9 |
| invite_quiet (10) | 10 | 9 | 10 | 10 | 10 |
| refocus (9) | 7 | 8 | 8 | 8 | 8 |
| tatemae (11) | 9 | 11 | 10 | 11 | 10 |

### Latency and cost per decision

| | Median | p95 | LLM cost / 1000 decisions |
|---|---|---|---|
| strands-decider (local RTX 4060) | 331 ms | 508 ms | $0 |
| Haiku 5.5 | 1,264 ms | 1,926 ms | $0.14 |
| decider + Haiku | 338 ms | 1,806 ms | $0.03 |
| Sonnet 5.5 | 1,366 ms | 2,306 ms | $2.68 |
| **decider + Sonnet** | **337 ms** | 1,927 ms | **$0.61** |

- About 830 input and 105 output tokens per LLM call. Costs use Anthropic list prices; Bedrock
  regional prices can differ slightly.
- A one-hour meeting is roughly 600 utterances: about $1.60 with Sonnet alone and about $0.37
  with the hybrid.
- The p95 of the hybrid is the escalated decisions (about 2 s).

### Reading the numbers

- **Sonnet alone is the most accurate** (69/70). strands-decider alone loses on reasoning-heavy
  errors and vague references.
- **The hybrid reaches 94% (66/70) at about a quarter of Sonnet's latency and cost.** 77% of
  decisions are made without asking the LLM; it is called only to write the words when Kuuki acts. On the independent `test2` scenes it scored 18/20, against 16/20
  for strands-decider alone and 20/20 for Sonnet alone.
- **The hybrid's misses are the ones System 1 is confident about.** None of the 16 escalated
  decisions was wrong. The four misses never reached the LLM: a "correction" in a scene with no
  stated facts (has_error 0.41), a drift to a house move that scored only 0.14 as off-agenda,
  inviting someone who was silent but not an expert, and a polite no whose "is this a reply?" probe
  sat just under its gate. Gray zones cannot catch confident mistakes; better probes or more labeled
  data can.
- **Haiku is close to strands-decider** on the hard moves. It applied the rules loosely: it read a
  newcomer's hesitant 「はい…」 as a polite refusal and missed most vague instructions.
- strands-decider's value here is not accuracy. It is speed, running locally, and calibrated
  numbers that make "I am not sure" detectable.

## 5. Caveats

- 70 scenes is small, and one author wrote and labelled them all. The labels encode one view of
  Japanese meeting etiquette.
- Only `test2` is a clean held-out check; `test` informed the gray zones.
- The LLM judge sees the final state once; strands-decider is replayed utterance by utterance.
  This is needed for "two in a row" and favours neither side on the other moves.
- LLM decisions vary between runs. Each comparison is a single run.
- The state marks "has not spoken yet" only from the third utterance on, so in two-utterance
  scenes the silent expert is not marked. Both systems still found the expert in most of them.
- A wording slip in the LLM rules once made Sonnet write the move name into the delivery field
  (8/8 correct moves scored 3/11). Rules for an LLM need the same care as probes.
- Scripts are replayed text, not live speech recognition.
