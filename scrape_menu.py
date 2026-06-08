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
    parts = [p.strip(" ,") for p in raw.replace("\n", " ").split(",")]
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

def send_teams_powerautomate(webhook_url, text):
    """Power Automate 'When an HTTP request is received' 트리거에 맞춘 페이로드.

    PA 플로우에서 triggerBody()?['text'] 또는 triggerBody()?['message'] 로 읽어
    Teams 채널에 포스팅하도록 설정하면 됩니다.
    (플로우 편집 화면 참고 → 'Teams에 메시지 게시' 액션 → 메시지 필드에 수식 입력)
    """
    payload = {
        "text": text,       # Teams 메시지 본문
        "message": text,    # 플로우 설정에 따라 둘 중 하나를 씀
        "title": "오늘의 점심",
    }
    r = requests.post(webhook_url, json=payload, timeout=15)
    r.raise_for_status()


def send_discord(webhook_url, text):
    r = requests.post(webhook_url, json={"embeds": [{"description": text, "color": 0x4CAF50}]}, timeout=15)
    r.raise_for_status()


def send_slack(webhook_url, text):
    r = requests.post(webhook_url, json={"text": text}, timeout=15)
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
    text = build_message_plain(day, items, calories, today_str)

    sender(webhook_url, text)
    print(f"전송 완료 ({webhook_type}): 메뉴 {len(items)}개")


if __name__ == "__main__":
    main()