"""경주 카페 나들이 MCP 서버.

날씨에 맞는 경주 카페 3곳을 위치·영업일 정보와 함께 추천한다.

데이터 출처
- 날씨: MET Norway Locationforecast (키 불필요)
- 카페 검색·위치: 카카오 로컬 API            -> KAKAO_REST_API_KEY
- 전망 좋은 카페: 경주시 공공데이터 API       -> DATA_GO_KR_SERVICE_KEY
- 영업일(선택): Google Places API (New)     -> GOOGLE_PLACES_API_KEY

실행: python server.py  (stdio 방식 MCP 서버)
"""

from __future__ import annotations

import asyncio
import math
import os
import random
import re
import ssl
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Annotated, Any
from urllib.parse import quote

import httpx
from fastmcp import FastMCP
from fastmcp.exceptions import ToolError
from pydantic import Field

try:  # 회사망처럼 자체 인증서를 쓰는 환경에서도 되도록 운영체제의 인증서 저장소를 쓴다.
    import truststore

    SSL_CONTEXT: ssl.SSLContext | bool = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
except ImportError:
    SSL_CONTEXT = True

# ---------------------------------------------------------------------------
# 설정
# ---------------------------------------------------------------------------

GYEONGJU = {"lat": 35.8562, "lng": 129.2247}
WEATHER_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
KST = timezone(timedelta(hours=9))
KAKAO_URL = "https://dapi.kakao.com/v2/local/search/keyword.json"
KAKAO_ADDRESS_URL = "https://dapi.kakao.com/v2/local/search/address.json"
GOOGLE_PLACES_URL = "https://places.googleapis.com/v1/places:searchText"
SCENIC_URL = "https://apis.data.go.kr/5050000/niceViewCafeService/getNiceViewCafe"
NO_HOURS = "정보 없음 (카카오맵 링크의 상세 페이지에서 확인하세요)"


def load_env() -> None:
    """server.py 옆의 .env 파일을 읽어 환경변수로 등록한다. (이미 설정된 값은 유지)"""
    env_file = Path(__file__).with_name(".env")
    if not env_file.exists():
        return
    for line in env_file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


load_env()

mcp = FastMCP(
    "경주 카페 나들이",
    instructions=(
        "경주 카페 나들이를 돕는 서버입니다. 사용자가 경주 카페 추천을 원하면 "
        "recommend_cafes 를 호출해 날씨에 맞는 카페 3곳을 위치·영업일과 함께 안내하세요."
    ),
)

# ---------------------------------------------------------------------------
# 날씨
# ---------------------------------------------------------------------------

# 날씨 유형별 추천 방향. queries 는 앞에서부터 시도하고 결과가 모자라면 다음 것으로 넘어간다.
MOODS: dict[str, dict[str, Any]] = {
    "rain": {
        "label": "비",
        "reason": "비가 오는 날이라 빗소리를 들으며 머물기 좋은 아늑한 실내 카페를 골랐어요.",
        "queries": ["경주 한옥카페", "경주 북카페", "경주 디저트카페"],
    },
    "snow": {
        "label": "눈",
        "reason": "눈이 오는 날이라 설경이 어울리는 한옥 카페와 따뜻한 실내 카페를 골랐어요.",
        "queries": ["경주 한옥카페", "경주 베이커리카페"],
    },
    "hot": {
        "label": "더움",
        "reason": "더운 날이라 시원하게 쉬어 갈 수 있는 빙수·대형 카페를 골랐어요.",
        "queries": ["경주 빙수", "경주 대형카페"],
    },
    "cold": {
        "label": "추움",
        "reason": "쌀쌀한 날이라 따뜻한 빵과 음료가 있는 실내 카페를 골랐어요.",
        "queries": ["경주 베이커리카페", "경주 한옥카페"],
    },
    "clear": {
        "label": "맑음",
        "reason": "맑은 날이라 경주시가 선정한 '전망 좋은 카페' 중에서 골랐어요.",
        "queries": ["경주 뷰카페", "경주 루프탑카페", "경주 정원카페"],
    },
    "cloudy": {
        "label": "흐림",
        "reason": "흐린 날이라 풍경보다 디저트와 분위기가 좋은 카페를 골랐어요.",
        "queries": ["경주 디저트카페", "경주 베이커리카페"],
    },
}


