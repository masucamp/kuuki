"""Day0 切り分け: 精度の低さは「日本語」のせいか「問いが抽象的」なせいか。

具体的な noul の問いに分解し、同じ場面を日本語 state / 英訳 state で比べる。

    uv run python eval/day0_probe.py
"""

from __future__ import annotations

import json
import urllib.request

URL = "http://127.0.0.1:8000/v1/systemone"

PROBES = {
    "has_error": "Does the latest utterance contain a factual error, given the stated facts?",
    "polite_refusal": "Is the latest utterance a polite way of declining or saying no?",
    "is_vague": "Does the latest utterance rely on a vague reference (like 'that thing') that a newcomer could not understand?",
    "off_agenda": "Has the recent conversation drifted away from the meeting agenda?",
    "needs_silent_expert": "Is there a participant who has not spoken yet and is the right person for the current question?",
    "speaker_is_senior": "Is the speaker of the latest utterance senior to most others in the room?",
}

# (id, ja_state, en_state, 正解が True の probe)
SCENES = [
    (
        "factual_error_peer",
        "会議: 新機能のリリース計画（参加者: 佐藤=開発リーダー, 鈴木=開発, AI）\n前提: 決済APIのv1は先月で提供終了し、現在はv2のみ利用可能。\n鈴木: 来月のリリースでは決済APIのv1を使って実装します。",
        "Meeting: release plan (Sato=dev lead, Suzuki=developer, AI)\nFact: Payment API v1 was shut down last month; only v2 is available.\nSuzuki: For next month's release, we'll implement it with Payment API v1.",
        {"has_error"},
    ),
    (
        "factual_error_boss",
        "会議: 来期予算の検討（参加者: 山田部長, 高橋=課長, 若手2名, AI）\n前提: 資料によると昨年の売上は前年比108%。\n山田部長: 去年は売上が前年比120%も伸びたんだから、来期の予算は思い切って増やそう。",
        "Meeting: next year's budget (Director Yamada, Manager Takahashi, 2 juniors, AI)\nFact: per the report, last year's sales were 108% of the year before.\nDirector Yamada: Sales grew 120% last year, so let's boldly increase next year's budget.",
        {"has_error", "speaker_is_senior"},
    ),
    (
        "polite_no_client",
        "会議: 取引先A社への新プラン提案（参加者: A社 木村課長, 当社 営業2名, 海外メンバーのJohn, AI）\n当社営業: 来月から新プランに切り替えていただけないでしょうか。\n木村課長: なるほど、前向きに検討させていただきます。ただ、社内の調整にかなりお時間をいただくかと…。",
        "Meeting: proposing a new plan to client A (Manager Kimura of A, 2 of our sales reps, John from overseas, AI)\nSales: Could you switch to the new plan starting next month?\nManager Kimura: I see. We will consider it positively. However, internal coordination may take quite a long time...",
        {"polite_refusal"},
    ),
    (
        "sincere_yes",
        "会議: 社内ツール改善（参加者: 佐藤, 鈴木, AI）\n佐藤: ログ画面にフィルタを付ける案、どうですか。\n鈴木: いいですね、それでいきましょう。金曜までに私が実装します。",
        "Meeting: internal tool improvements (Sato, Suzuki, AI)\nSato: How about adding a filter to the log screen?\nSuzuki: Sounds good, let's do it. I'll implement it by Friday.",
        set(),
    ),
    (
        "off_topic",
        "会議: 障害の再発防止策（60分、残り5分。決めるべきこと: 再発防止策の担当者）\n佐藤: そういえば駅前に新しいラーメン屋ができましたよね。\n鈴木: あそこ行きました、味噌がおすすめです。\n田中: 今度みんなで行きましょうか、来週の金曜とか。",
        "Meeting: incident prevention (60 min, 5 min left. To decide: owner of the prevention plan)\nSato: By the way, a new ramen shop opened near the station.\nSuzuki: I went there, the miso is great.\nTanaka: Shall we all go sometime, maybe next Friday?",
        {"off_agenda"},
    ),
    (
        "quiet_expert",
        "会議: DBの移行方針（参加者: 佐藤, 鈴木, 伊藤=DB担当で移行経験あり・まだ一度も発言していない, AI）\n佐藤: Auroraに移すかDynamoDBに移すか、正直決めきれないですね。\n鈴木: 誰か過去に移行やった人いないですかね…。",
        "Meeting: DB migration (Sato, Suzuki, Ito = DB owner with migration experience, has not spoken yet, AI)\nSato: Honestly I can't decide between Aurora and DynamoDB.\nSuzuki: Has anyone here done a migration before...?",
        {"needs_silent_expert"},
    ),
    (
        "vague_reference",
        "会議: 新規案件のキックオフ（参加者: 佐藤, 新しく参加した中村, AI）\n佐藤: じゃあ例の件は、前回と同じ例のやり方で進めておいてください。\n中村: あ、はい…。",
        "Meeting: new project kickoff (Sato, Nakamura who just joined, AI)\nSato: So, for that matter, please proceed the usual way, same as last time.\nNakamura: Uh, okay...",
        {"is_vague"},
    ),
]


def ask(state: str) -> dict:
    body = {"state": state, "questions": {k: {"type": "noul", "instructions": v} for k, v in PROBES.items()}}
    req = urllib.request.Request(URL, data=json.dumps(body).encode(), headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as res:
        return json.load(res)


def main() -> None:
    score = {"ja": [0, 0], "en": [0, 0]}
    print(f"{'scene':20s} {'probe':20s}  want   ja     en")
    for sid, ja, en, positives in SCENES:
        a_ja, a_en = ask(ja)["answers"], ask(en)["answers"]
        for k in PROBES:
            want = k in positives
            row = []
            for lang, ans in (("ja", a_ja), ("en", a_en)):
                p = ans[k]["noul"]
                ok = (p >= 0.5) == want
                score[lang][0] += ok
                score[lang][1] += 1
                row.append(f"{p:.2f}{'' if ok else '✗'}")
            print(f"{sid:20s} {k:20s}  {str(want):5s}  {row[0]:6s} {row[1]:6s}")
    for lang, (hit, n) in score.items():
        print(f"-> {lang} state: {hit}/{n}")


if __name__ == "__main__":
    main()
