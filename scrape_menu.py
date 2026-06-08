#!/usr/bin/env python3
"""광명융합기술교육원 점심(중식) 메뉴를 스크래핑해
Microsoft Teams(Power Automate)로 전송하는 봇.

환경변수
    WEBHOOK_URL    (필수) Power Automate HTTP 트리거 URL
    WEBHOOK_TYPE   (선택) discord | slack | teams  (기본값: teams)
    OPENAI_API_KEY (선택) 있으면 칼로리 추정을 메시지에 추가
"""

import os
import sys
import base64
import io
from datetime import datetime, timezone, timedelta

import requests
from bs4 import BeautifulSoup

MENU_URL = "https://www.kopo.ac.kr/gm/content.do?menu=12623"
KST = timezone(timedelta(hours=9))
WEEKDAY_KO = ["월요일", "화요일", "수요일", "목요일", "금요일", "토요일", "일요일"]

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0 Safari/537.36"
    )
}


# ---------------------------------------------------------------------------
# 스크래핑 / 파싱
# ---------------------------------------------------------------------------

def fetch_html(url=MENU_URL):
    resp = requests.get(url, headers=HEADERS, timeout=15)
    resp.raise_for_status()
    if not resp.encoding or resp.encoding.lower() == "iso-8859-1":
        resp.encoding = resp.apparent_encoding or "utf-8"
    return resp.text


def find_menu_table(soup):
    """요일 행이 가장 많은 <table>을 식단표로 선택 (클래스명 무관)."""
    best, best_score = None, 0
    for table in soup.find_all("table"):
        score = sum(
            1 for tr in table.find_all("tr")
            if (c := tr.find(["th", "td"])) and c.get_text(strip=True) in WEEKDAY_KO
        )
        if score > best_score:
            best, best_score = table, score
    return best


def parse_week_menu(html):
    soup = BeautifulSoup(html, "html.parser")
    table = find_menu_table(soup)
    if table is None:
        return {}

    rows = table.find_all("tr")

    # 헤더에서 '중식' 컬럼 위치 파악 (못 찾으면 기본 2번)
    lunch_idx = 2
    for row in rows:
        texts = [c.get_text(strip=True) for c in row.find_all(["th", "td"])]
        if "중식" in texts:
            lunch_idx = texts.index("중식")
            break

    week = {}
    for row in rows:
        cells = row.find_all(["th", "td"])
        if not cells:
            continue
        day = cells[0].get_text(strip=True)
        if day not in WEEKDAY_KO:
            continue
        lunch = cells[lunch_idx].get_text(separator=" ", strip=True) if len(cells) > lunch_idx else ""
        week[day] = lunch
    return week


def clean_menu_items(raw):
    if not raw:
        return []
    # Windows 줄바꿈(\r\n) 또는 단독 \r 제거 후 쉼표로 분리
    raw = raw.replace("\r\n", " ").replace("\r", " ").replace("\n", " ")
    parts = [p.strip(" ,") for p in raw.split(",")]
    return [p for p in parts if p]


def get_today_menu(html, today_kr=None):
    if today_kr is None:
        today_kr = WEEKDAY_KO[datetime.now(KST).weekday()]
    week = parse_week_menu(html)
    return today_kr, clean_menu_items(week.get(today_kr, ""))


# ---------------------------------------------------------------------------
# (선택) OpenAI 칼로리 추정
# ---------------------------------------------------------------------------

def estimate_calories(items):
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not items:
        return None
    try:
        prompt = (
            "다음은 오늘 구내식당 점심 메뉴야. 한 끼 기준 총 열량(kcal)을 대략 추정하고 "
            "한 문장으로 짧게 코멘트해줘. 형식은 '약 OOO kcal — 코멘트' 한 줄로만.\n"
            "메뉴: " + ", ".join(items)
        )
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.5,
            },
            timeout=30,
        )
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[AI] 칼로리 추정 실패: {e}", file=sys.stderr)
        return None


