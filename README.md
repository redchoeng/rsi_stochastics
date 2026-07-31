# stock_indicator_bot

미국 주식(주간 거래대금 top100)의 스토캐스틱(14,3,3)/RSI(14) 보조지표 신호를 감지해 텔레그램으로 알려주는 봇.

## 동작 방식

1. **유니버스** — S&P500 + Nasdaq100 후보군(~550~600개)에서 최근 5거래일 거래대금(Close×Volume) 상위 100개를 매주 월요일에 산출 (`config/universe.json`). 전체 미국 시장을 무료 인프라로 실시간 스크리닝하는 건 불가능해서 나온 근사치이며, 이 두 지수 밖의 급등 소형주는 잡히지 않는다.
2. **일봉 필터** — 매일 미장 마감 직후 top100 종목의 일봉 스토캐스틱/RSI 크로스를 계산해 방향(bullish/bearish)을 확정 (`config/daily_signals.json`).
3. **15분봉 트리거** — 장중 수분 간격으로 daily_signals에 방향이 있는 종목만 15분봉으로 재검사, 아래 조건 중 하나라도 새로 충족되면 텔레그램 알림:
   - 스토캐스틱 골든/데드크로스
   - 스토캐스틱 Bullish/Bearish Failure Swing
   - RSI(14) 골든/데드크로스 (RSI 자체의 14일 이동평균 대비)

## 로컬 설정

```bash
pip install -r requirements.txt
cp .env.example .env   # TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID 입력
```

### 텔레그램 봇 만들기
1. 텔레그램에서 `@BotFather`에게 `/newbot` 전송 → 토큰 발급받아 `TELEGRAM_BOT_TOKEN`에 입력
2. 알림 받을 채팅(개인/그룹)에서 봇에게 아무 메시지나 전송
3. `https://api.telegram.org/bot<TOKEN>/getUpdates`를 브라우저로 열어 `chat.id` 값을 `TELEGRAM_CHAT_ID`에 입력

### 로컬 실행/검증
```bash
python run_daily_scan.py --limit 20            # 유니버스 상위 20개만 빠르게 테스트
python run_intraday_check.py --ignore-market-hours --dry-run   # 장 시간 무시 + 실제 전송 없이 확인
```

## GitHub Actions 배포

1. 이 디렉토리로 새 GitHub 저장소를 만들고 push
2. 저장소 Settings → Secrets and variables → Actions에 등록:
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
3. 두 워크플로우:
   - `.github/workflows/daily-scan.yml` — 매일 자동 실행(cron) + 수동 실행 가능
   - `.github/workflows/intraday-check.yml` — `workflow_dispatch` 전용, cron-job.org가 장중 수분 간격으로 호출해야 실제로 동작함
4. cron-job.org에서 새 크론잡 생성 (etf_guide와 동일한 방식):
   - 이 저장소 한정 fine-grained PAT 발급 (Actions: Read & write, Contents: Read & write)
   - 요청: `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/intraday-check.yml/dispatches`
   - 헤더: `Authorization: token <PAT>`, `Accept: application/vnd.github.v3+json`
   - 바디: `{"ref":"main"}`
   - 실행 주기: 장중 수 분 간격 (스크립트가 `America/New_York` 기준 정규장 시간이 아니면 자동으로 즉시 종료하므로, 넉넉하게 KST 21:00~06:00 매일로 걸어둬도 무방)
5. 초기 배포 직후에는 `daily-scan.yml`을 한 번 수동 실행(`workflow_dispatch`)해 `daily_signals.json`을 채워야 `intraday-check.yml`이 감지할 종목이 생긴다.

## 한계
- 유니버스가 S&P500/Nasdaq100 기반 근사치 (전체 시장 아님)
- yfinance 15분봉은 최근 60일까지만 제공
- Failure Swing 감지는 로컬 극값(local peak/trough) 기반 휴리스틱이며 교과서적 정의를 단순화한 것
