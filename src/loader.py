"""엑셀 로드 및 파싱."""

import re
from pathlib import Path

import pandas as pd

DATA_DIR = Path(__file__).resolve().parent.parent / "data"
FILE_PATTERN = re.compile(r"무공해차_보조금현황_2026_(\d{4}-\d{2}-\d{2})\.xlsx$")

SUMMARY_SHEET = "요약"
MODEL_SHEET = "모델별_지방비"

SUMMARY_COLUMNS = [
    "시도", "지역구분", "접수상태", "최종 신청마감",
    "공고대수(전체)", "접수대수(전체)", "출고대수(전체)",
    "선정잔여(전체)", "출고잔여(전체)",
    "접수율(%)", "예산소진율(%)", "담당부서", "연락처", "비고",
]

MODEL_COLUMNS = [
    "시도", "지역구분", "모델명", "제조사", "세부차종",
    "배터리", "주행거리",
    "국비(만원)", "지방비(만원)",
    "전환지원금 국비(만원)", "전환지원금 지방비(만원)",
    "총지원금(만원)", "전환 포함 총액(만원)",
]

# 만원 단위 컬럼 → 원 단위 컬럼
MANWON_COLUMNS = {
    "국비(만원)": "subsidy_national",
    "지방비(만원)": "subsidy_local",
    "전환지원금 국비(만원)": "scrap_national",
    "전환지원금 지방비(만원)": "scrap_local",
    "총지원금(만원)": "subsidy_total",
    "전환 포함 총액(만원)": "subsidy_with_scrap",
}

# "리튬이온(83.6kWh)", "84 kWh" 모두 허용. "-", "." 등은 NaN
BATTERY_RE = re.compile(r"([\d.]+)\s*kWh", re.IGNORECASE)
# "(상온) 462km (저온) 369km", "(상온)462km (저온) 369km" 모두 허용
RANGE_NORMAL_RE = re.compile(r"\(상온\)\s*(\d+)\s*km")
RANGE_COLD_RE = re.compile(r"\(저온\)\s*(\d+)\s*km")


def find_latest_file(data_dir: Path = DATA_DIR) -> Path:
    """data/ 안에서 파일명 날짜가 가장 최신인 엑셀 경로를 반환."""
    candidates = [
        (m.group(1), p)
        for p in data_dir.glob("*.xlsx")
        if (m := FILE_PATTERN.search(p.name))
    ]
    if not candidates:
        raise FileNotFoundError(f"{data_dir}에 보조금 현황 엑셀이 없습니다.")
    return max(candidates)[1]


def _parse_battery(value) -> float:
    match = BATTERY_RE.search(str(value))
    return float(match.group(1)) if match else float("nan")


def _parse_range(value, pattern: re.Pattern):
    match = pattern.search(str(value))
    return int(match.group(1)) if match else pd.NA


def _disambiguate_regions(df: pd.DataFrame, duplicated_names: set[str]) -> pd.DataFrame:
    """시도가 다른 동명 지역(예: 강원/경남 고성군)을 '고성군(강원)'으로 구분."""
    mask = df["지역구분"].isin(duplicated_names)
    df.loc[mask, "지역구분"] = df.loc[mask, "지역구분"] + "(" + df.loc[mask, "시도"] + ")"
    return df


def load_data(path=None) -> tuple[pd.DataFrame, pd.DataFrame]:
    """엑셀을 읽어 (요약_df, 모델_df)를 반환. path가 None이면 최신 파일 사용."""
    path = Path(path) if path else find_latest_file()

    summary = pd.read_excel(path, sheet_name=SUMMARY_SHEET, usecols=SUMMARY_COLUMNS)
    models = pd.read_excel(path, sheet_name=MODEL_SHEET, usecols=MODEL_COLUMNS)

    region_sido = summary[["지역구분", "시도"]].drop_duplicates()
    duplicated_names = set(region_sido.loc[region_sido["지역구분"].duplicated(), "지역구분"])
    summary = _disambiguate_regions(summary, duplicated_names)
    models = _disambiguate_regions(models, duplicated_names)

    models["battery_kwh"] = models["배터리"].map(_parse_battery)
    models["range_normal"] = models["주행거리"].map(
        lambda v: _parse_range(v, RANGE_NORMAL_RE)
    ).astype("Int64")
    models["range_cold"] = models["주행거리"].map(
        lambda v: _parse_range(v, RANGE_COLD_RE)
    ).astype("Int64")

    for src, dst in MANWON_COLUMNS.items():
        models[dst] = pd.to_numeric(models[src], errors="coerce").astype("Int64") * 10000

    return summary, models


def get_regions(summary_df: pd.DataFrame) -> list[str]:
    """지역구분 목록을 정렬해서 반환."""
    return sorted(summary_df["지역구분"].dropna().unique().tolist())


def get_models(model_df: pd.DataFrame, region: str) -> list[str]:
    """해당 지역에서 지원하는 모델명 목록."""
    rows = model_df[model_df["지역구분"] == region]
    return rows["모델명"].dropna().drop_duplicates().tolist()


def _to_python(value):
    """numpy/pandas 스칼라를 파이썬 기본 타입으로, 결측은 None으로."""
    if pd.isna(value):
        return None
    return value.item() if hasattr(value, "item") else value


def get_model_info(model_df: pd.DataFrame, region: str, model: str) -> dict:
    """지역·모델의 배터리, 주행거리, 보조금 정보."""
    rows = model_df[(model_df["지역구분"] == region) & (model_df["모델명"] == model)]
    if rows.empty:
        raise KeyError(f"{region}에 '{model}' 모델이 없습니다.")
    row = rows.iloc[0]
    return {
        "battery_kwh": _to_python(row["battery_kwh"]),
        "range_normal": _to_python(row["range_normal"]),
        "range_cold": _to_python(row["range_cold"]),
        "subsidy_national": _to_python(row["subsidy_national"]),
        "subsidy_local": _to_python(row["subsidy_local"]),
        "scrap_national": _to_python(row["scrap_national"]),
        "scrap_local": _to_python(row["scrap_local"]),
        "subsidy_total": _to_python(row["subsidy_total"]),
        "subsidy_with_scrap": _to_python(row["subsidy_with_scrap"]),
        "maker": _to_python(row["제조사"]),
    }


def _clean_notice(value):
    """지자체 공지 원문. 비어 있으면 None."""
    if value is None or pd.isna(value):
        return None
    text = str(value).strip()
    return text or None


def get_region_status(summary_df: pd.DataFrame, region: str) -> dict:
    """지역의 접수 현황."""
    rows = summary_df[summary_df["지역구분"] == region]
    if rows.empty:
        raise KeyError(f"'{region}' 지역이 없습니다.")
    row = rows.iloc[0]
    return {
        "접수상태": _to_python(row["접수상태"]),
        "접수율": _to_python(row["접수율(%)"]),
        "출고잔여": _to_python(row["출고잔여(전체)"]),
        "공고대수": _to_python(row["공고대수(전체)"]),
        "접수대수": _to_python(row["접수대수(전체)"]),
        "담당부서": _to_python(row["담당부서"]),
        "연락처": _to_python(row["연락처"]),
        "최종신청마감": _to_python(row["최종 신청마감"]),
        "notice": _clean_notice(row["비고"]),
    }
