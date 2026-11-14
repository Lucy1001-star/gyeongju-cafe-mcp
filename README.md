# 경주 카페 나들이 MCP

경주 날씨를 확인하고, 그 날씨에 어울리는 경주 카페 3곳을 위치·영업시간과 함께 추천하는 MCP 서버입니다.
Claude 같은 MCP 클라이언트에 연결하면 "이번 주말 경주 카페 추천해줘" 같은 요청에 이 서버의 도구가 쓰입니다.

- 실행 방식: stdio (로컬에서 `python server.py`로 실행)
- 구현: Python + [FastMCP](https://gofastmcp.com)

## 폴더 구조

```
new-mcp/
├─ .mcp.json.example       # Claude Code용 MCP 서버 등록 양식 (경로는 예시)
├─ .mcp.json               # 실제 등록 파일 (직접 만들며, 깃허브에 올라가지 않음)
├─ .gitignore
└─ gyeongju-cafe-mcp/
   ├─ server.py            # MCP 서버 본체
   ├─ requirements.txt     # 필요한 패키지
   ├─ .env.example         # API 키 양식 (값은 비어 있음)
   └─ .env                 # 실제 API 키 (직접 만들며, 깃허브에 올라가지 않음)
```

## 제공 도구

| 도구 | 하는 일 |
|---|---|
| `recommend_cafes` | 경주 날씨를 확인하고 그 날씨에 맞는 카페 3곳을 추천합니다. |
| `get_gyeongju_weather` | 경주 날씨(요약, 최고·최저 기온, 강수량)만 조회합니다. |

두 도구 모두 입력값은 `day_offset` 하나입니다.

| 입력값 | 형식 | 기본값 | 설명 |
|---|---|---|---|
| `day_offset` | 정수 0~7 | 0 | 며칠 뒤 날씨를 기준으로 할지. 0=오늘, 1=내일, 7=일주일 뒤 |

### `recommend_cafes` 응답

| 항목 | 내용 |
|---|---|
| `weather` | 날짜, 날씨 요약, 최고·최저 기온, 강수량, 날씨 유형. 오늘(`day_offset` 0)이면 현재 기온도 포함 |
| `reason` | 이 날씨에 왜 이런 카페를 골랐는지 한 문장 |
| `cafes` | 카페 3곳. 아래 표 참고 |
| `notes` | 일부 데이터를 가져오지 못했을 때의 안내 문구 (문제가 없으면 빈 목록) |

카페 한 곳에 들어 있는 정보:

| 항목 | 내용 |
|---|---|
| `name`, `address`, `phone` | 이름, 주소, 전화번호 |
| `location` | 좌표 (`lat`, `lng`) |
| `map_url` | 카카오맵 상세 페이지 링크 |
| `directions_url` | 카카오맵 길찾기 링크 |
| `business_days` | 영업시간. 찾지 못하면 "정보 없음" 안내 문구 |
| `business_days_source` | 영업시간을 Google Places에서 가져온 경우에만 표시 |
| `homepage`, `view` | 전망 좋은 카페 공공데이터에 값이 있을 때만 표시 |
| `source` | 카페 정보 출처 (카카오 로컬 API 또는 경주시 공공데이터) |

## 추천 방식

1. 경주의 해당 날짜 예보를 요약합니다. 하늘 상태와 강수량은 나들이 시간대(08~20시), 최고·최저 기온은 하루 전체 기준입니다.
2. 하늘 상태와 최고 기온으로 날씨 유형을 정합니다.
3. 유형에 맞는 카페를 고릅니다.

| 날씨 유형 | 조건 | 고르는 카페 |
|---|---|---|
| 눈 | 눈·진눈깨비 예보가 있음 | 한옥카페, 베이커리카페 |
| 비 | 강수량 1mm 이상이거나 비 예보가 3분의 1 이상 | 한옥카페, 북카페, 디저트카페 |
| 더움 | 최고 기온 28°C 이상 | 빙수, 대형카페 |
| 추움 | 최고 기온 7°C 이하 | 베이커리카페, 한옥카페 |
| 맑음 | 맑은 시간대가 절반 이상 | 경주시 선정 '전망 좋은 카페' |
| 흐림 | 그 외 | 디저트카페, 베이커리카페 |

눈·비가 기온보다 우선합니다. 맑은 날은 공공데이터 목록에서, 나머지는 카카오 검색 상위 10곳에서 무작위로 고르기 때문에 같은 날씨라도 호출할 때마다 추천이 조금씩 달라집니다.

## 데이터 출처와 API 키

| 용도 | 출처 | 환경변수 | 필수 여부 |
|---|---|---|---|
| 날씨 | MET Norway Locationforecast | 없음 | 키 불필요 |
| 카페 검색·위치 | 카카오 로컬 API | `KAKAO_REST_API_KEY` | 필수 |
| 전망 좋은 카페 목록 | 공공데이터포털 (경주시) | `DATA_GO_KR_SERVICE_KEY` | 맑은 날 추천에 필요 |
| 요일별 영업시간 | Google Places API (New) | `GOOGLE_PLACES_API_KEY` | 선택 |

키 발급 위치:

- **카카오**: 카카오 개발자 사이트 > 내 애플리케이션 > 앱 키 > REST API 키
- **공공데이터포털**: 마이페이지 > '경주시 전망 좋은 카페 현황' 활용신청 > 일반 인증키 (Encoding·Decoding 아무거나)
- **Google**: Google Cloud에서 Places API (New)를 켜고 API 키 발급. 호출량에 따라 요금이 나올 수 있습니다.

키가 일부 없을 때의 동작:

- 공공데이터 키가 없으면 맑은 날에도 카카오 검색(뷰카페·루프탑카페·정원카페)으로 대신 추천하고, `notes`에 사유를 남깁니다.
- 카카오 키가 없으면 전망 좋은 카페 목록에서 대신 고르며, 좌표와 지도 링크가 빠질 수 있습니다.
- Google 키가 없으면 영업시간이 대부분 "정보 없음"으로 나옵니다.

## 설치

Python 3.14, fastmcp 4.2.0에서 동작을 확인했습니다. 아래 명령은 Windows PowerShell 기준입니다.

```powershell
cd gyeongju-cafe-mcp
python -m venv .venv
.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

그다음 `.env`를 열어 발급받은 키를 넣습니다.

```
KAKAO_REST_API_KEY=발급받은_키
DATA_GO_KR_SERVICE_KEY=발급받은_키
GOOGLE_PLACES_API_KEY=발급받은_키
```

## Claude에 연결하기

### Claude Code

저장소 최상위의 `.mcp.json`이 서버를 등록합니다. 이 파일에는 내 PC의 절대 경로가 들어가기 때문에 저장소에는 양식인 `.mcp.json.example`만 들어 있습니다. 양식을 복사한 뒤 두 경로를 자기 폴더 위치에 맞게 고칩니다.

```powershell
Copy-Item .mcp.json.example .mcp.json
```

```json
{
  "mcpServers": {
    "gyeongju-cafe": {
      "command": "C:\\path\\to\\new-mcp\\gyeongju-cafe-mcp\\.venv\\Scripts\\python.exe",
      "args": ["C:\\path\\to\\new-mcp\\gyeongju-cafe-mcp\\server.py"],
      "env": { "PYTHONUTF8": "1" }
    }
  }
}
```

이 폴더에서 Claude Code를 열면 `gyeongju-cafe` 서버를 사용할지 묻고, 승인하면 연결됩니다.

### Claude Desktop

`claude_desktop_config.json`의 `mcpServers`에 위와 같은 `gyeongju-cafe` 항목을 추가하고 앱을 다시 시작합니다.

## 사용 예시

연결한 뒤 평소처럼 말하면 됩니다.

- "오늘 경주 카페 추천해줘"
- "이번 주 토요일 경주 날씨 어때?"
- "내일 경주 갈 건데 카페 3곳이랑 동선 짜줘"

## 문제 해결

| 증상 | 확인할 것 |
|---|---|
| `KAKAO_REST_API_KEY 가 없습니다` | `.env`가 `server.py`와 같은 폴더에 있는지, 키 이름이 정확한지 |
| `공공데이터 API 오류` | 활용신청이 승인됐는지. 발급 직후에는 반영까지 시간이 걸릴 수 있음 |
| `Google Places API 오류` | Places API (New)가 켜져 있는지, 결제 계정이 연결돼 있는지 |
| `날씨 예보가 아직 없습니다` | `day_offset`을 더 가까운 날짜로 |
| 영업시간이 "정보 없음" | `map_url`의 카카오맵 상세 페이지에서 확인 |
| 회사망에서 인증서(SSL) 오류 | `truststore` 패키지가 설치돼 있는지 (`requirements.txt`에 포함) |
| 한글이 깨짐 | 서버 등록 설정의 `env`에 `PYTHONUTF8=1`이 있는지 |

## 참고

- 영업시간은 Google Places 기준이며 실제와 다를 수 있으니, 방문 전에 확인하는 것을 권합니다.
- 전망 좋은 카페 목록과 영업시간 조회 결과는 서버가 켜져 있는 동안 메모리에 보관해 재사용합니다. 최신 값으로 바꾸려면 서버를 다시 시작합니다.
- `.env`에는 실제 API 키가 들어 있으므로 공유하거나 깃허브에 올리지 않습니다. (`.gitignore`로 제외돼 있습니다.)
