# stock_indicator_bot

미국 주식(주간 거래대금 top100)의 스토캐스틱(14,3,3)/RSI(14) 보조지표 신호를 감지해 텔레그램으로 알려주는 봇. `run_scan.py` 하나가 04:00~20:00 ET 내내 수 분 간격으로 실행되며, 모든 판단이 **당일 한정**이다 (전날 신호가 오늘로 안 넘어옴).

## 동작 방식

매 실행마다 유니버스(top100) 각 종목에 대해:

1. **오늘 일봉이 조건을 만족하는지 확인** — 스토캐스틱 골든/데드크로스, 스토캐스틱 Failure Swing, RSI(14) 골든/데드크로스(RSI 자체의 14일 이동평균 대비) 3가지 중 **오늘 날짜의 일봉 그 자체**에서 2개 이상 동시 충족돼야 함 (`config/settings.yaml`의 `daily_scan.min_conditions`). 어제 이전에 난 크로스는 보지 않는다 — 오늘 조건이 안 맞으면 그 종목은 이번 실행에서 그냥 건너뛴다.
2. **매수(bullish)** — 오늘 일봉이 매수 조건을 만족했으면, 오늘 15분봉(프리~애프터마켓 포함)에서 같은 3가지 조건 중 하나라도 새로 나오면 즉시 텔레그램 알림. 15분봉이 아직 어제 걸로 남아있으면(예: 프리마켓 시작 전) 대기.
3. **매도(bearish)** — 오늘 일봉이 매도 조건을 만족하면 15분봉 확인 없이 바로 텔레그램 알림. 진입/청산 타이밍은 사용자가 직접 판단.
4. 유니버스(S&P500+Nasdaq100 후보군에서 최근 5거래일 거래대금 상위 100개)는 매주 월요일에만 재계산 (`config/universe.json`).
5. 프리마켓/애프터마켓 봉은 거래량이 얕아 신호가 노이즈성일 수 있어 매수 알림에 세션(프리마켓/정규장/애프터마켓)을 표시함.

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
python run_scan.py --limit 20 --ignore-market-hours --dry-run   # 상위 20개만, 장 시간/전송 무시
python run_scan.py --force-universe-refresh                     # 유니버스 강제 재계산
```

## GitHub Actions 배포

1. 이 디렉토리로 새 GitHub 저장소를 만들고 push
2. 저장소 Settings → Secrets and variables → Actions에 등록:
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
3. `.github/workflows/scan.yml` — `workflow_dispatch` 전용, cron-job.org가 호출해야 실제로 동작함
4. cron-job.org에서 새 크론잡 생성 (etf_guide와 동일한 방식):
   - 이 저장소 한정 fine-grained PAT 발급 (Actions: Read & write, Contents: Read & write)
   - 요청: `POST https://api.github.com/repos/<owner>/<repo>/actions/workflows/scan.yml/dispatches`
   - 헤더: `Authorization: token <PAT>`, `Accept: application/vnd.github.v3+json`
   - 바디: `{"ref":"main"}`
   - 실행 주기: 수 분 간격 (스크립트가 `America/New_York` 기준 04:00~20:00 ET 세션이 아니면 자동으로 즉시 종료하므로, DST 상관없이 넉넉하게 KST 17:00~10:00(다음날)로 걸어둬도 무방 — 04:00 ET는 서머타임이면 KST 17:00, 아니면 18:00; 20:00 ET는 서머타임이면 다음날 KST 09:00, 아니면 10:00)
5. 매 실행마다 top100 전종목의 일봉을 다시 받기 때문에(당일 조건을 매번 새로 확인해야 해서), 폴링 주기를 너무 짧게 잡으면 yfinance 호출량이 늘어난다 — 몇 분 간격 정도면 무리 없지만 그보다 훨씬 짧게 갈 거면 rate limit을 유의할 것.

## 한계
- 유니버스가 S&P500/Nasdaq100 기반 근사치 (전체 시장 아님)
- yfinance 15분봉은 최근 60일까지만 제공
- Failure Swing 감지는 로컬 극값(local peak/trough) 기반 휴리스틱이며 교과서적 정의를 단순화한 것
- 오늘자 일봉은 정규장이 실제로 열려야 형성되기 시작함 — 프리마켓 시간대엔 전날 일봉이 마지막 봉으로 남아있어 당일 신호가 아직 안 잡힐 수 있음
