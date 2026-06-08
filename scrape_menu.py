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


def generate_menu_image(items):
    """DALL-E로 급식 트레이에 오늘 메뉴가 담긴 사진을 생성한다.
    dall-e-3 실패 시 dall-e-2 로 자동 재시도한다.
    """
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key or not items:
        return None

    menu_str = ", ".join(items)
    prompt = (
        "A photorealistic top-down food photo of a Korean cafeteria lunch tray. "
        "The tray is a rectangular stainless steel divided tray with multiple compartments. "
        f"Each compartment contains a different Korean dish: {menu_str}. "
        "The food is freshly served, colorful, and appetizing. "
        "Clean white table background, bright natural lighting, food photography style."
    )

    for model in ("dall-e-3", "dall-e-2"):
        try:
            params = {
                "model": model,
                "prompt": prompt,
                "n": 1,
                "size": "1024x1024",
            }
            if model == "dall-e-3":
                params["quality"] = "standard"

            resp = requests.post(
                "https://api.openai.com/v1/images/generations",
                headers={"Authorization": f"Bearer {api_key}",
                         "Content-Type": "application/json"},
                json=params,
                timeout=60,
            )

            if not resp.ok:
                # 400 등 에러 시 상세 내용 출력 후 다음 모델로 폴백
                print(f"[AI] {model} 실패 ({resp.status_code}): {resp.text}", file=sys.stderr)
                continue

            image_url = resp.json()["data"][0]["url"]
            print(f"[AI] 이미지 생성 완료 ({model})")
            return image_url

        except Exception as e:
            print(f"[AI] {model} 예외: {e}", file=sys.stderr)
            continue

    print("[AI] 이미지 생성 실패: 모든 모델 시도 완료", file=sys.stderr)
    return None


# ---------------------------------------------------------------------------
# 메시지 구성
# ---------------------------------------------------------------------------

def build_message_plain(day, items, calories, today_str):
    """플랫폼 공통으로 쓸 수 있는 마크다운 없는 메시지."""
    if not items:
        return f"🍽️ {today_str} ({day})\n오늘은 등록된 중식 메뉴가 없어요. (휴무/공휴일일 수 있어요)"
    lines = [f"🍽️ {today_str} ({day}) 오늘의 점심", ""]
    lines += [f"• {it}" for it in items]
    if calories:
        lines += ["", f"📊 {calories}"]
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Webhook 전송 — Power Automate(Teams) / Discord / Slack
# ---------------------------------------------------------------------------

def send_teams_powerautomate(webhook_url, text, image_url=None):
    """PA 'Post card in a chat or channel' 액션에 맞춘 Adaptive Card 페이로드."""
    body_blocks = []

    # 이미지가 있으면 맨 위에 배치
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
        block = {"type": "TextBlock", "text": line, "wrap": True}
        if line.startswith("🍽️"):
            block["weight"] = "Bolder"
            block["size"] = "Medium"
            block["color"] = "Accent"
        body_blocks.append(block)

    adaptive_card = {
        "type": "AdaptiveCard",
        "$schema": "http://adaptivecards.io/schemas/adaptive-card.json",
        "version": "1.4",
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

    calories = estimate_calories(items)
    image_url = generate_menu_image(items)
    text = build_message_plain(day, items, calories, today_str)

    sender(webhook_url, text, image_url)
    print(f"전송 완료 ({webhook_type}): 메뉴 {len(items)}개")


if __name__ == "__main__":
    main()