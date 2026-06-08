# 🍽️ 점심 메뉴 알림 봇 (광명융합기술교육원)

GitHub Actions 스케줄만으로 동작하는 **서버리스** 봇입니다.
매일 평일 아침 9시, 학교 식단 페이지에서 오늘의 중식 메뉴를 스크래핑해
Microsoft Teams(Power Automate)로 보내줍니다.

- 데이터 출처: `https://www.kopo.ac.kr/gm/content.do?menu=12623`
- 서버/EC2 불필요 — GitHub Actions 러너에서 실행
- (선택) OpenAI 키를 넣으면 **칼로리 추정**이 메시지에 자동 추가

---

## 토의 결과 요약

| 질문 | 결론 |
|---|---|
| GitHub Actions에서 스케줄 작업이 가능한가? | ✅ `on: schedule: cron` 으로 가능 (UTC 기준, 평일만 지정 가능) |
| Python을 실행해서 스크래핑이 가능한가? | ✅ `setup-python` + `pip install` 후 실행 가능 |
| 점심 메뉴 스크래핑 → webhook 봇이 가능한가? | ✅ 위 둘을 합치면 서버 없이 구현 가능 (이 레포가 그 결과) |

---

## 동작 원리

```
GitHub Actions (cron, 평일 09:00 KST)
        │
        ▼
scrape_menu.py
   1) 식단 페이지 HTML 요청 (requests)
   2) 표에서 오늘 요일의 '중식' 칸 파싱 (BeautifulSoup)
   3) (선택) OpenAI 로 칼로리 추정
   4) Power Automate webhook 으로 메시지 전송
        │
        ▼
   Microsoft Teams 채팅방
```

---

## 레포 구조

```
lunch-bot/
├── .github/
│   └── workflows/
│       └── lunch-bot.yml      # 스케줄 + 실행 정의
├── scrape_menu.py             # 스크래핑 + 전송 로직
├── requirements.txt
└── README.md
```

---

## 설치 순서

### 1. 레포 만들기

이 4개 파일을 새 GitHub 레포에 올립니다. `.github/workflows/` 경로를 그대로 유지해야 합니다.

### 2. GitHub Secrets 등록

레포 → **Settings → Secrets and variables → Actions → New repository secret**

| Secret 이름 | 필수 | 값 |
|---|---|---|
| `WEBHOOK_URL` | ✅ | Power Automate HTTP 트리거 URL |
| `WEBHOOK_TYPE` | 선택 | `teams` / `discord` / `slack` (미설정 시 기본값 `teams`) |
| `OPENAI_API_KEY` | 선택 | OpenAI 키 (있으면 칼로리 추정 자동 추가) |

> webhook URL 에는 인증 토큰(`sig=...`)이 포함되어 있습니다.
> 코드에 직접 넣지 말고 반드시 Secret 으로 관리하세요.

### 3. Power Automate 플로우 설정 확인

봇이 보내는 JSON 페이로드 형식입니다.

```json
{
  "text": "🍽️ 2026-06-09 (월요일) 오늘의 점심\n\n• 치즈닭갈비\n• 흑미밥\n...",
  "message": "(text 와 동일)",
  "title": "오늘의 점심"
}
```

Power Automate 플로우의 "Teams에 메시지 게시" 액션 메시지 필드에
`@{triggerBody()?['text']}` 수식을 입력하면 됩니다.

### 4. 테스트

레포 → **Actions 탭 → lunch-menu-bot → Run workflow** (수동 실행)

Teams 채팅방에 오늘 메뉴가 오면 성공입니다.
Actions 로그에서 `파싱 결과: ... -> [...]` 줄로 스크래핑 결과를 확인할 수 있습니다.

---

## 발송 시간 변경

`.github/workflows/lunch-bot.yml` 의 cron 을 수정합니다. **cron 은 UTC 기준 (KST = UTC + 9시간)**.

| KST 시각 | cron (UTC) |
|---|---|
| **09:00 (현재 설정)** | `0 0 * * 1-5` |
| 10:00 | `0 1 * * 1-5` |
| 11:30 | `30 2 * * 1-5` |
| 08:00 | `0 23 * * 0-4` ← 전날 UTC 23시 주의 |

> GitHub Actions cron 은 트래픽에 따라 수 분 지연될 수 있습니다 (정시 보장 X).

---

## 로컬 테스트

```bash
pip install -r requirements.txt

export WEBHOOK_URL="https://your-powerautomate-url..."
export WEBHOOK_TYPE="teams"
# export OPENAI_API_KEY="sk-..."   # 선택

python scrape_menu.py
```

---

## 참고

- 학교 식단 페이지 HTML 구조가 바뀌면 파싱이 깨질 수 있습니다.
  파서는 클래스명 대신 "요일 행이 가장 많은 표"를 선택하고 헤더에서 '중식' 컬럼을 찾으므로
  웬만한 변경에는 버티지만, 가끔 직접 확인해 주세요.
- 휴무·공휴일로 메뉴가 비어 있으면 "등록된 중식 메뉴가 없어요" 메시지를 보냅니다.
- OpenAI 모델명(`gpt-4o-mini`)은 사용 가능한 모델로 자유롭게 변경할 수 있습니다.
