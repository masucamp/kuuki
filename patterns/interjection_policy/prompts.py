"""The same rules as thresholds.yaml, written for an LLM.

Used wherever an LLM makes the decision instead of the thresholds (an LLM judge baseline,
or the agent itself when System 1 escalates), so that both sides apply one set of rules.
"""

MOVE_RULES = """Judge only the latest utterance (and, for clarify, the instruction just before a hesitant reply).
Anything said earlier in the meeting has already been handled: never bring it up again.

move (pick one):
- correct: the latest utterance contradicts the stated facts (前提となる事実)
- clarify: the latest instruction (or the one just before a hesitant reply) refers to the task or method only with vague words (例の件, 例のやり方, いつものやつ, いい感じに) without saying concretely what it is
- invite_quiet: someone asked the room a question nobody has answered, and a participant marked as not having spoken yet is the right expert for it
- refocus: the last two utterances in a row are off the agenda
- stay_silent: none of the above clearly applies. Prefer this when unsure.

delivery (only when move is not stay_silent; one of direct, cushioned, private_dm_later):
- for move correct: direct. A speaker marked （上位者） is corrected too: direct when the contradiction is unmistakable (a number or date that plainly differs from the stated facts), otherwise private_dm_later
- for move clarify: private_dm_later if the person who gave the vague instruction is marked （上位者）; otherwise direct
- for move invite_quiet or refocus: cushioned

tatemae: true if the latest utterance is a reply to a request or proposal and is actually a polite "no" in Japanese business culture (e.g. 前向きに検討します with hedges, 難しいかもしれません, 善処します, 機会があれば). false for a genuine yes with a concrete commitment, or if it is not a reply to a request."""