SKY_LABELS = {"snow": "눈", "rain": "비", "clear": "맑음", "cloudy": "흐림"}


def sky_of(symbol: str) -> str:
    """MET Norway 날씨 기호(symbol_code)를 눈·비·맑음·흐림으로 줄인다."""
    if "snow" in symbol or "sleet" in symbol:
        return "snow"
    if "rain" in symbol or "thunder" in symbol:
        return "rain"
    if symbol.startswith(("clearsky", "fair", "partlycloudy")):
        return "clear"
    return "cloudy"


def classify(sky: str, temp_max: float) -> str:
    """하늘 상태와 최고 기온을 추천 유형(MOODS 의 키)으로 분류한다."""
    if sky in ("snow", "rain"):
        return sky
    if temp_max >= 28:
        return "hot"
    if temp_max <= 7:
        return "cold"
    return sky


async def fetch_weather(client: httpx.AsyncClient, day_offset: int) -> dict[str, Any]:
    """경주의 해당 날짜 예보를 하루 단위로 요약한다. (나들이 시간대인 08~20시 기준)"""
    res = await client.get(
        WEATHER_URL,
        params={"lat": GYEONGJU["lat"], "lon": GYEONGJU["lng"]},
        headers={"User-Agent": "gyeongju-cafe-mcp/0.1"},
    )
    if res.status_code != 200:
        raise ToolError(f"날씨 정보를 가져오지 못했습니다 (HTTP {res.status_code}).")
    series = res.json()["properties"]["timeseries"]

    def kst(entry: dict[str, Any]) -> datetime:
        return datetime.fromisoformat(entry["time"].replace("Z", "+00:00")).astimezone(KST)

    target = (datetime.now(KST) + timedelta(days=day_offset)).date()
    day = [e for e in series if kst(e).date() == target]
    if not day:
        raise ToolError(f"{target} 날씨 예보가 아직 없습니다.")
    # 오늘 저녁처럼 낮 시간대가 이미 지났으면 남은 시간 전체를 본다.
    outing = [e for e in day if 8 <= kst(e).hour <= 20] or day

    skies: list[str] = []
    rain_mm = 0.0
    for entry in outing:
        period = entry["data"].get("next_1_hours") or entry["data"].get("next_6_hours")
        if not period:
            continue
        skies.append(sky_of(period["summary"]["symbol_code"]))
        rain_mm += float(period.get("details", {}).get("precipitation_amount") or 0)

    if "snow" in skies:
        sky = "snow"
    elif rain_mm >= 1.0 or skies.count("rain") * 3 >= max(len(skies), 1):
        sky = "rain"
    elif skies.count("clear") * 2 >= max(len(skies), 1):
        sky = "clear"
    else:
        sky = "cloudy"

    temps = [float(e["data"]["instant"]["details"]["air_temperature"]) for e in day]
    mood = classify(sky, max(temps))
    weather: dict[str, Any] = {
        "date": target.isoformat(),
        "summary": SKY_LABELS[sky],
        "temp_max_c": max(temps),
        "temp_min_c": min(temps),
        "precipitation_mm": round(rain_mm, 1),
        "type": MOODS[mood]["label"],
        "_mood": mood,
    }
    if day_offset == 0:
        weather["current_temp_c"] = float(series[0]["data"]["instant"]["details"]["air_temperature"])
    return weather


# ---------------------------------------------------------------------------
# 카카오 로컬 API
# ---------------------------------------------------------------------------


async def kakao_get(client: httpx.AsyncClient, url: str, params: dict[str, Any]) -> list[dict[str, Any]]:
    key = os.environ.get("KAKAO_REST_API_KEY", "").strip()
    if not key:
        raise ToolError("KAKAO_REST_API_KEY 가 없습니다. server.py 옆 .env 파일에 카카오 REST API 키를 넣어주세요.")
    res = await client.get(url, params=params, headers={"Authorization": f"KakaoAK {key}"})
    if res.status_code != 200:
        raise ToolError(f"카카오 로컬 API 오류 (HTTP {res.status_code}): {res.text[:200]}")
    return res.json()["documents"]


