#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""동토리 MCP 어댑터 — 백엔드 API 클라이언트 (api_programs 단일호출 + TTL 캐시).

계약:
  - 리스트 툴은 전부 GET /api/programs?sido= 하나로.
  - 카드에 sido 필드 없음 → 호출측이 되붙임. 상세는 GET /api/detail?id=.
  - 시도당 1회(최대 ~640건)라 전국(16키)도 병렬 16콜 + 캐시로 가벼움
    → find_place_programs의 전국 venue 인덱스가 이 캐시 위에서 동작.
"""
import os
import json
import time
import threading
from urllib.parse import urlencode
from urllib.request import urlopen, Request
from concurrent.futures import ThreadPoolExecutor

from regions import _SIDO  # 내부 시도키 16개(정식 명칭) 원천

BASE = os.environ.get("DONGTORI_API_BASE", "https://dongtori-api.datachat.kr")
TTL = int(os.environ.get("DONGTORI_CACHE_TTL", "900"))   # 15분
TIMEOUT = int(os.environ.get("DONGTORI_HTTP_TIMEOUT", "15"))

INTERNAL_SIDOS = [full for full, _short, _al in _SIDO]   # 16키("전남광주통합특별시" 포함)

_cache = {}          # sido_key -> (fetched_ts, items)
_lock = threading.Lock()


def _get_json(path, params):
    url = f"{BASE}{path}?{urlencode(params)}"
    req = Request(url, headers={"User-Agent": "dongtori-mcp/1.0"})
    with urlopen(req, timeout=TIMEOUT) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_programs(sido_key):
    """시도 1개의 활성 프로그램 카드 전량(TTL 캐시).
    fetch 실패 시 만료된 stale 캐시로 폴백(백엔드 순단에 raw 에러 노출 방지).
    응답 형태가 어긋나면 캐시하지 않고 예외(빈 지역 오답 15분 고착 방지)."""
    now = time.time()
    with _lock:
        hit = _cache.get(sido_key)
    if hit and now - hit[0] < TTL:
        return hit[1]
    try:
        data = _get_json("/api/programs", {"sido": sido_key})
        if not isinstance(data, dict) or "items" not in data:
            raise ValueError(f"unexpected /api/programs shape for {sido_key}")
        items = data["items"]
    except Exception:
        if hit:                     # 만료됐어도 직전 정상본이 낫다
            return hit[1]
        raise
    with _lock:
        _cache[sido_key] = (time.time(), items)
    return items


def _fetch_safe(sido_key):
    try:
        return fetch_programs(sido_key)
    except Exception:
        return []   # 전국 스캔에서 시도 1개 실패는 무시(부분 결과 우선)


def fetch_all_programs():
    """전국 16시도 병렬 수집(캐시 경유) → {내부시도키: [카드...]}.
    find_place_programs의 시설명 전국 매칭 전용."""
    with ThreadPoolExecutor(max_workers=8) as ex:
        results = ex.map(lambda s: (s, _fetch_safe(s)), INTERNAL_SIDOS)
        return dict(results)


def fetch_detail(program_id):
    """GET /api/detail?id= — VISIBILITY 게이트 없음(closed_now 유일 통과 경로)."""
    return _get_json("/api/detail", {"id": program_id})
