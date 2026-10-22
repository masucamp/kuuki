# Raw results

The outputs behind the numbers in [docs/evaluation.md](../docs/evaluation.md). All scenes,
people and companies are fictional. Re-scoring needs neither a GPU nor AWS credentials.

| File | What it is |
|---|---|
| `eval_probs.json` | strands-decider probabilities for every utterance of the 70 evaluation scenes: `{scene_id: [{probe: P(true)}, ...]}` (one entry per utterance, last entry = the scored decision point) |
| `compare_sonnet.jsonl` | One row per scene: the decisions of strands-decider, Claude Sonnet 5.5 as an LLM judge, and the hybrid; the LLM's reason, latencies and token counts |
| `compare_haiku.jsonl` | The same with Claude Haiku 5.5 |
| `day0_abstract_questions.txt` | Day 0: asking strands-decider the decision directly (speak now? which move?) on 8 scenes |
| `day0_concrete_probes.txt` | Day 0: the same scenes as concrete yes/no probes, Japanese state vs English translation |
| `day0_criteria.txt` | Day 0: the three weakest probes again, with `criteria` |

The Day 0 files come from `eval/day0_check.py`, `eval/day0_probe.py` and `eval/day0_criteria.py`. A few labels in
them are Japanese: 一致 = match, 期待 / expected = the label, OK / NG = right / wrong.

## Re-score

```bash
# strands-decider + thresholds.yaml (edit the thresholds and re-run to see the effect)
uv run python eval/run_eval.py --split all --from-cache --cache results/eval_probs.json

# decider vs LLM vs hybrid, against the current labels
uv run python eval/compare_llm.py --model sonnet --rescore results/compare_sonnet.jsonl
uv run python eval/compare_llm.py --model haiku --rescore results/compare_haiku.jsonl
```

## Notes

- Both comparisons use the current escalation rule (escalate only when the thresholds say
  "stay silent" and a probe is in its gray zone) and the current LLM rules in `prompts.py`.
- `split` in each row: `tune` (thresholds fitted), `test` (seen once, informed the gray zones),
  `test2` (written after everything was frozen; the clean check).
- Latency in the `.jsonl` files is wall-clock from a laptop in Tokyo: strands-decider on a local
  RTX 4060, Bedrock in `ap-northeast-1` (Sonnet through the global inference profile).