def evaluate_meal(items):
    """오늘 급식 메뉴를 학생 입장에서 재밌게 평가한다."""
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not items:
        return None
    try:
        prompt = (
            "너는 급식을 누구보다 사랑하는 한국 대학생이야.\n"
            "오늘 급식 메뉴를 보고 솔직하고 재밌게 평가해줘.\n\n"
            "평가 기준:\n"
            "- 인기 메뉴(닭갈비, 탕수육, 돈까스 등)가 있으면 기대감 높게\n"
            "- 메뉴 구성이 풍성하면 긍정적으로\n"
            "- 다이어트 메뉴나 채소 위주면 살짝 아쉬움\n"
            "- 면류(냉모밀, 짜장 등)가 있으면 특별 언급\n"
            "- 학생들 반응을 예측해서 '줄 설 것 같다', '오늘 일찍 가야겠다' 등 포함\n\n"
            f"오늘 메뉴: {', '.join(items)}\n\n"
            "2~3문장으로 짧고 재밌게 평가해줘. 이모지 1~2개 포함."
        )
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.9,  # 높게 설정해 매일 다른 평가 생성
            },
            timeout=30,
        )
        resp.raise_for_status()
        result = resp.json()["choices"][0]["message"]["content"].strip()
        print(f"[AI] 급식 평가 완료")
        return result
    except Exception as e:
        print(f"[AI] 급식 평가 실패: {e}", file=sys.stderr)
        return None
    """메뉴별 잠재 알레르기 유발 성분을 분석한다.

    한국 식품 알레르기 표시 기준 21종 기반:
    난류, 우유, 메밀, 땅콩, 대두, 밀, 고등어, 게, 새우, 돼지고기,
    복숭아, 토마토, 아황산류, 호두, 닭고기, 쇠고기, 오징어, 조개류, 잣,
    글루텐, 견과류
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not items:
        return None
    try:
        allergen_list = (
            "난류(계란), 우유(유제품), 메밀, 땅콩, 대두(콩), 밀(글루텐), "
            "고등어, 게, 새우, 돼지고기, 복숭아, 토마토, 아황산류, 호두, "
            "닭고기, 쇠고기, 오징어, 조개류, 잣, 견과류"
        )
        menu_str = "\n".join(f"- {it}" for it in items)
        prompt = (
            f"한국 식품 알레르기 표시 기준 21종은 다음과 같아: {allergen_list}\n\n"
            "아래 급식 메뉴 각각에 포함될 수 있는 알레르기 유발 성분을 분석해줘.\n"
            "확실하지 않아도 재료상 포함 가능성이 있으면 표시해.\n"
            "없으면 '없음'으로 표시해.\n\n"
            f"메뉴:\n{menu_str}\n\n"
            "아래 형식으로만 답해줘 (다른 말 없이, 메뉴 순서 그대로):\n"
            + "\n".join(f"{it}: [알레르기1, 알레르기2, ...]" for it in items)
        )
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.3,   # 낮게 설정해 일관성 있는 결과
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()

        # 파싱: "메뉴명: [A, B, C]" 형태 → {메뉴명: [A, B, C]}
        result = {}
        for line in raw.splitlines():
            if ":" not in line:
                continue
            name, _, allergens_raw = line.partition(":")
            name = name.strip()
            # 매칭되는 메뉴명 찾기 (GPT가 메뉴명을 살짝 바꾸는 경우 대비)
            matched = next((it for it in items if it in name or name in it), name)
            allergens = [
                a.strip().strip("[]")
                for a in allergens_raw.replace("[", "").replace("]", "").split(",")
                if a.strip() and a.strip() not in ("없음", "[]", "")
            ]
            result[matched] = allergens

        print(f"[AI] 알레르기 분석 완료: {len(result)}개 항목")
        return result

    except Exception as e:
        print(f"[AI] 알레르기 분석 실패: {e}", file=sys.stderr)
        return None


def recommend_dinner(items):
    """점심 메뉴를 분석해 영양 균형을 고려한 저녁 메뉴를 추천한다.

    분석 기준:
      - 단백질/채소/탄수화물 비율
      - 나트륨·칼로리 과다 여부 (국물 메뉴, 볶음류 등)
      - 맛 프로필 (매운맛, 기름진 맛, 담백함)
      - 조리 방식 중복 회피 (점심에 볶음이 많으면 저녁은 찜/삶기 위주 등)
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not items:
        return None
    try:
        prompt = (
            "너는 한국인 영양사야. 오늘 점심 급식 메뉴를 보고 저녁 식사 메뉴를 추천해줘.\n\n"
            "분석 규칙:\n"
            "1. 점심에 고기류가 많으면 → 저녁은 채소·두부·생선 위주\n"
            "2. 점심에 국물(찌개·국)이 있으면 → 저녁은 국물 없이 담백하게\n"
            "3. 점심이 고탄수화물(밥·면)이면 → 저녁은 잡곡밥 또는 밥 양 줄이기\n"
            "4. 점심이 매운 메뉴 위주면 → 저녁은 순한 맛\n"
            "5. 점심 칼로리가 높으면 → 저녁은 700kcal 이하 목표\n"
            "6. 채소 반찬이 부족했으면 → 저녁은 나물·샐러드 강화\n\n"
            f"오늘 점심 메뉴: {', '.join(items)}\n\n"
            "아래 형식으로만 답해줘 (다른 말 없이):\n"
            "추천메뉴: [메뉴1], [메뉴2], [메뉴3], [메뉴4]\n"
            "추천이유: [점심 분석 + 저녁 보완 포인트를 한 문장으로]"
        )
        resp = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}",
                     "Content-Type": "application/json"},
            json={
                "model": "gpt-4o-mini",
                "messages": [{"role": "user", "content": prompt}],
                "temperature": 0.7,
            },
            timeout=30,
        )
        resp.raise_for_status()
        raw = resp.json()["choices"][0]["message"]["content"].strip()

        # 파싱: "추천메뉴: A, B, C\n추천이유: ..." 형태
        dinner_items, reason = [], ""
        for line in raw.splitlines():
            if line.startswith("추천메뉴:"):
                dinner_items = [x.strip() for x in line.replace("추천메뉴:", "").split(",") if x.strip()]
            elif line.startswith("추천이유:"):
                reason = line.replace("추천이유:", "").strip()

        if not dinner_items:
            return None
        print(f"[AI] 저녁 추천 완료: {dinner_items}")
        return {"items": dinner_items, "reason": reason}

    except Exception as e:
        print(f"[AI] 저녁 추천 실패: {e}", file=sys.stderr)
        return None