async def kakao_search(
    client: httpx.AsyncClient, query: str, size: int = 15, cafes_only: bool = True
) -> list[dict[str, Any]]:
    """경주 안의 장소를 키워드로 검색한다. cafes_only 면 카페 업종만 돌려준다."""
    params: dict[str, Any] = {"query": query, "size": size}
    if cafes_only:
        params["category_group_code"] = "CE7"
    docs = await kakao_get(client, KAKAO_URL, params)
    return [d for d in docs if "경주" in d.get("address_name", "")]


async def kakao_geocode(client: httpx.AsyncClient, address: str) -> dict[str, float] | None:
    """주소를 좌표로 바꾼다. 괄호 안 동 이름은 검색을 방해해 떼어낸다."""
    docs = await kakao_get(client, KAKAO_ADDRESS_URL, {"query": re.sub(r"\s*\(.*?\)", "", address), "size": 1})
    return {"lat": float(docs[0]["y"]), "lng": float(docs[0]["x"])} if docs else None


def directions_url(name: str, lat: float, lng: float) -> str:
    return f"https://map.kakao.com/link/to/{quote(name)},{lat},{lng}"


def from_kakao(doc: dict[str, Any]) -> dict[str, Any]:
    lat, lng = float(doc["y"]), float(doc["x"])
    return {
        "name": doc["place_name"],
        "address": doc.get("road_address_name") or doc.get("address_name"),
        "phone": doc.get("phone") or "정보 없음",
        "location": {"lat": lat, "lng": lng},
        "map_url": doc.get("place_url"),
        "directions_url": directions_url(doc["place_name"], lat, lng),
        "business_days": NO_HOURS,
        "source": "카카오 로컬 API",
    }


# ---------------------------------------------------------------------------
# 경주시 전망 좋은 카페 (공공데이터)
# ---------------------------------------------------------------------------

_scenic_cache: list[dict[str, Any]] | None = None


def pick(item: dict[str, Any], *patterns: str) -> str | None:
    """공공데이터 필드명이 문서에 공개돼 있지 않아, 필드명 패턴으로 값을 찾는다."""
    for pattern in patterns:
        for key, value in item.items():
            if re.search(pattern, key, re.IGNORECASE) and value not in (None, ""):
                return str(value).strip()
    return None


def business_days_of(item: dict[str, Any]) -> str | None:
    """영업일·영업시간·휴무일처럼 보이는 필드를 모두 모아 한 줄로 만든다."""
    pattern = r"영업|휴무|운영|OPEN|CLOSE|HOLIDAY|RSTDE|OPER|BSN|BIZ|SALE|WEEK|TIME|HR"
    parts = [
        f"{key}: {str(value).strip()}"
        for key, value in item.items()
        if re.search(pattern, key, re.IGNORECASE) and value not in (None, "")
    ]
    return " / ".join(parts) or None


