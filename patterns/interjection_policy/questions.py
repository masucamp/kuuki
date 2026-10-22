"""Typed probe questions for the interjection policy.

Day0 finding: asking Decider abstract questions ("should the AI speak now?", "what move?")
gave near-uniform answers. Concrete yes/no probes with explicit `criteria` separate well,
and the language of the state (Japanese or English) made no difference. So the policy asks
only concrete probes in one batch and derives the move from them (see policy.py).

Questions and criteria are written in English; the meeting state can stay in Japanese.
Facts the app already knows (who is senior, who has not spoken) are not asked here; they are
computed in code and written into the state or passed to the policy.
"""

from __future__ import annotations

PROBES: dict[str, dict] = {
    "has_error": {
        "type": "noul",
        "instructions": "Compare the stated facts with the latest utterance. Does the latest utterance contradict the stated facts?",
        "criteria": {
            "true": "The utterance states a number, version or fact that differs from the stated facts.",
            "false": "The utterance is consistent with the stated facts, or no facts are stated.",
        },
    },
    "is_vague": {
        "type": "noul",
        "instructions": "Does the latest utterance refer to a task or method only with vague words such as 例の件, 例のやり方, あれ ('that matter', 'the usual way') without saying concretely what it is?",
        "criteria": {
            "true": "A key part of the instruction is only named by a vague reference, so what to actually do is not stated.",
            "false": "What to do is stated concretely, or the utterance contains no instruction.",
        },
    },
    "off_agenda": {
        "type": "noul",
        "instructions": "Has the recent conversation drifted away from the meeting agenda?",
        "criteria": {
            "true": "The last few utterances are about something unrelated to the agenda.",
            "false": "The conversation is about the agenda.",
        },
    },
    "needs_silent_expert": {
        "type": "noul",
        "instructions": "Is there a participant who has not spoken yet and is the right person for the current question?",
        "criteria": {
            "true": "Someone with relevant expertise has stayed silent while the others are stuck on that topic.",
            "false": "Nobody silent is especially relevant, or the relevant person has already spoken.",
        },
    },
    "is_response_to_request": {
        "type": "noul",
        "instructions": "Is the latest utterance a reply to a request or proposal made to the speaker?",
        "criteria": {
            "true": "Someone asked or proposed something to the speaker, and the latest utterance answers it.",
            "false": "The latest utterance is a statement, a question, small talk, or an instruction.",
        },
    },
    "polite_refusal": {
        "type": "noul",
        "instructions": "In Japanese business culture, is the latest utterance actually a soft 'no' even though it sounds positive?",
        "criteria": {
            "true": "Indirect refusal: e.g. 'we will consider it positively' (前向きに検討します) together with hedges like 'it will take time' or 'it is difficult'. No commitment, owner or date.",
            "false": "Genuine agreement with a concrete commitment such as who will do it and by when.",
        },
    },
    "open_question": {
        "type": "noul",
        "instructions": "Is the latest utterance a question put to the room that nobody has answered yet?",
        "criteria": {
            "true": "The speaker asks the group something (e.g. 'does anyone know...?') and is waiting for someone to answer.",
            "false": "The latest utterance is a statement, an answer, or a question already answered.",
        },
    },
}