def compress_image_b64(b64_data, max_size=512, quality=75):
    """base64 PNG 이미지를 리사이즈 + JPEG 압축해서 용량을 줄인다.

    1024x1024 PNG (~1.5MB) → 512x512 JPEG quality=75 (~80KB 수준)

    Args:
        max_size: 가로/세로 최대 픽셀 (기본 512)
        quality:  JPEG 품질 0~95 (기본 75, 낮을수록 작아짐)
    """
    from PIL import Image
    try:
        raw = base64.b64decode(b64_data)
        img = Image.open(io.BytesIO(raw)).convert("RGB")

        # 비율 유지하며 리사이즈
        img.thumbnail((max_size, max_size), Image.LANCZOS)

        buf = io.BytesIO()
        img.save(buf, format="JPEG", quality=quality, optimize=True)
        compressed = base64.b64encode(buf.getvalue()).decode()

        original_kb  = len(b64_data) * 3 // 4 // 1024
        compressed_kb = len(compressed) * 3 // 4 // 1024
        print(f"[AI] 이미지 압축: {original_kb}KB → {compressed_kb}KB")
        return compressed
    except Exception as e:
        print(f"[AI] 이미지 압축 실패 (원본 사용): {e}", file=sys.stderr)
        return b64_data  # 실패 시 원본 그대로


def generate_menu_image(items):
    """gpt-image-1 로 급식 트레이 이미지를 생성하고 imgbb 에 업로드해 URL을 반환한다.

    필요한 Secret:
        OPENAI_API_KEY  - 이미지 생성
        IMGBB_API_KEY   - 이미지 호스팅 (https://api.imgbb.com 에서 무료 발급)
    """
    openai_key = os.environ.get("OPENAI_API_KEY")
    imgbb_key   = os.environ.get("IMGBB_API_KEY")
    if not openai_key or not items:
        return None

    menu_str = ", ".join(items)
    prompt = (
        "A photorealistic top-down food photo of a Korean cafeteria lunch tray. "
        "The tray is a rectangular stainless steel divided tray with multiple compartments. "
        f"Each compartment contains a different Korean dish: {menu_str}. "
        "The food is freshly served, colorful, and appetizing. "
        "Clean background, bright natural lighting, food photography style."
    )

    # ── 1단계: gpt-image-1 로 base64 이미지 생성 ──────────────────────────
    try:
        resp = requests.post(
            "https://api.openai.com/v1/images/generations",
            headers={"Authorization": f"Bearer {openai_key}",
                     "Content-Type": "application/json"},
            json={
                "model": "gpt-image-1",
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
            },
            timeout=120,
        )
        if not resp.ok:
            print(f"[AI] 이미지 생성 실패 ({resp.status_code}): {resp.text}", file=sys.stderr)
            return None

        b64_data = resp.json()["data"][0].get("b64_json")
        if not b64_data:
            print("[AI] 이미지 응답에 b64_json 없음", file=sys.stderr)
            return None
        print("[AI] 이미지 생성 완료 (gpt-image-1)")

        # 업로드 전 압축 (1024px PNG → 512px JPEG)
        b64_data = compress_image_b64(b64_data)

    except Exception as e:
        print(f"[AI] 이미지 생성 예외: {e}", file=sys.stderr)
        return None

    # ── 2단계: imgbb 에 업로드해서 공개 URL 획득 ──────────────────────────
    if not imgbb_key:
        print("[AI] IMGBB_API_KEY 없음 → 이미지 생략 (Secret 에 추가하면 Teams 카드에 표시됨)", file=sys.stderr)
        return None
    try:
        upload = requests.post(
            "https://api.imgbb.com/1/upload",
            data={"key": imgbb_key, "image": b64_data},
            timeout=60,
        )
        if not upload.ok:
            print(f"[AI] imgbb 업로드 실패 ({upload.status_code}): {upload.text}", file=sys.stderr)
            return None

        image_url = upload.json()["data"]["url"]
        print(f"[AI] 이미지 업로드 완료")
        return image_url

    except Exception as e:
        print(f"[AI] imgbb 업로드 예외: {e}", file=sys.stderr)
        return None