async def fetch_scenic(client: httpx.AsyncClient) -> list[dict[str, Any]]:
    """경주시 전망 좋은 카페 전체 목록(원본 필드)을 가져온다. 한 번 가져오면 재사용한다."""
    global _scenic_cache
    if _scenic_cache is not None:
        return _scenic_cache
    key = os.environ.get("DATA_GO_KR_SERVICE_KEY", "").strip()
    if not key:
        raise ToolError("DATA_GO_KR_SERVICE_KEY 가 없습니다. server.py 옆 .env 파일에 공공데이터포털 인증키를 넣어주세요.")
    # Encoding 키(%가 들어 있음)는 그대로, Decoding 키는 인코딩해서 보낸다.
    encoded = key if re.search(r"%[0-9A-Fa-f]{2}", key) else quote(key, safe="")
    res = await client.get(f"{SCENIC_URL}?serviceKey={encoded}&pageNo=1&numOfRows=200")
    try:
        data = res.json()
    except ValueError:
        raise ToolError(f"공공데이터 API 응답을 해석하지 못했습니다 (HTTP {res.status_code}): {res.text[:200]}")
    gateway_error = (data.get("OpenAPI_ServiceResponse") or {}).get("cmmMsgHeader")
    if gateway_error:
        raise ToolError(
            f"공공데이터 API 오류: {gateway_error.get('errMsg')} ({gateway_error.get('returnAuthMsg')}). "
            "인증키와 활용신청 승인 여부를 확인해주세요. 발급 직후에는 반영까지 시간이 걸릴 수 있습니다."
        )
    root = data.get("response", data)
    header = root.get("header") or {}
    if header.get("resultCode") and str(header["resultCode"]).strip("0"):
        raise ToolError(f"공공데이터 API 오류: {header.get('resultCode')} {header.get('resultMsg', '')}")
    body = root.get("body") or {}
    items = body.get("items") or body.get("item") or []
    if isinstance(items, dict):
        items = items.get("item", items)
    if isinstance(items, dict):
        items = [items]
    _scenic_cache = [i for i in items if isinstance(i, dict)]
    return _scenic_cache


async def from_scenic(client: httpx.AsyncClient, item: dict[str, Any]) -> dict[str, Any]:
    """공공데이터 카페 한 곳을 추천 형식으로 바꾸고, 카카오에서 위치·지도 링크를 보강한다."""
    name = pick(item, r"^CAFE_?NM$", r"NM$", r"NAME|카페") or "이름 미상"
    cafe: dict[str, Any] = {
        "name": name,
        "address": pick(item, r"RDNM|ROAD|도로명", r"ADDR|ADRES|주소"),
        "phone": pick(item, r"TEL|PHONE|CTTPC|전화|연락"),
        "location": None,
        "map_url": None,
        "directions_url": None,
        "business_days": business_days_of(item) or NO_HOURS,
        "view": pick(item, r"VIEW|PRSPCT|CHARTR|FEATURE|조망|전망|특성"),
        "homepage": pick(item, r"HMPG|HOMEPAGE|URL|홈페이지"),
        "source": "경주시 전망 좋은 카페(공공데이터)",
    }
    lat, lng = pick(item, r"^LA$|LAT|YCRD|위도"), pick(item, r"^LO$|LOT$|LNG|LON|XCRD|경도")
    try:
        if lat and lng and float(lat) and float(lng):
            cafe["location"] = {"lat": float(lat), "lng": float(lng)}
    except ValueError:
        pass
    try:
        # 빵집·디저트 가게는 카페 업종이 아닐 수 있어 업종 제한 없이 이름으로 찾는다.
        docs = await kakao_search(client, f"경주 {name}", size=1, cafes_only=False)
        if docs:
            found = from_kakao(docs[0])
            cafe["location"] = cafe["location"] or found["location"]
            cafe["address"] = cafe["address"] or found["address"]
            cafe["phone"] = cafe["phone"] or found["phone"]
            cafe["map_url"] = found["map_url"]
        elif not cafe["location"] and cafe["address"]:
            cafe["location"] = await kakao_geocode(client, cafe["address"])
    except ToolError:
        pass
    if cafe["location"]:
        lat, lng = cafe["location"]["lat"], cafe["location"]["lng"]
        cafe["directions_url"] = directions_url(name, lat, lng)
        cafe["map_url"] = cafe["map_url"] or f"https://map.kakao.com/link/map/{quote(name)},{lat},{lng}"
    return {k: v for k, v in cafe.items() if v is not None}


# ---------------------------------------------------------------------------
# Google Places (영업일) — GOOGLE_PLACES_API_KEY 가 있을 때만 쓴다
# ---------------------------------------------------------------------------

_hours_cache: dict[str, str | None] = {}


