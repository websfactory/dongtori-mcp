#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
지역(시도) 마스터 — 전국확대 준비 ("게이트는 제거가 아니라 파라미터화").

한 곳에 모으는 것:
  - 시도 표기 정규화(norm_sido): 카카오 keyword 검색은 "대구", coord2regioncode 는
    "대구광역시"를 줘서 같은 시도가 두 표기로 갈라진다(실측 79:82). venue eid·서버 필터가
    표기에 흔들리지 않도록 항상 정식 명칭으로 수렴시킨다.
  - 지오코딩 오매칭 게이트(match_region): 동명 기관(도서관 등)이 타 시도에 많아
    "기대 시도" 밖 결과는 기각한다. 시도별 bbox·예외 시군구를 여기 등록해 확장한다.
  - 시군구 마스터(SIGUNGUS_BY_SIDO): 동네 시트·resolver 이름 정규화 공용.

시도 추가 시 이 파일만 갱신.
"""

# (정식 명칭, 단축형, 그 외 별칭들) — 2026 현행 16개 시도 + 개편 전 옛 명칭.
# ★광주+전남 = "전남광주통합특별시"(카카오 coord2regioncode 실측 2026-07-04, 광주 동구·목포 동일 응답).
#   표준데이터 주소는 아직 옛 명칭(광주광역시·전라남도)이라 별칭으로 흡수 → 한 표기로 수렴.
#   ("광주시"는 경기도 광주시와 겹쳐 별칭에 안 넣음 — 기존 광주광역시 항목의 관행 유지.)
_SIDO = [
    ("서울특별시", "서울", ("서울시",)),
    ("부산광역시", "부산", ("부산시",)),
    ("대구광역시", "대구", ("대구시",)),
    ("인천광역시", "인천", ("인천시",)),
    ("전남광주통합특별시", "전남광주", ("광주광역시", "광주", "전라남도", "전남")),
    ("대전광역시", "대전", ("대전시",)),
    ("울산광역시", "울산", ("울산시",)),
    ("세종특별자치시", "세종", ("세종시", "세종특별시")),
    ("경기도", "경기", ()),
    ("강원특별자치도", "강원", ("강원도",)),
    ("충청북도", "충북", ()),
    ("충청남도", "충남", ()),
    ("전북특별자치도", "전북", ("전라북도",)),
    ("경상북도", "경북", ()),
    ("경상남도", "경남", ()),
    ("제주특별자치도", "제주", ("제주도",)),
]

_TO_FULL = {}
_TO_SHORT = {}
for _full, _short, _aliases in _SIDO:
    _TO_SHORT[_full] = _short
    for a in (_full, _short) + _aliases:
        _TO_FULL[a] = _full

DEFAULT_SIDO = "대구광역시"


def norm_sido(s):
    """시도 표기 → 정식 명칭("대구"·"대구시" → "대구광역시"). 미등록 값은 strip 만."""
    s = (s or "").strip()
    return _TO_FULL.get(s, s)


def short_sido(s):
    """시도 → 단축형("대구광역시" → "대구"). 미등록 값은 그대로."""
    return _TO_SHORT.get(norm_sido(s), norm_sido(s))


def region_wide_label(sido):
    """시 전역 우산 sigungu 값("대구광역시" → "대구 전역"). 기존 저장 리터럴과 호환."""
    return f"{short_sido(sido)} 전역"


# 지오코딩 오매칭 방지 게이트. bbox=(lng_min, lng_max, lat_min, lat_max).
# extra_sigungu: 시도 경계 개편 등으로 카카오가 다른 시도로 줄 수 있는 예외
#   (군위군 = 2023 대구 편입, 옛 경북 표기 응답 허용).
REGION_GATES = {
    "대구광역시": {
        "bbox": (128.4, 128.8, 35.7, 36.3),
        "extra_sigungu": ("군위",),
    },
    # 부산 = 시범 확대 지역. 기장군 북단~가덕도 포함.
    "부산광역시": {
        "bbox": (128.75, 129.35, 34.95, 35.45),
        "extra_sigungu": (),
    },
}


def sido_from_address(addr):
    """주소 문자열 → 정식 시도 명칭. 표준데이터 주소 오염 대응:
    공백 없는 "강원특별자치도양구군", 옛 명칭 "전라북도", "세종특별시" 등.
    첫 토큰 정규화 → 실패 시 전체 명칭·별칭 접두 매칭(긴 것 우선). 못 찾으면 ""."""
    addr = (addr or "").strip()
    if not addr:
        return ""
    head = addr.split()[0]
    if head in _TO_FULL:
        return _TO_FULL[head]
    # 공백 없는 오염 주소 폴백. ★3글자 이상 명칭만 접두 매칭(적대 리뷰 F1·F3):
    #   2글자 단축형을 넣으면 "광주시 …"(경기도 광주시, 도 생략 표기)가 "광주" 접두에 걸려
    #   전남광주로, "세종대로 110"(서울)이 "세종"에 걸려 세종시로 오분류된다.
    #   못 찾으면 "" = 시도 미상으로 두고 지오코딩 결과를 따르는 게 안전.
    for key in sorted(_TO_FULL, key=len, reverse=True):
        if len(key) >= 3 and addr.startswith(key):
            return _TO_FULL[key]
    return ""


def match_region(expected_sido, result_sido, result_sigungu, lng, lat):
    """지오코딩 결과가 기대 시도 안인가. 시도명 일치 → 예외 시군구 → bbox 순.
    bbox 미등록 시도는 시도명 일치로만 판정(보수적)."""
    exp = norm_sido(expected_sido) or DEFAULT_SIDO
    if norm_sido(result_sido) == exp:
        return True
    gate = REGION_GATES.get(exp, {})
    for x in gate.get("extra_sigungu", ()):
        if x in (result_sigungu or ""):
            return True
    b = gate.get("bbox")
    if b and lng is not None and lat is not None:
        return b[0] <= lng <= b[1] and b[2] <= lat <= b[3]
    return False


def search_prefixes(sido):
    """동명 기관 오매칭 방지용 지오코딩 검색 접두어(단축형 우선, 카카오 검색 관행)."""
    exp = norm_sido(sido) or DEFAULT_SIDO
    return (short_sido(exp), exp)


# 시군구 마스터(동네 시트·resolver 이름 정규화 공용). 순서 = 화면 노출 순서.
# 지역 확대 시 그 시도의 시군구를 여기 추가한다(소스 없는 시도는 등록 안 함).
SIGUNGUS_BY_SIDO = {
    "대구광역시": ["수성구", "북구", "달서구", "동구", "중구", "서구", "남구", "달성군", "군위군"],
    # 부산 = 시범 확대 지역. 노출 순서는 인구 큰 구 우선, 군 마지막(대구 관행 동일).
    "부산광역시": ["해운대구", "부산진구", "사하구", "동래구", "남구", "북구", "금정구",
                "연제구", "사상구", "수영구", "영도구", "동구", "서구", "중구", "강서구", "기장군"],
}


def sigungus_of(sido):
    return SIGUNGUS_BY_SIDO.get(norm_sido(sido) or DEFAULT_SIDO, [])