# ---------------------------------------------------------------------------
# 메시지 구성
# ---------------------------------------------------------------------------

def build_message_plain(day, items, calories, today_str,
                        dinner=None, allergies=None, evaluation=None):
    """플랫폼 공통으로 쓸 수 있는 마크다운 없는 메시지."""
    if not items:
        return f"🍽️ {today_str} ({day})\n오늘은 등록된 중식 메뉴가 없어요. (휴무/공휴일일 수 있어요)"
    lines = [f"🍽️ {today_str} ({day}) 오늘의 점심", ""]
    lines += [f"• {it}" for it in items]

    if evaluation:
        lines += ["", f"🎯 오늘의 급식 평가", evaluation]

    if calories:
        lines += ["", f"📊 {calories}"]

    if allergies:
        lines += ["", "⚠️ 알레르기 정보 (AI 추정)"]
        for item in items:
            allergen_list = allergies.get(item, [])
            tag = ", ".join(allergen_list) if allergen_list else "해당없음"
            lines.append(f"• {item}: {tag}")
        lines += ["", "※ 정확한 정보는 급식실에 문의하세요."]

    if dinner:
        lines += ["", "🌙 오늘 저녁 추천"]
        lines += [f"• {it}" for it in dinner["items"]]
        if dinner["reason"]:
            lines += ["", f"💡 {dinner['reason']}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Webhook 전송 — Power Automate(Teams) / Discord / Slack
# ---------------------------------------------------------------------------

def send_teams_powerautomate(webhook_url, text, image_url=None):
    """PA 'Post card in a chat or channel' 액션에 맞춘 Adaptive Card 페이로드.
    Teams Bot Framework 호환을 위해 TextBlock 기본 속성만 사용한다.
    """
    body_blocks = []

    if image_url:
        body_blocks.append({
            "type": "Image",
            "url": image_url,
            "size": "Stretch",
            "altText": "오늘의 급식 사진",
        })

    for line in text.splitlines():
        if not line.strip():
            continue
        block = {
            "type": "TextBlock",
            "text": line,
            "wrap": True,
        }
        # 제목 줄만 굵게 (weight 는 Teams 지원)
        if line.startswith("🍽️") or line.startswith("🌙") or \
                line.startswith("⚠️") or line.startswith("🎯"):
            block["weight"] = "Bolder"

        body_blocks.append(block)

    adaptive_card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.2",   # Teams 가 안정적으로 지원하는 버전
        "body": body_blocks,
    }
    r = requests.post(webhook_url, json=adaptive_card, timeout=15)
    r.raise_for_status()


def send_discord(webhook_url, text, image_url=None):
    embed = {"description": text, "color": 0x4CAF50}
    if image_url:
        embed["image"] = {"url": image_url}
    r = requests.post(webhook_url, json={"embeds": [embed]}, timeout=15)
    r.raise_for_status()


def send_slack(webhook_url, text, image_url=None):
    blocks = [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]
    if image_url:
        blocks.append({"type": "image", "image_url": image_url, "alt_text": "오늘의 급식"})
    r = requests.post(webhook_url, json={"text": text, "blocks": blocks}, timeout=15)
    r.raise_for_status()


SENDERS = {
    "teams":   send_teams_powerautomate,
    "discord": send_discord,
    "slack":   send_slack,
}


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main():
    webhook_url = os.environ.get("WEBHOOK_URL")
    webhook_type = os.environ.get("WEBHOOK_TYPE", "teams").lower()  # 기본값 teams

    if not webhook_url:
        print("환경변수 WEBHOOK_URL 이 필요합니다.", file=sys.stderr)
        sys.exit(1)
    sender = SENDERS.get(webhook_type)
    if sender is None:
        print(f"알 수 없는 WEBHOOK_TYPE: {webhook_type} (teams|discord|slack)", file=sys.stderr)
        sys.exit(1)

    now = datetime.now(KST)
    today_str = now.strftime("%Y-%m-%d")

    html = fetch_html()
    day, items = get_today_menu(html)
    print(f"파싱 결과: {today_str} {day} -> {items}")

    calories   = estimate_calories(items)
    evaluation = evaluate_meal(items)
    allergies  = analyze_allergies(items)
    dinner     = recommend_dinner(items)
    image_url  = generate_menu_image(items)
    text = build_message_plain(day, items, calories, today_str,
                               dinner=dinner, allergies=allergies,
                               evaluation=evaluation)

    sender(webhook_url, text, image_url)
    print(f"전송 완료 ({webhook_type}): 메뉴 {len(items)}개")


if __name__ == "__main__":
    main()