def distance_m(a: dict[str, float], b: dict[str, float]) -> float:
    """두 좌표 사이의 직선거리(m)."""
    lat1, lat2 = math.radians(a["lat"]), math.radians(b["lat"])
    d_lat, d_lng = lat2 - lat1, math.radians(b["lng"] - a["lng"])
    h = math.sin(d_lat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(d_lng / 2) ** 2
    return 2 * 6371000 * math.asin(math.sqrt(h))


async def google_hours(client: httpx.AsyncClient, cafe: dict[str, Any]) -> str | None:
    """Google Places 에서 카페의 요일별 영업시간을 찾는다. 못 찾으면 None.

    이름이 같은 다른 가게의 영업시간을 붙이지 않도록, 카카오 좌표에서 300m 안의 결과만 인정한다.
    한 번 조회한 카페는 서버가 켜져 있는 동안 다시 조회하지 않는다. (호출 요금 절약)
    """
    location = cafe.get("location")
    if not location:
        return None
    cache_key = f'{cafe["name"]}@{location["lat"]:.4f},{location["lng"]:.4f}'
    if cache_key in _hours_cache:
        return _hours_cache[cache_key]

    res = await client.post(
        GOOGLE_PLACES_URL,
        headers={
            "X-Goog-Api-Key": os.environ["GOOGLE_PLACES_API_KEY"].strip(),
            "X-Goog-FieldMask": "places.location,places.businessStatus,"
            "places.regularOpeningHours.weekdayDescriptions",
        },
        json={
            "textQuery": f'경주 {cafe["name"]}',
            "languageCode": "ko",
            "regionCode": "KR",
            "maxResultCount": 3,
            "locationBias": {
                "circle": {
                    "center": {"latitude": location["lat"], "longitude": location["lng"]},
                    "radius": 500.0,
                }
            },
        },
    )
    if res.status_code != 200:
        try:
            message = res.json()["error"]["message"]
        except (ValueError, KeyError, TypeError):
            message = res.text[:200]
        raise ToolError(f"Google Places API 오류 (HTTP {res.status_code}): {message}")

    result: str | None = None
    for place in res.json().get("places", []):
        spot = place.get("location") or {}
        if "latitude" not in spot:
            continue
        if distance_m(location, {"lat": spot["latitude"], "lng": spot["longitude"]}) > 300:
            continue
        status = place.get("businessStatus")
        if status == "CLOSED_PERMANENTLY":
            result = "폐업한 것으로 표시됨 (Google 지도 기준)"
        else:
            days = (place.get("regularOpeningHours") or {}).get("weekdayDescriptions") or []
            # "월요일: 오전 10:00 ~ 오후 10:00" 형태. 7일이 모두 같으면 "매일 …" 한 줄로 줄인다.
            times = {d.split(": ", 1)[-1] for d in days}
            result = f"매일 {times.pop()}" if len(days) == 7 and len(times) == 1 else " / ".join(days) or None
            if result and status == "CLOSED_TEMPORARILY":
                result = f"임시 휴업 중으로 표시됨 · 평소 영업시간: {result}"
        break
    _hours_cache[cache_key] = result
    return result


# ---------------------------------------------------------------------------
# 추천 로직
# ---------------------------------------------------------------------------


def normalized(name: str) -> str:
    return re.sub(r"\s+", "", name)


async def pick_cafes(client: httpx.AsyncClient, mood: str) -> tuple[list[dict[str, Any]], list[str]]:
    """날씨 유형에 맞는 카페 3곳과 참고 메모를 돌려준다."""
    notes: list[str] = []
    cafes: list[dict[str, Any]] = []

    # 전망 좋은 카페 목록은 맑은 날의 추천 후보이자, 다른 날에는 영업일 정보 보강에 쓴다.
    try:
        scenic = await fetch_scenic(client)
    except ToolError as error:
        scenic = []
        notes.append(f"전망 좋은 카페 공공데이터를 쓰지 못했습니다: {error}")

    if mood == "clear" and scenic:
        chosen = random.sample(scenic, min(3, len(scenic)))
        cafes = list(await asyncio.gather(*(from_scenic(client, item) for item in chosen)))

    if len(cafes) < 3:
        seen = {normalized(c["name"]) for c in cafes}
        hours_by_name = {
            normalized(pick(item, r"^CAFE_?NM$", r"NM$", r"NAME|카페") or ""): business_days_of(item)
            for item in scenic
        }
        for query in [*MOODS[mood]["queries"], "경주 카페"]:
            if len(cafes) >= 3:
                break
            try:
                docs = await kakao_search(client, query)
            except ToolError as error:
                # 카카오를 못 쓰면 공공데이터 목록에서라도 3곳을 채운다. (좌표·지도 링크 없이 주소만)
                notes.append(f"카카오 검색을 쓰지 못해 전망 좋은 카페 목록에서 대신 골랐습니다: {error}")
                rest = [i for i in scenic if normalized(pick(i, r"^CAFE_?NM$") or "") not in seen]
                chosen = random.sample(rest, min(3 - len(cafes), len(rest)))
                cafes += await asyncio.gather(*(from_scenic(client, item) for item in chosen))
                break
            # 정확도 상위 10곳 안에서 무작위로 골라 매번 조금씩 다른 추천이 나오게 한다.
            top = docs[:10]
            random.shuffle(top)
            for doc in top:
                key = normalized(doc["place_name"])
                if key in seen:
                    continue
                seen.add(key)
                cafe = from_kakao(doc)
                cafe["business_days"] = hours_by_name.get(key) or NO_HOURS
                cafes.append(cafe)
                if len(cafes) >= 3:
                    break

    cafes = cafes[:3]
    if os.environ.get("GOOGLE_PLACES_API_KEY", "").strip():
        try:
            hours = await asyncio.gather(*(google_hours(client, cafe) for cafe in cafes))
        except (ToolError, httpx.HTTPError) as error:
            hours = [None] * len(cafes)
            notes.append(f"Google Places 에서 영업일을 가져오지 못했습니다: {error}")
        for cafe, found in zip(cafes, hours):
            if found:
                cafe["business_days"] = found
                cafe["business_days_source"] = "Google Places"
        missing = "Google Places 에서 영업시간을 찾지 못한 곳입니다."
    else:
        missing = "GOOGLE_PLACES_API_KEY 를 .env 에 넣으면 요일별 영업시간이 함께 나옵니다."

    if any(c["business_days"] == NO_HOURS for c in cafes):
        notes.append(
            f"영업일이 '정보 없음'인 곳은 map_url 의 카카오맵 상세 페이지에서 확인하도록 안내하세요. {missing}"
        )
    return cafes, notes


# ---------------------------------------------------------------------------
# MCP 도구
# ---------------------------------------------------------------------------

DayOffset = Annotated[
    int,
    Field(ge=0, le=7, description="며칠 뒤 날씨를 기준으로 할지. 0=오늘, 1=내일 … 7=일주일 뒤"),
]


@mcp.tool
async def get_gyeongju_weather(day_offset: DayOffset = 0) -> dict[str, Any]:
    """경주의 날씨(요약, 최고·최저 기온, 강수량)를 조회한다."""
    async with httpx.AsyncClient(timeout=15, verify=SSL_CONTEXT) as client:
        weather = await fetch_weather(client, day_offset)
    weather.pop("_mood")
    return weather


@mcp.tool
async def recommend_cafes(day_offset: DayOffset = 0) -> dict[str, Any]:
    """경주 날씨를 확인하고, 그 날씨에 어울리는 경주 카페 3곳을 추천한다.

    카페마다 이름, 주소, 좌표(location), 카카오맵 링크(map_url), 길찾기 링크(directions_url),
    영업일(business_days)을 함께 돌려준다.
    """
    async with httpx.AsyncClient(timeout=15, verify=SSL_CONTEXT) as client:
        weather = await fetch_weather(client, day_offset)
        mood = weather.pop("_mood")
        cafes, notes = await pick_cafes(client, mood)
    if not cafes:
        raise ToolError("추천할 카페를 찾지 못했습니다. 잠시 후 다시 시도해주세요.")
    return {
        "weather": weather,
        "reason": MOODS[mood]["reason"],
        "cafes": cafes,
        "notes": notes,
    }


if __name__ == "__main__":
    mcp.run(show_banner=False)
