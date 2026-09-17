"""지자체별 전기차 충전소·충전기 개수 수집.

한국환경공단 전기자동차 충전소 정보 API(getChargerInfo)를 data/region_codes.json의
지역마다 호출해 집계하고 data/charger_counts.json에 저장한다.
챗봇은 이 파일만 읽는다(질문마다 API를 호출하지 않음).
파일 상단 _meta.collected_at이 답변에 붙일 기준일이다.

실행: python scripts/collect_chargers.py [--max-calls 900] [--delay 1.0]
필요: .env의 DATA_GO_KR_API_KEY
"""

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import date
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
REGION_CODES_PATH = ROOT / "data" / "region_codes.json"
OUTPUT_PATH = ROOT / "data" / "charger_counts.json"

ENDPOINT = "https://apis.data.go.kr/B552584/EvCharger/getChargerInfo"
PAGE_SIZE = 9999  # API 최대값
RETRIES = 3
TIMEOUT = 120

FAST_KW = 50
BUS_ONLY = "11"  # DC콤보2(버스전용)
# output이 비었을 때 충전기 타입으로 보완
TYPE_FALLBACK = {"02": "slow", "06": "fast"}
CRITERIA = (
    "delYn=Y·버스전용(chgerType 11) 제외, 급속 output≥50kW, 완속 <50kW, "
    "output 없으면 02 완속·06 급속, 충전소=고유 statId"
)


class QuotaExceeded(Exception):
    pass


class Client:
    """호출 수를 세어 한도 전에 멈춘다. 인증키는 출력하지 않는다."""

    def __init__(self, key: str, max_calls: int, delay: float):
        self.key = urllib.parse.quote(key, safe="") if "%" not in key else key
        self.max_calls = max_calls
        self.delay = delay
        self.calls = 0

    def page(self, query: dict, page_no: int) -> dict:
        params = urllib.parse.urlencode(
            {"pageNo": page_no, "numOfRows": PAGE_SIZE, "dataType": "JSON", **query}
        )
        url = f"{ENDPOINT}?serviceKey={self.key}&{params}"
        last_error = None
        for attempt in range(1, RETRIES + 1):
            if self.calls >= self.max_calls:
                raise QuotaExceeded(f"호출 수 상한 {self.max_calls}회 도달")
            if self.calls:
                time.sleep(self.delay)
            self.calls += 1
            try:
                with urllib.request.urlopen(url, timeout=TIMEOUT) as resp:
                    body = json.loads(resp.read())
            except urllib.error.HTTPError as e:
                last_error = f"HTTP {e.code}"
            except (urllib.error.URLError, TimeoutError) as e:
                last_error = f"네트워크 오류 ({getattr(e, 'reason', e)})"
            except json.JSONDecodeError:
                last_error = "JSON이 아닌 응답 (인증 오류 가능)"
            else:
                if body.get("resultCode") == "00":
                    return body
                header = body.get("OpenAPI_ServiceResponse", {}).get("cmmMsgHeader", {})
                last_error = body.get("resultMsg") or header.get("returnAuthMsg") or "알 수 없는 응답"
                if "LIMITED" in str(header.get("errMsg", "")) or "LIMITED" in str(last_error):
                    raise QuotaExceeded(f"API 트래픽 한도 초과: {last_error}")
            time.sleep(self.delay * 2 * attempt)
        raise RuntimeError(f"{query} {page_no}페이지 실패: {last_error}")

    def fetch_all(self, query: dict) -> list[dict]:
        first = self.page(query, 1)
        total = int(first.get("totalCount") or 0)
        items = _items(first)
        pages = -(-total // PAGE_SIZE)
        for page_no in range(2, pages + 1):
            items += _items(self.page(query, page_no))
        if len(items) != total:
            print(f"    경고: {query} totalCount {total}건 중 {len(items)}건 수신", flush=True)
        return items


def _items(body: dict) -> list[dict]:
    items = (body.get("items") or {}).get("item") or []
    return items if isinstance(items, list) else [items]


def classify(item: dict) -> str | None:
    try:
        return "fast" if float(item.get("output") or "") >= FAST_KW else "slow"
    except ValueError:
        return TYPE_FALLBACK.get(item.get("chgerType"))


def aggregate(items: list[dict]) -> dict:
    """여러 query의 결과를 합칠 수 있도록 (statId, chgerId)로 중복을 제거한다."""
    chargers = {}
    for item in items:
        if item.get("delYn") == "Y" or item.get("chgerType") == BUS_ONLY:
            continue
        chargers[(item.get("statId"), item.get("chgerId"))] = item

    counts = {"fast": 0, "slow": 0, "unclassified": 0}
    for item in chargers.values():
        counts[classify(item) or "unclassified"] += 1

    result = {
        "stations": len({station for station, _ in chargers}),
        "chargers": len(chargers),
        "fast": counts["fast"],
        "slow": counts["slow"],
    }
    if counts["unclassified"]:
        result["unclassified"] = counts["unclassified"]  # output도 없고 보완 타입도 아닌 충전기
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--max-calls", type=int, default=900, help="이번 실행의 최대 API 호출 수 (일일 한도 1,000)")
    parser.add_argument("--delay", type=float, default=1.0, help="호출 간 간격(초)")
    args = parser.parse_args()

    load_dotenv(ROOT / ".env")
    key = os.environ.get("DATA_GO_KR_API_KEY", "").strip()
    if not key:
        print("DATA_GO_KR_API_KEY가 없습니다 (.env 확인)", file=sys.stderr)
        return 1

    regions = json.loads(REGION_CODES_PATH.read_text(encoding="utf-8"))["regions"]
    client = Client(key, args.max_calls, args.delay)
    today = date.today().isoformat()
    results, failed = {}, {}

    for n, (region, info) in enumerate(regions.items(), start=1):
        print(f"[{n}/{len(regions)}] {region} (누적 호출 {client.calls})", flush=True)
        try:
            items = []
            for query in info["query"]:
                items += client.fetch_all(query)
        except QuotaExceeded as e:
            print(f"중단: {e}", flush=True)
            failed.update({r: "호출 한도로 미수집" for r in list(regions)[n - 1:]})
            break
        except RuntimeError as e:
            print(f"    실패: {e}", flush=True)
            failed[region] = str(e)
            continue
        results[region] = {**aggregate(items), "collected_at": today}
        print(f"    {results[region]}", flush=True)

    meta = {
        "collected_at": today,
        "source": "한국환경공단 전기자동차 충전소 정보 API (getChargerInfo)",
        "criteria": CRITERIA,
    }
    OUTPUT_PATH.write_text(
        json.dumps({"_meta": meta, **results}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"\n저장: {OUTPUT_PATH.relative_to(ROOT)} ({len(results)}개 지역), 총 호출 {client.calls}회")
    if failed:
        print(f"실패 {len(failed)}개 지역 (파일에서 제외):")
        for region, reason in failed.items():
            print(f"  - {region}: {reason}")
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
