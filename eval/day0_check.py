"""Day0 go/no-go: Decider 2B が日本語の会議 state で使えるかと、手元GPUでのレイテンシを見る。

    uv run python eval/day0_check.py [--url http://127.0.0.1:8000] [--runs 10]

同じ場面を「問いが英語」「問いが日本語」の2通りで投げ、期待ラベルとの一致とレイテンシを出す。
"""

from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.request

QUESTIONS_EN = {
    "speak_now": {
        "type": "noul",
        "instructions": "Should the AI teammate interject in the meeting right now, after the latest utterance?",
    },
    "move": {
        "type": "choice",
        "instructions": "If the AI teammate interjects now, what is the most appropriate move?",
        "criteria": {
            "backchannel": "A short acknowledgement to show it is listening.",
            "clarify": "Ask what an ambiguous or vague expression refers to.",
            "correct": "Point out a factual error in the latest utterance.",
            "add_info": "Add a relevant fact nobody has mentioned yet.",
            "refocus": "Bring the discussion back to the agenda.",
            "invite_quiet": "Invite a participant who has not spoken yet and is relevant.",
            "summarize": "Summarize what has been decided so far.",
            "stay_silent": "Nothing useful to add; stay quiet.",
        },
    },
    "delivery": {
        "type": "choice",
        "instructions": "Considering who spoke and the hierarchy in the room, how should the AI deliver its point?",
        "criteria": {
            "direct": "Say it plainly to the whole room.",
            "cushioned": "Say it to the room, but softly and politely.",
            "private_dm_later": "Do not say it in the meeting; send a private message to the speaker later.",
        },
    },
    "tatemae": {
        "type": "score",
        "instructions": "How much does the latest utterance actually mean 'no', despite its polite surface?",
        "criteria": ["sincere_yes", "uncertain", "polite_no"],
    },
}

QUESTIONS_JA = {
    "speak_now": {
        "type": "noul",
        "instructions": "直近の発言を受けて、AIの同席者は今この会議で口を挟むべきか？",
    },
    "move": {
        "type": "choice",
        "instructions": "AIが今口を挟むとしたら、何をするのが最も適切か？",
        "criteria": {
            "backchannel": "聞いていることを示す短い相づち",
            "clarify": "あいまいな表現が何を指すか確認する",
            "correct": "直近の発言の事実誤りを指摘する",
            "add_info": "まだ誰も言っていない関連情報を補足する",
            "refocus": "議題に話を戻す",
            "invite_quiet": "まだ発言していない関係者に話を振る",
            "summarize": "ここまでの決定事項をまとめる",
            "stay_silent": "特に言うことはないので黙っている",
        },
    },
    "delivery": {
        "type": "choice",
        "instructions": "発言者と場の上下関係をふまえ、AIはどう伝えるべきか？",
        "criteria": {
            "direct": "全員の前ではっきり言う",
            "cushioned": "全員の前で、やわらかく丁寧に言う",
            "private_dm_later": "会議では言わず、あとで発言者に個別メッセージを送る（根回し）",
        },
    },
    "tatemae": {
        "type": "score",
        "instructions": "直近の発言は、丁寧な表面とは裏腹に実質『No』である度合いはどれくらいか？",
        "criteria": ["本心のYes", "どちらとも言えない", "遠回しのNo"],
    },
}

