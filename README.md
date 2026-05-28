# Discord Song Bot

Claude LLM으로 자연어 명령을 처리하는 Discord 음악 봇.  
yt-dlp + FFmpeg으로 YouTube 오디오를 스트리밍합니다.

---

## 목차

1. [기능 목록](#기능-목록)
2. [아키텍처](#아키텍처)
3. [초기 설정](#초기-설정)
4. [배포 (AWS EC2)](#배포-aws-ec2)
5. [YouTube 쿠키 갱신](#youtube-쿠키-갱신) ← 주기적으로 필요
6. [환경 변수 레퍼런스](#환경-변수-레퍼런스)

---

## 기능 목록

| 카테고리 | 예시 명령어 |
|---|---|
| 재생 / 큐 | "다이너마이트 틀어줘", "BTS 틀어줘" |
| 여러 곡 한번에 | "카녜웨스트 5개 틀어줘", "신나는 노래 3곡 추천해줘" |
| 검색 후 선택 | "BTS 검색해줘" → 목록 → "1, 3번 추가해줘" |
| 일시정지 / 재개 | "멈춰", "다시 재생" |
| 스킵 / 정지 | "다음", "그만" |
| 볼륨 / 반복 | "볼륨 70", "한 곡 반복", "전체 반복" |
| 대기열 관리 | "큐 보여줘", "2, 4번 지워줘", "큐에서 Dynamite 빼줘" |
| 개인 플레이리스트 | "내 플리 보여줘", "지금 곡 내 플리에 추가해줘" |
| 재생 기록 | "기록 보여줘" → "2번 다시 틀어줘" |
| 음악 정보 | "지금 곡 정보 알려줘", "가수 정보 자세히" |
| 채널 제어 | "들어와", "나가" |

---

## 아키텍처

```
Discord 메시지
    │
    ▼
LLMListener (Claude Tool Use)
    │  자연어 → 툴 호출 결정
    ▼
Music Cog
    │  큐 관리, 재생 제어
    ▼
utils/youtube.py (yt-dlp)
    │  검색(flat) + 스트림 URL 추출(play time)
    ▼
FFmpeg → Discord 음성 채널
```

**두 단계 YouTube 추출 방식:**
- `search_youtube()` — 메타데이터만 빠르게 (스트림 URL 없음)
- `get_stream_url()` — 재생 직전에만 호출 (YouTube 스트림 URL은 ~6시간 만료)

**봇 감지 우회:**
- `bgutil-ytdlp-pot-provider` 사이드카 (포트 4416) — PO 토큰 자동 주입
- `yt-dlp-ejs` pip 패키지 — EJS 서명 해결 스크립트 번들
- `Deno` JS 런타임 — n-challenge 해결

---

## 초기 설정

### 1. `.env` 파일 생성

```env
DISCORD_TOKEN=your_discord_bot_token
ANTHROPIC_API_KEY=your_anthropic_api_key

# 봇이 항상 반응할 채널 ID (쉼표 구분, 멘션은 어디서나 동작)
# 쿠키 만료 알림도 이 채널들로 전송됩니다
MUSIC_CHANNEL_IDS=123456789,987654321

# 사용할 Claude 모델 (기본값: claude-sonnet-4-6)
CLAUDE_MODEL=claude-sonnet-4-6
```

### 2. 로컬 실행

```bash
pip install -r requirements.txt
python bot.py
```

### 3. Docker 실행

```bash
docker compose up -d
```

---

## 배포 (AWS EC2)

### 최초 배포

```bash
# EC2에서
git clone <repo> ~/discord-song-bot
cd ~/discord-song-bot
cp .env.example .env   # 편집 후 토큰 입력
touch cookies.txt      # 쿠키 파일 생성 (비어있어도 됨)
mkdir -p data          # 재생기록/플레이리스트 저장 디렉토리
docker compose up -d --build
```

### 업데이트 배포

```bash
cd ~/discord-song-bot
git pull origin main
docker compose up -d --build
```

---

## YouTube 쿠키 갱신

### 왜 필요한가?

YouTube 쿠키는 보통 **2~4주** 후 만료됩니다.  
만료되면 연령제한·지역제한 영상 재생이 불가능해집니다.

> **참고:** `bgutil` PO 토큰 덕분에 **일반 영상은 쿠키 없이도 재생 가능**합니다.  
> 쿠키는 주로 연령제한 영상을 위한 보조 수단입니다.

### 만료 알림

봇이 자동으로 `cookies.txt` 파일의 만료 타임스탬프를 파싱합니다:
- **시작 시** — 봇 로그인 직후
- **24시간마다** — 백그라운드 루프

만료됐거나 7일 미만이면 `MUSIC_CHANNEL_IDS`에 설정된 모든 채널로 알림이 전송됩니다.

알림 예시:
```
⚠️ YouTube 쿠키 만료 임박
쿠키가 5일 후 만료됩니다. 만료 전에 갱신해 주세요.
만료일: 2026-06-02 09:00 UTC
```

### 갱신 방법

#### Step 1 — 브라우저 확장 설치

Chrome / Edge 에서 아래 중 하나 설치:

- **[Get cookies.txt LOCALLY](https://chrome.google.com/webstore/detail/get-cookiestxt-locally/cclelndahbckbenkjhflpdbgdldlbecc)**  
  *(가장 간단, 추천)*
- **EditThisCookie**

#### Step 2 — YouTube 로그인

1. 브라우저에서 [youtube.com](https://youtube.com) 접속
2. Google 계정으로 로그인
3. 일반 영상 하나 재생해서 세션 쿠키가 생성됐는지 확인

#### Step 3 — 쿠키 내보내기

1. YouTube 탭에서 확장 아이콘 클릭
2. **"Export"** 또는 **"현재 탭의 쿠키 내보내기"** 클릭
3. 파일 형식: **Netscape** (`.txt`)
4. `cookies.txt` 로 저장

#### Step 4 — 서버에 업로드

```bash
# 로컬에서 실행
scp cookies.txt ubuntu@<EC2_IP>:~/discord-song-bot/cookies.txt
```

> SSH 키 인증 사용 시:
> ```bash
> scp -i ~/.ssh/your-key.pem cookies.txt ubuntu@<EC2_IP>:~/discord-song-bot/cookies.txt
> ```

#### Step 5 — 봇 재시작 없이 즉시 적용

yt-dlp는 매 재생 시마다 쿠키 파일을 읽으므로 **재시작 불필요**합니다.  
파일만 교체하면 다음 재생부터 바로 적용됩니다.

#### Step 6 — 쿠키 정상 동작 확인 (선택)

업로드 후 쿠키가 제대로 인식되는지 직접 확인하려면:

```bash
# EC2 서버에서 실행 (Docker 컨테이너 내부)
docker exec -it discord-song-bot \
    yt-dlp -vU --cookies /app/cookies.txt \
    --list-formats "https://www.youtube.com/watch?v=gdZLi9oWNZg"
```

출력에 포맷 목록이 나오면 쿠키가 정상적으로 작동하는 것입니다.  
`Sign in to confirm` 또는 `cookies are required` 같은 메시지가 나오면 쿠키를 다시 내보내 주세요.

### 주의사항

- 쿠키 파일에는 Google 계정 세션 정보가 담겨 있습니다 — **절대 공개 저장소에 커밋하지 마세요**
- `.gitignore` 에 `cookies.txt` 가 포함되어 있는지 확인하세요
- 봇 전용 Google 계정을 별도로 만들어 사용하는 것을 권장합니다

### 쿠키 없이 운영하는 경우

`cookies.txt` 파일이 비어있거나 없어도 봇은 정상 동작합니다.  
단, 아래 콘텐츠는 재생이 안 될 수 있습니다:
- 연령제한(19금) 영상
- 일부 지역 제한 영상

---

## 환경 변수 레퍼런스

| 변수 | 필수 | 기본값 | 설명 |
|---|---|---|---|
| `DISCORD_TOKEN` | ✅ | — | Discord 봇 토큰 |
| `ANTHROPIC_API_KEY` | ✅ | — | Anthropic API 키 |
| `MUSIC_CHANNEL_IDS` | — | `""` | 봇이 반응할 채널 ID (쉼표 구분) — 쿠키 알림도 이 채널로 전송 |
| `COOKIE_WARN_DAYS` | — | `7` | 만료 N일 전부터 경고 |
| `COOKIE_PATH` | — | `/app/cookies.txt` | 쿠키 파일 경로 |
| `CLAUDE_MODEL` | — | `claude-opus-4-7` | 사용할 Claude 모델 ID |
| `HISTORY_PATH` | — | `/app/data/history.json` | 재생 기록 파일 경로 |
| `PLAYLIST_PATH` | — | `/app/data/playlists.json` | 플레이리스트 파일 경로 |
| `LOG_YTDLP_VERBOSE` | — | `0` | `1`로 설정 시 yt-dlp 상세 로그 출력 |
