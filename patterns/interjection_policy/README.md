# Interjection policy

A reusable pattern for an AI teammate that listens to a conversation and has to decide,
after every utterance, **whether** to speak, **what** to do, and **how** to say it.
Most of the time the right answer is to stay quiet, so asking an LLM after every utterance
is mostly wasted time and money.

The pattern splits the decision in two:

- **System 1 (strands-decider, local, about 300 ms):** a batch of narrow yes/no probes
  about the latest utterance, turned into a decision by a threshold table in YAML.
- **System 2 (an LLM):** called only when System 1 says "stay silent" but one of its
  probes lands in a gray zone where the probe is known to be unreliable.

```
utterance ──> strands-decider: 7 probes in one batch ──> threshold table
                                                          ├─ stay silent (confident) ──> Deny: no model call
                                                          ├─ act (confident) ─────────> the LLM only writes the words
                                                          └─ stay silent, gray zone ──> the LLM decides (System 2)
```

On our 70 labeled meeting scenes (Japanese), System 1 plus Claude Sonnet 5.5 reached 94% exact
match at about a quarter of Sonnet's latency and cost:

| | exact match (70) | median latency per decision | LLM cost / 1000 decisions |
|---|---|---|---|
| strands-decider only | 55 | 331 ms | $0 |
| Claude Sonnet 5.5 only | 69 | 1,366 ms | $2.68 |
| **decider + Sonnet (this pattern)** | **66** | **337 ms** | **$0.61** |

77% of decisions are made without asking the LLM (it is still called to write the words when Kuuki acts), and none of the escalated ones was wrong. Details and caveats: [docs/evaluation.md](../../docs/evaluation.md).

## Files

| File | What it is |
|---|---|
| `questions.py` | The probes: concrete yes/no (`noul`) questions with `criteria` |
| `thresholds.yaml` | The policy: which probe triggers which move, how to deliver it, and the escalation gray zones |
| `policy.py` | `InterjectionPolicy`: probabilities to `Decision`, plus `should_escalate()` |
| `handler.py` | `InterjectionHandler`: a Strands `InterventionHandler` that applies the policy in `before_invocation` |
| `prompts.py` | The same rules written for an LLM, shared by System 2 and any LLM-judge baseline |
| `decider.py` | A minimal client for `strands-decider serve` |
| `example_strands.py` | A meeting replayed through a Strands agent gated by the handler |

## Use it with Strands

```python
from strands import Agent
from patterns.interjection_policy import InterjectionHandler, MOVE_RULES

handler = InterjectionHandler()          # talks to strands-decider at 127.0.0.1:8000
agent = Agent(model=..., system_prompt=f"...{MOVE_RULES}...", interventions=[handler])

handler.reset()                          # once per conversation
for utterance in conversation:
    room.add(utterance)
    result = agent(prompt_for(room), invocation_state={"room": room})
    verdict = handler.log[-1]            # silent | act | escalate, with the Decision and probabilities
```

`room` is anything that implements `RoomView`: `render()` (the state text for
strands-decider), `recent_senior()` (who spoke last, latest first) and `has_facts`.
What the handler returns:

| Verdict | Strands action | Effect |
|---|---|---|
| `silent` | `Deny` | The invocation is cancelled before any model call |
| `act` | `Transform` | `[interjection policy] System 1 decided: move=..., delivery=...` is appended to the prompt; the model only writes the words |
| `escalate` | `Transform` | `System 1 was unsure (...)` is appended; the model decides with `MOVE_RULES` and replies `SILENT` if nothing is needed |

Without Strands, call the policy directly:

```python
from patterns.interjection_policy import DeciderClient, InterjectionPolicy

policy, decider = InterjectionPolicy(), DeciderClient()
d = policy.decide(decider.ask(state, policy.questions).probs, recent_senior=[True, False])
if gray := policy.should_escalate(d, has_facts=True):
    ...  # ask your LLM
elif d.move != "stay_silent" or d.tatemae:
    ...  # write the words for d.move / d.delivery
```

## The threshold table

```yaml
moves:                     # first match wins
  - move: correct
    probe: has_error
    threshold: 0.3
  - move: invite_quiet
    probe: needs_silent_expert
    threshold: 0.6
    requires: {open_question: 0.5}   # all of these must also hold
    unless: {off_agenda: 0.6}        # none of these may hold
  - move: refocus
    probe: off_agenda
    threshold: 0.6
    consecutive: 2                   # must hold for 2 utterances in a row

delivery:
  default: {correct: direct, clarify: direct, invite_quiet: cushioned, refocus: cushioned}
  senior:                            # correcting a senior in front of the room needs certainty
    min_confidence_for_public: 0.85  # below it: private_dm_later
    lookback: {correct: 0, clarify: 1}

escalate:                            # gray zones measured on labeled data
  is_vague: {range: [0.45, 0.6]}
  has_error: {range: [0.15, 0.3], requires_facts: true}
```

The model answers questions; the YAML decides. Changing behaviour means changing a number
you can review in a diff, not rewording a prompt.

## What we learned building it

1. **Ask concrete probes, not the decision.** Asking strands-decider "should the AI speak
   now?" or "which move?" gave near-uniform answers (8/16 correct). The same scenes split
   into concrete probes ("does the latest utterance contradict the stated facts?") worked.
2. **Write `criteria` for every probe.** The `true` / `false` descriptions carry most of the
   signal. A polite Japanese "no" (前向きに検討します) went from 0.23 to 0.91 once the
   criteria described it.
3. **Don't ask what you already know.** While developing, "Is the speaker senior?" swung between 0.48 and 0.87
   for the same director. Seniority is in the participant list, so it is passed as data.
4. **Gate fragile probes with sturdy ones.** "Is this a polite refusal?" runs high on almost
   anything; it is only trusted when "is this a reply to a request?" is also high.
5. **Language was not the bottleneck.** Japanese and English states scored the same
   (38/42). Write the probes and criteria in English and keep the state as it is.
6. **Escalate uncertainty, not actions.** Only a "stay silent" with a gray-zone probe goes to
   the LLM. Second-guessing confident actions raised the escalation rate without improving
   accuracy.
7. **Keep the AI's own lines out of the System 1 state.** A reply to the AI looks like a
   reply to a request and triggers false refusals. Give them to the LLM instead, so it
   knows what it already said.

## Adapting it to your domain

1. Replace the probes in `questions.py` with the narrow questions your decision depends on.
2. Label 30 to 50 scenes and look at each probe's distribution on positives and negatives
   (`eval/run_eval.py` caches the probabilities, so re-scoring is free).
3. Set thresholds where the distributions separate; where they overlap, declare a gray zone
   in `escalate` instead of forcing a threshold.
4. Hold some scenes out and score them once at the end.

## Limitations

- Thresholds were tuned on 31 scenes and checked on 19 + 20 held-out scenes, all labeled by one author.
- Probes that need reasoning (date arithmetic, knowing that a version is end-of-life) and
  subtle topic drift are weak in strands-decider; the gray zones exist for them.
- The gray zones were set after looking at the first held-out mismatches. Only the last 20 scenes,
  written after everything was frozen, are a clean check (hybrid 18/20, strands-decider 16/20, Sonnet 20/20).
- System 1's confident mistakes are not escalated, so gray zones cannot fix them.