# expect: 正解と考えるラベル（None は判定しない）。tatemae は 0=sincere_yes, 1=uncertain, 2=polite_no
SCENES = [
    {
        "id": "factual_error_peer",
        "state": "会議: 新機能のリリース計画（参加者: 佐藤=開発リーダー, 鈴木=開発, AI）\n"
        "前提: 決済APIのv1は先月で提供終了し、現在はv2のみ利用可能。\n"
        "鈴木: 来月のリリースでは決済APIのv1を使って実装します。",
        "expect": {"speak_now": True, "move": "correct", "delivery": "direct"},
    },
    {
        "id": "factual_error_boss",
        "state": "会議: 来期予算の検討（参加者: 山田部長, 高橋=課長, 若手2名, AI）\n"
        "前提: 資料によると昨年の売上は前年比108%。\n"
        "山田部長: 去年は売上が前年比120%も伸びたんだから、来期の予算は思い切って増やそう。",
        "expect": {"move": "correct", "delivery": "private_dm_later"},
    },
    {
        "id": "smooth_flow",
        "state": "会議: 週次定例（参加者: 佐藤, 鈴木, 田中, AI）\n"
        "佐藤: 先週の課題は全部クローズしました。\n"
        "鈴木: ありがとうございます。じゃあ次のアジェンダに進みます。",
        "expect": {"speak_now": False, "move": "stay_silent"},
    },
    {
        "id": "polite_no_client",
        "state": "会議: 取引先A社への新プラン提案（参加者: A社 木村課長, 当社 営業2名, 海外メンバーのJohn, AI）\n"
        "当社営業: 来月から新プランに切り替えていただけないでしょうか。\n"
        "木村課長: なるほど、前向きに検討させていただきます。ただ、社内の調整にかなりお時間をいただくかと…。",
        "expect": {"tatemae": 2},
    },
    {
        "id": "sincere_yes",
        "state": "会議: 社内ツール改善（参加者: 佐藤, 鈴木, AI）\n"
        "佐藤: ログ画面にフィルタを付ける案、どうですか。\n"
        "鈴木: いいですね、それでいきましょう。金曜までに私が実装します。",
        "expect": {"tatemae": 0, "speak_now": False},
    },
    {
        "id": "off_topic",
        "state": "会議: 障害の再発防止策（60分、残り5分。決めるべきこと: 再発防止策の担当者）\n"
        "佐藤: そういえば駅前に新しいラーメン屋ができましたよね。\n"
        "鈴木: あそこ行きました、味噌がおすすめです。\n"
        "田中: 今度みんなで行きましょうか、来週の金曜とか。",
        "expect": {"speak_now": True, "move": "refocus"},
    },
    {
        "id": "quiet_expert",
        "state": "会議: DBの移行方針（参加者: 佐藤, 鈴木, 伊藤=DB担当で移行経験あり・まだ一度も発言していない, AI）\n"
        "佐藤: Auroraに移すかDynamoDBに移すか、正直決めきれないですね。\n"
        "鈴木: 誰か過去に移行やった人いないですかね…。",
        "expect": {"speak_now": True, "move": "invite_quiet"},
    },
    {
        "id": "vague_reference",
        "state": "会議: 新規案件のキックオフ（参加者: 佐藤, 新しく参加した中村, AI）\n"
        "佐藤: じゃあ例の件は、前回と同じ例のやり方で進めておいてください。\n"
        "中村: あ、はい…。",
        "expect": {"speak_now": True, "move": "clarify"},
    },
]


def post(url: str, body: dict) -> tuple[dict, float]:
    req = urllib.request.Request(
        f"{url}/v1/systemone",
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json"},
    )
    t0 = time.perf_counter()
    with urllib.request.urlopen(req) as res:
        payload = json.load(res)
    return payload, (time.perf_counter() - t0) * 1000


def judge(answers: dict, expect: dict) -> dict[str, bool]:
    result = {}
    for qid, want in expect.items():
        a = answers[qid]
        if a["type"] == "noul":
            result[qid] = (a["noul"] >= 0.5) == want
        elif a["type"] == "choice":
            result[qid] = a["choice"] == want
        else:
            result[qid] = round(a["score"]) == want
    return result


def fmt(a: dict) -> str:
    if a["type"] == "noul":
        return f"P(yes)={a['noul']:.2f}"
    if a["type"] == "choice":
        top = max(a["probabilities"].values())
        return f"{a['choice']} (p={top:.2f}, conf={a['confidence']:.2f})"
    return f"{a['score']:.2f} (conf={a['confidence']:.2f})"


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--url", default="http://127.0.0.1:8000")
    ap.add_argument("--runs", type=int, default=10)
    args = ap.parse_args()

    for _ in range(3):  # warmup
        post(args.url, {"state": SCENES[0]["state"], "questions": QUESTIONS_EN})

    for lang, questions in (("en", QUESTIONS_EN), ("ja", QUESTIONS_JA)):
        print(f"\n===== questions: {lang} =====")
        hits = total = 0
        server_ms, wall_ms = [], []
        for scene in SCENES:
            body = {"state": scene["state"], "questions": questions}
            for _ in range(args.runs):
                payload, wall = post(args.url, body)
                server_ms.append(payload["latency_ms"])
                wall_ms.append(wall)
            answers = payload["answers"]
            verdict = judge(answers, scene["expect"])
            hits += sum(verdict.values())
            total += len(verdict)
            print(f"\n[{scene['id']}]")
            for qid, a in answers.items():
                mark = {True: "OK ", False: "NG "}.get(verdict.get(qid), "   ")
                want = scene["expect"].get(qid, "")
                print(f"  {mark}{qid:10s} {fmt(a)}" + (f"   expected={want}" if qid in verdict else ""))
        q = statistics.quantiles(server_ms, n=20)
        print(f"\n-> 一致 {hits}/{total}")
        print(
            f"-> latency server: median {statistics.median(server_ms):.0f}ms, p95 {q[18]:.0f}ms"
            f" / wall: median {statistics.median(wall_ms):.0f}ms (4問バッチ, n={len(server_ms)})"
        )


if __name__ == "__main__":
    main()
