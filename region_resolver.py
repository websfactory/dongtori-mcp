#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""동토리 MCP 어댑터 — 지역 해소 로직 (검증 완료본, 2026-07-06 세션).

역할: 부모의 자유 발화(sido 문자열 / sigungu 문자열)를 백엔드가 이해하는
      (내부 시도키, 시군구 리스트)로 정규화한다. **대화형 MCP 전용**(앱은 드롭다운이라 불필요).

데이터: `data/region_master_snapshot.json` = 지역 마스터 스냅샷.
        추후 백엔드 지역 API가 서면 로더만 교체(데이터=자유, 인터페이스=동결).

정규화 파이프(시도): 동봉된 `regions.py`의 norm_sido를 재사용.

★검증: norm_sigungu 21/21 + infer_sido 8/8 통과(지역 마스터 실데이터).
★함정: substring/contains 매칭 절대 금지 — "해운대구"처럼 구 이름에 타 시도명이
       포함될 수 있어 접두/정확 매칭만 허용.
"""
import re
import os
import json

# ── 시도 정규화(norm_sido) 재사용 ─────────────────────────────────────────
# 동봉 사본 regions.py의 시도 정규화를 재사용. 시도 개편 시 원본 갱신 후 재복사.
from regions import norm_sido, DEFAULT_SIDO  # noqa: F401

# ── 시군구 마스터(내부 시도키 기준) ────────────────────────────────────────
_SNAPSHOT = os.path.join(os.path.dirname(__file__), "data", "region_master_snapshot.json")
SIGUNGUS_BY_SIDO = json.load(open(_SNAPSHOT, encoding="utf-8"))  # {내부시도키: [시군구...]}

# ── LLM 대면 sido enum (17값, 정식명, 광주·전남 분리) ─────────────────────
#   서버/마스터 내부키는 "전남광주통합특별시"(16). norm_sido가 17→16 흡수.
SIDO_ENUM = [
    "서울특별시", "부산광역시", "대구광역시", "인천광역시",
    "광주광역시", "전라남도", "대전광역시", "울산광역시",
    "세종특별자치시", "경기도", "강원특별자치도", "충청북도",
    "충청남도", "전북특별자치도", "경상북도", "경상남도",
    "제주특별자치도",
]

# ── 랜드마크/통칭 → 시군구(대구·부산 고빈도, 확장 가능). 값 None = 시(통칭) union ──
LANDMARK_ALIASES = {
    "대구광역시": {"동성로": "중구", "앞산": "남구", "이월드": "달서구", "두류공원": "달서구"},
    "부산광역시": {"센텀시티": "해운대구", "해운대해수욕장": "해운대구", "광안리": "수영구",
                "서면": "부산진구", "남포동": "중구"},
    "경기도": {"일산": None},   # 고양시 일산동/서구 통칭
}


def _canon(s):
    """공백 전부 제거한 매칭용 정규형('성남시 분당구' → '성남시분당구')."""
    return re.sub(r"\s+", "", (s or "").strip())


def norm_sigungu(sido, raw):
    """sido 안에서 raw를 시군구로 정규화. 반환:
       - list[str]: 매칭된 시군구(들). 'OO시'면 하위 구 전체(union).
       - None: 빈입력·단층(세종)·못찾음 → 시도 전역(호출측이 고지).
       ★substring/contains 매칭 금지(해운대해수욕장⊃해운대 오매칭 방지)."""
    key = norm_sido(sido)
    lst = SIGUNGUS_BY_SIDO.get(key, [])
    if not lst:
        return None                      # 세종 등 단층 → 전역
    raw_c = _canon(raw)
    if not raw_c:
        return None

    canon2full = {_canon(s): s for s in lst}
    # 일반구 보유 시 = 구 단위 행에서 시 이름 역산(region_master는 본체 시행을 뺌).
    cities = sorted({s.split()[0] for s in lst if " " in s and s.split()[0].endswith("시")})

    def city_union(city):
        return [x for x in lst if x.startswith(city + " ")]

    # (1) 'OO시' 또는 어간 'OO' → 그 시 하위 구 union
    for city in cities:
        if raw_c in (_canon(city), city[:-1]):
            kids = city_union(city)
            if kids:
                return kids

    # (2) 정확 일치(공백무시)
    if raw_c in canon2full:
        return [canon2full[raw_c]]

    # (3) 구/시/군 단독 → 유일 endswith ('분당구' → '성남시 분당구')
    if raw_c[-1:] in "구시군":
        ends = [full for c, full in canon2full.items() if c.endswith(raw_c)]
        if len(ends) == 1:
            return ends
        if len(ends) > 1:
            return None

    # (4) 접미사 보정: 구/시/군 없으면 붙여 재시도 ('해운대'→'해운대구')
    if raw_c[-1:] not in "구시군읍면동":
        for suf in ("구", "시", "군"):
            cand = raw_c + suf
            if cand in canon2full:
                return [canon2full[cand]]
            ends = [full for c, full in canon2full.items() if c.endswith(cand)]
            if len(ends) == 1:
                return ends

    # (4b) 다토큰: 마지막 토큰에 접미사 보정 ('성남 분당'→'분당구'→'성남시 분당구')
    toks = (raw or "").split()
    if len(toks) >= 2:
        last = _canon(toks[-1])
        cands = [last] if last[-1:] in "구시군" else [last + s for s in ("구", "시", "군")]
        for cand in cands:
            ends = [full for c, full in canon2full.items() if c.endswith(cand)]
            if len(ends) == 1:
                return ends

    # (5) 랜드마크/통칭 별칭
    lm = LANDMARK_ALIASES.get(key, {})
    lm_canon = {_canon(k): k for k in lm}
    if raw_c in lm_canon:
        target = lm[lm_canon[raw_c]]
        if target is None:                     # 시 통칭 → 포함 구만(사전 명시항목 통제)
            for city in cities:
                matched = [x for x in city_union(city) if raw in x]
                if matched:
                    return matched
            return None
        return [target]

    return None                                # 못찾음 → 전역


def infer_sido(raw):
    """sido 없이 sigungu만 왔을 때 시도 역추론(norm_sigungu 재사용).
       반환: ('ok', 내부시도키) / ('ambiguous', [내부시도키...]) / ('none', [])."""
    hits = [sido for sido in SIGUNGUS_BY_SIDO if norm_sigungu(sido, raw)]
    if len(hits) == 1:
        return ("ok", hits[0])
    if len(hits) > 1:
        return ("ambiguous", hits)
    return ("none", [])


def resolve_region(sido, sigungu):
    """어댑터 지역 해소 진입점. 계약: sido·sigungu 중 최소 하나.
       반환 dict:
         {'status': 'ok', 'sido': 내부키, 'sigungus': [..]|None(전역), 'note': str|None}
         {'status': 'ask',  'reason': 'no_region'|'ambiguous_sigungu', 'candidates': [...]}
       ★DEFAULT_SIDO 조용한 폴백 금지 — 지역 신호 없으면 'ask'."""
    sido = (sido or "").strip()
    sigungu = (sigungu or "").strip()

    if not sido and not sigungu:
        return {"status": "ask", "reason": "no_region", "candidates": []}

    if not sido:  # sigungu만 → 역추론
        st, val = infer_sido(sigungu)
        if st == "ok":
            sido = val
        elif st == "ambiguous":
            return {"status": "ask", "reason": "ambiguous_sigungu", "candidates": val}
        else:
            return {"status": "ask", "reason": "no_region", "candidates": []}

    key = norm_sido(sido)
    sgg = norm_sigungu(key, sigungu) if sigungu else None
    note = None
    if sigungu and sgg is None:
        note = "wide_fallback"   # 입력한 동네를 못 찾아 시도 전역으로(호출측 고지)
    return {"status": "ok", "sido": key, "sigungus": sgg, "note": note}


if __name__ == "__main__":
    # 스모크 테스트(검증본 요약)
    for a in [("경기도", "수원시"), ("부산광역시", "해운대"), (None, "수성구"),
              (None, "중구"), ("세종특별자치시", "조치원읍"), ("대구광역시", "동성로")]:
        print(a, "→", resolve_region(*a))
