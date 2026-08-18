# stock_indicator_bot

미국 주식(유동성 상위 후보군 중 변동성 top100)의 스토캐스틱(14,3,3)/RSI(14) 보조지표 신호를 감지해 텔레그램으로 알려주는 봇. `run_scan.py` 하나가 04:00~20:00 ET 내내 수 분 간격으로 실행되며, 모든 판단이 **당일 한정**이다 (전날 신호가 오늘로 안 넘어옴).

## 동작 방식

매 실행마다 유니버스(top100) 각 종목에 대해:

1. **오늘 일봉이 조건을 만족하는지 확인** — 스토캐스틱 골든/데드크로스, 스토캐스틱 Failure Swing, RSI(14) 골든/데드크로스(RSI 자체의 14일 이동평균 대비) 3가지 중 **오늘 날짜의 일봉 그 자체**에서 2개 이상 동시 충족돼야 함 (`config/settings.yaml`의 `daily_scan.min_conditions`). 어제 이전에 난 크로스는 보지 않는다 — 오늘 조건이 안 맞으면 그 종목은 이번 실행에서 그냥 건너뛴다.
2. **매수(bullish)** — 오늘 일봉이 매수 조건을 만족하면 먼저 "오늘의 매수 후보" 1차 알림을 보내고(당일 1회, 조건이 여러 개 겹쳐도 메시지 하나), 이어서 같은 실행 안에서 오늘 15분봉(프리~애프터마켓 포함)에 같은 3가지 조건 중 하나라도 새로 나오면 "오늘의 매수 타이밍"으로 딱 한 번만 진입 알림을 보낸다. 그 이후 같은 날 다른 조건이 또 나와도 재알림 없음 — 하루 최대 2통(후보 1 + 진입 1)으로 제한된다.
3. **거래량 필터** — 15분봉 진입 신호가 나와도, 그 봉의 거래량이 해당 종목 정규장 15분봉 거래량 중앙값의 `intraday_check.min_volume_ratio`(기본 10%) 미만이면 무시한다. 프리/애프터마켓의 거래량이 거의 없는 봉에서 나오는 크로스/RSI 노이즈를 걸러내기 위함.
4. **매도(bearish)** — 오늘 일봉이 매도 조건을 만족하면 15분봉 확인 없이 바로 텔레그램 알림(당일 1회, 조건 여러 개면 메시지 하나). 진입/청산 타이밍은 사용자가 직접 판단.
5. 유니버스는 매주 월요일에만 재계산 (`config/universe.json`), 2단계로 산출: S&P500+Nasdaq100 후보군에서 ① 최근 5거래일 거래대금 상위 `liquidity_floor_n`(기본 300)개로 유동성 최소 기준만 거르고, ② 그 안에서 ATR%(`volatility_period`일, 기본 14) 상위 `top_n`(기본 100)개로 재랭킹. 거래대금은 크지만 하루 변동폭이 작은 대형 필수소비재주(PG, KO 등)가 걸러지고, 실제로 움직이는 종목 위주로 남는다.
6. 프리마켓/애프터마켓 봉은 거래량이 얕아 신호가 노이즈성일 수 있어 매수 진입 알림에 세션(프리마켓/정규장/애프터마켓)을 표시함.

## 포지션 추적 & 청산 (텔레그램 명령)

매수 알림이 떠도 실제로 샀는지는 봇이 알 수 없어서, 알림만으로 자동으로 포지션을 잡지 않는다. 텔레그램 채팅에서 직접 명령을 보내야 추적이 시작된다:

- `/buy TICKER [가격]` — 포지션 등록. 가격 생략 시 현재가로 등록. 이미 등록된 티커면 덮어씀.
- `/sell TICKER [가격]` — 수동 청산 (봇이 스톱을 못 잡았거나 직접 판단해서 팔았을 때). 가격 생략 시 현재가로 손익 계산.
- `/positions` — 현재 추적 중인 포지션과 진입가/최고가 목록.

`/buy`로 등록되면 매 실행마다 **ATR 트레일링 스톱(Chandelier Exit)**으로 청산 여부를 감시한다: 진입 이후 최고가를 계속 갱신하고, `손절선 = 최고가 - (일봉 ATR14 × exit_strategy.atr_multiplier)`(기본 3배)를 종가가 밑돌면 자동으로 청산 알림을 보내고 추적을 종료한다. 고정 %가 아니라 종목별 변동성(ATR)에 연동되는 손절선이라, 지금처럼 변동성 상위로 재랭킹된 유니버스(종목마다 ATR%가 5~13%대로 제각각)에 더 적합하다. 이 종목이 이번 주 유니버스 top100에서 빠져도 포지션이 열려 있는 한 계속 추적한다.

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
- 텔레그램 명령(/buy 등)은 스캔 주기(수 분 간격)에 맞춰 폴링하기 때문에, 명령을 보낸 뒤 다음 실행 전까지는 반영이 안 됨. 장 시간이 아니면 스캔 자체가 안 도니 이 시간대에 보낸 명령은 다음 장 시간까지 대기
- 유니버스가 S&P500/Nasdaq100 기반 근사치 (전체 시장 아님)
- 변동성(ATR%) 상위로 재랭킹하는 만큼 신호도 더 노이즈성일 수 있음 — 거래량 필터/2-of-3 조건으로 어느 정도 상쇄
- yfinance 15분봉은 최근 60일까지만 제공
- Failure Swing 감지는 로컬 극값(local peak/trough) 기반 휴리스틱이며 교과서적 정의를 단순화한 것
- 오늘자 일봉은 정규장이 실제로 열려야 형성되기 시작함 — 프리마켓 시간대엔 전날 일봉이 마지막 봉으로 남아있어 당일 신호가 아직 안 잡힐 수 있음
