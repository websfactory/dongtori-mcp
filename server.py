#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""동토리(Dongtori) MCP 서버: 카카오 AGENTIC PLAYER 10 출품.

우리 동네 아이 프로그램 큐레이션. 툴 4개:
  find_drop_in_places   상시 방문형(dropin) 장소
  find_sign_up_programs 신청형(apply) 프로그램
  find_place_programs   특정 시설의 프로그램 전부(dropin+apply 무구분)
  get_program_detail    상세 1건

설계 계약:
  - 백엔드 = programs API 단일호출 + client-side 필터. 상세만 detail API.
  - 지역 = sido(17 enum)·sigungu(free-text) 중 최소 하나. 없으면 되묻기.
    기본 지역 조용한 폴백 금지: 전국구 서비스.
  - free_only = is_free None(미상) 포함 + "요금 미확인" 표시(strict 아님).
  - inclusive = true면 배려시설·배지 위로 부스팅(하드필터 금지). false면 전용시설 제외.
  - 응답 = 정제 마크다운, top10 + 잘라내기 고지. raw 덤프 금지.

구현 노트:
  - 파라미터 설명은 Field(description=)로만 스키마에 실린다(docstring은 사장) → 전면 이식.
  - description 국문 본문 + 말미 영문 요약(1024자 이내 영/국 병기). 발화 예시는 번역 안 함.
  - 툴은 async + to_thread(동기 HTTP가 이벤트루프 점유 방지). 날짜는 KST 고정.
"""
import os
from datetime import datetime
from functools import partial
from typing import Annotated, List, Literal, Optional
from zoneinfo import ZoneInfo

import anyio.to_thread
from pydantic import Field
from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

import backend
import render
from region_resolver import SIDO_ENUM, resolve_region
from regions import norm_sido, region_wide_label

TOP_N = 10

Sido = Literal[
    "서울특별시", "부산광역시", "대구광역시", "인천광역시",
    "광주광역시", "전라남도", "대전광역시", "울산광역시",
    "세종특별자치시", "경기도", "강원특별자치도", "충청북도",
    "충청남도", "전북특별자치도", "경상북도", "경상남도",
    "제주특별자치도",
]
assert list(Sido.__args__) == SIDO_ENUM  # enum 17 ↔ resolver 원천 일치 가드

READONLY = ToolAnnotations(
    readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=True,
)

mcp = FastMCP(
    "dongtori",
    instructions=(
        "동토리(Dongtori)는 아이와 갈 만한 곳·참여할 프로그램을 찾아주는 전국 서비스입니다. "
        "신청 없이 방문하는 상시 장소는 find_drop_in_places, 신청·접수가 필요한 프로그램은 "
        "find_sign_up_programs, 특정 시설 이름을 콕 집어 물으면 find_place_programs, "
        "검색 결과 하나를 파고들 땐 get_program_detail을 쓰세요. "
        "지역(시/도나 동네)을 모르면 먼저 물어보세요. "
        "(EN) Dongtori finds kid-friendly places and programs across Korea: "
        "find_drop_in_places for walk-in venues, find_sign_up_programs for programs requiring "
        "registration, find_place_programs for a specific named venue, get_program_detail for "
        "one item's details. Ask for the region first if unknown."
    ),
    host="0.0.0.0",
    port=int(os.environ.get("PORT", "8080")),
    stateless_http=True,
    json_response=True,
)

# ── 파라미터 Field 정의 (★docstring은 스키마에 안 실림: 여기만 호스트에 전달됨) ──

F_SIDO = Field(description=(
    "시/도 정식 명칭으로(예: '대구'는 '대구광역시', '전남'은 '전라남도'로 변환해 입력). "
    "/ Province or metro city, full official Korean name."))
F_SIGUNGU = Field(description=(
    "동네·시군구·랜드마크 이름 자유 입력(예: 수성구, 해운대, 동성로). "
    "sido와 sigungu 중 하나는 반드시 필요. "
    "/ District, neighborhood, or landmark; free text. At least one of sido/sigungu required."))
F_AGES = Field(description=(
    "아이 나이 목록: 부모가 말한 나이 그대로 정수 배열로(예: 5살·8살이면 [5, 8]). "
    "하나라도 대상에 맞으면 노출됩니다. / Children's ages as an integer array."))
F_FREE = Field(description=(
    "무료 위주로 보기. 요금 미확인 건은 '요금 미확인'으로 표시하고 함께 보여줍니다. "
    "/ Prefer free items; unknown-fee items are still shown with a note."))
F_INCLUSIVE = Field(description=(
    "발달이 느리거나 장애가 있는 아이를 배려하는 곳을 위로 올립니다. "
    "부모가 그런 배려를 요청할 때만 true. "
    "/ Boost disability-friendly items; set true only when explicitly requested."))
F_PLACE = Field(description=(
    "시설의 고유 이름(예: '북부도서관', '국립대구과학관'). "
    "'도서관'처럼 시설 종류만으로는 불가: 그럴 땐 지역 검색 툴을 쓰세요. "
    "/ Proper venue name; a facility type alone (e.g. 'library') is not valid."))
# ★place 툴 전용: 지역은 '선택'(동명 구분용). 공용 F_SIDO/F_SIGUNGU의
#   "하나는 반드시 필요" 문구를 재사용하면 호스트가 지역부터 되물어 전국 스캔 강점이 죽는다
#   (stdio 라우팅 테스트 case4 실측).
F_PLACE_SIDO = Field(description=(
    "선택. 같은 이름의 시설이 여러 지역에 있을 때 구분용 시/도 정식 명칭. "
    "몰라도 됩니다: 지역 없이 호출하면 전국에서 찾아 확인해 드립니다. "
    "/ Optional; only to disambiguate same-named venues. Omit to search nationwide."))
F_PLACE_SIGUNGU = Field(description=(
    "선택. 동명 시설 구분용 동네 이름(예: 수성구). 몰라도 됩니다. "
    "/ Optional district name for disambiguation."))
F_PROGRAM_ID = Field(description=(
    "find_* 검색 결과 각 항목에 표시된 id 정수. "
    "/ Integer id shown on items in previous search results."))


# ── 공통 필터 (client-side, 호출계약 문서 그대로) ─────────────────────────

def _today():
    """KST 기준 오늘(컨테이너 TZ 무관: UTC 컨테이너에서 새벽 시간대 하루 어긋남 방지)."""
    return datetime.now(ZoneInfo("Asia/Seoul")).date().isoformat()


def _age_ok(item, ages):
    """ages OR: 하나라도 대상 범위에 걸치면 통과. 가족 대상·범위 미상은 통과."""
    if not ages:
        return True
    if item.get("target_is_family"):
        return True
    lo, hi = item.get("target_age_min"), item.get("target_age_max")
    if lo is None and hi is None:
        return True
    return any((lo is None or a >= lo) and (hi is None or a <= hi) for a in ages)


def _free_ok(item, free_only):
    """free_only여도 is_free None(미상)은 살린다: False(유료 확인)만 제외."""
    return (not free_only) or (item.get("is_free") is not False)


def _sigungu_ok(item, sigungus, sido_key):
    """sigungu ∈ 요청 구들 ∪ {'OO 전역'}(시 전역 프로그램 누락 방지). None=시도 전역.
    카드 sigungu=None(위치미상, 대구 13건)은 구 질의에서 보수적 제외: 특정 구 결과에
    위치 모르는 카드를 넣으면 오답 위험(의도된 결정, 시도 전체 질의에선 노출됨)."""
    if not sigungus:
        return True
    allowed = set(sigungus) | {region_wide_label(sido_key)}
    return item.get("sigungu") in allowed


def _inclusive_filter(items, inclusive):
    """inclusive=false → 발달·장애 전용시설 제외. true → 포함하고 위로 부스팅(배제 금지)."""
    if not inclusive:
        return [i for i in items if i.get("venue_type") != "disability"]
    return sorted(items, key=lambda i: 0 if (
        i.get("venue_type") == "disability" or i.get("support_badge")) else 1)


def _start_ok(item, today):
    """dropin 노출게이트 미러링. 날짜가 datetime 문자열로 와도 [:10]으로 방어."""
    d = item.get("event_start_date")
    return d is None or d[:10] <= today


def _close_ok(item, today):
    """apply 노출게이트 미러링."""
    d = item.get("apply_close_at")
    return d is None or d[:10] >= today


def _resolve_or_ask(sido, sigungu):
    """지역 해소. 실패 시 (None, 되묻기 마크다운)."""
    r = resolve_region(sido, sigungu)
    if r["status"] == "ok":
        return r, None
    if r["reason"] == "ambiguous_sigungu":
        cands = " / ".join(f"{render.display_sido(c)} {sigungu}" for c in r["candidates"])
        return None, (f"'{sigungu}'라는 이름의 지역이 여러 곳에 있어요: {cands}\n"
                      f"어느 지역인지 알려주시면 바로 찾아드릴게요.")
    return None, ("어느 지역에서 찾아드릴까요? 시/도나 동네 이름(예: 대구 수성구, 부산 해운대)을 "
                  "알려주세요. 동토리는 전국 어디든 찾아볼 수 있어요.")


def _region_header(reg, sigungu_input):
    """wide_fallback 고지(입력 동네를 못 찾아 시도 전역으로)."""
    if reg.get("note") == "wide_fallback" and sigungu_input:
        return render.wide_fallback_notice(sigungu_input, reg["sido"])
    return ""


def _region_label(reg):
    if reg["sigungus"]:
        return render.display_region(reg["sido"], reg["sigungus"][0]) if len(reg["sigungus"]) == 1 \
            else f"{render.display_sido(reg['sido'])} {'·'.join(reg['sigungus'])}"
    return f"{render.display_sido(reg['sido'])} 전체"


DETAIL_HINT = ("\n\n_더 알아보고 싶은 항목이 있으면 말씀해 주세요. "
               "(상세 조회: get_program_detail, program_id=각 항목의 id)_")


# ── 툴 1: 상시 방문형 ─────────────────────────────────────────────────────

DESC_DROP_IN = (
    "예약이나 신청 없이 아이와 바로 방문할 수 있는 상시 개방 장소를 찾습니다. "
    "도서관·미술관·박물관·과학관처럼 운영시간에 맞춰 가면 되는 곳이에요. "
    "응답은 운영시간·휴관일·요금·위치를 안내합니다. "
    "부모가 \"지금 애 데리고 잠깐 갈 데\", \"주말에 놀러갈 곳\", \"여기 놀러 왔는데 잠시 들를 데\"처럼 "
    "날 잡아 그냥 가면 되는 곳을 찾을 때 쓰세요. sido나 sigungu 중 하나는 필요합니다. "
    "정해진 기간에 신청·접수·모집하는 강좌·교실을 찾을 때는 이 툴이 아니라 "
    "find_sign_up_programs를 쓰세요(이 툴은 신청 마감이 아니라 운영시간을 다룹니다). "
    "특정 시설 이름을 콕 집어 물으면(예: \"대구미술관 전시 뭐 해?\") find_place_programs를 쓰세요. "
    "(EN) Finds walk-in, no-registration places for kids (libraries, museums, science centers) "
    ": opening hours, closures, fees. Requires sido or sigungu. "
    "For programs needing advance sign-up use find_sign_up_programs; "
    "for one specific named venue use find_place_programs."
)


@mcp.tool(annotations=READONLY, description=DESC_DROP_IN)
async def find_drop_in_places(
    sido: Annotated[Optional[Sido], F_SIDO] = None,
    sigungu: Annotated[Optional[str], F_SIGUNGU] = None,
    ages: Annotated[Optional[List[int]], F_AGES] = None,
    free_only: Annotated[bool, F_FREE] = False,
    inclusive: Annotated[bool, F_INCLUSIVE] = False,
) -> str:
    return await anyio.to_thread.run_sync(
        partial(_find_drop_in, sido, sigungu, ages, free_only, inclusive))


def _find_drop_in(sido, sigungu, ages, free_only, inclusive):
    reg, ask = _resolve_or_ask(sido, sigungu)
    if ask:
        return ask
    today = _today()
    items = backend.fetch_programs(reg["sido"])
    in_region = [i for i in items if _sigungu_ok(i, reg["sigungus"], reg["sido"])]
    hits = [i for i in in_region
            if i.get("participation_type") == "dropin"
            and _start_ok(i, today)
            and _age_ok(i, ages) and _free_ok(i, free_only)]
    hits = _inclusive_filter(hits, inclusive)

    label = _region_label(reg)
    if not hits:
        out = render.empty_notice(label, "가볼 만한 곳")
        # cross-sell은 같은 지역 필터 기준으로만(시도 전체 기준 거짓 안내 방지)
        if any(i.get("participation_type") == "apply" for i in in_region):
            out += ("\n\n대신 이 지역엔 신청해서 참여하는 프로그램이 있어요. "
                    "find_sign_up_programs로 찾아볼까요?")
        return _region_header(reg, sigungu) + out

    # 장소 중심 그룹핑(같은 venue의 전시·행사 묶음). 오늘 휴관은 아래로.
    groups, order = {}, []
    for i in hits:
        k = i.get("venue_id") or f"p{i.get('id')}"
        if k not in groups:
            groups[k] = []
            order.append(k)
        groups[k].append(i)
    order.sort(key=lambda k: 1 if groups[k][0].get("closed_today") else 0)

    shown = order[:TOP_N]
    cards = [render.dropin_venue_card(reg["sido"], groups[k]) for k in shown]
    out = _region_header(reg, sigungu) + f"**{label}**에서 아이와 가볼 만한 곳이에요.\n\n" \
        + "\n\n".join(cards)
    if len(order) > len(shown):
        out += render.truncation_notice(len(shown), len(order))
    return out + DETAIL_HINT


# ── 툴 2: 신청형 ──────────────────────────────────────────────────────────

DESC_SIGN_UP = (
    "미리 신청·접수해야 참여할 수 있는 아이 프로그램을 찾습니다. "
    "방학 독서교실·체험강좌·캠프처럼 모집 기간과 신청 링크가 있는 것들이에요. "
    "응답은 신청 마감·선착순 여부·신청 링크·요금을 안내합니다. "
    "부모가 \"방학 때 보낼 프로그램\", \"신청할 수 있는 강좌\", \"모집하는 수업\"처럼 "
    "신청·모집·등록을 말할 때 쓰세요. sido나 sigungu 중 하나는 필요합니다. "
    "신청 없이 그냥 방문하는 상시 장소(도서관·미술관 등)를 찾을 때는 이 툴이 아니라 "
    "find_drop_in_places를 쓰세요(이 툴은 운영시간이 아니라 신청 마감을 다룹니다). "
    "특정 시설 이름을 콕 집어 물으면 find_place_programs를 쓰세요. "
    "(EN) Finds kids' programs that require advance registration (camps, classes, workshops) "
    ": deadlines, first-come status, application links, fees. Requires sido or sigungu. "
    "For walk-in venues use find_drop_in_places; "
    "for one specific named venue use find_place_programs."
)


@mcp.tool(annotations=READONLY, description=DESC_SIGN_UP)
async def find_sign_up_programs(
    sido: Annotated[Optional[Sido], F_SIDO] = None,
    sigungu: Annotated[Optional[str], F_SIGUNGU] = None,
    ages: Annotated[Optional[List[int]], F_AGES] = None,
    free_only: Annotated[bool, F_FREE] = False,
    inclusive: Annotated[bool, F_INCLUSIVE] = False,
) -> str:
    return await anyio.to_thread.run_sync(
        partial(_find_sign_up, sido, sigungu, ages, free_only, inclusive))


def _find_sign_up(sido, sigungu, ages, free_only, inclusive):
    reg, ask = _resolve_or_ask(sido, sigungu)
    if ask:
        return ask
    today = _today()
    items = backend.fetch_programs(reg["sido"])
    in_region = [i for i in items if _sigungu_ok(i, reg["sigungus"], reg["sido"])]
    hits = [i for i in in_region
            if i.get("participation_type") == "apply"
            and _close_ok(i, today)
            and _age_ok(i, ages) and _free_ok(i, free_only)]
    hits = _inclusive_filter(hits, inclusive)

    label = _region_label(reg)
    if not hits:
        out = render.empty_notice(label, "신청형 프로그램")
        if any(i.get("participation_type") == "dropin" for i in in_region):
            out += ("\n\n대신 이 지역엔 신청 없이 바로 가볼 수 있는 곳이 있어요. "
                    "find_drop_in_places로 찾아볼까요?")
        return _region_header(reg, sigungu) + out

    # 마감일 확인된 것 먼저(임박순), 미등록은 뒤(NULLS LAST). inclusive 부스팅은 유지.
    if not inclusive:
        hits.sort(key=lambda i: (i.get("apply_close_at") is None,
                                 i.get("apply_close_at") or ""))
    shown = hits[:TOP_N]
    cards = [render.apply_card(reg["sido"], i) for i in shown]
    out = _region_header(reg, sigungu) + f"**{label}**에서 신청할 수 있는 프로그램이에요.\n\n" \
        + "\n\n".join(cards)
    if len(hits) > len(shown):
        out += render.truncation_notice(len(shown), len(hits))
    out += ("\n\n_신청형은 마감일이 등록 안 된 경우가 많아, 확인된 것을 먼저 보여드렸어요. "
            "나머지는 선착순·상시 모집일 수 있어요._")
    return out + DETAIL_HINT


# ── 툴 3: 특정 시설의 프로그램 ────────────────────────────────────────────

import re as _re


def _canon_name(s):
    return _re.sub(r"\s+", "", (s or "").strip()).lower()


# 시설 '종류' 단어: place_name으로 오면 지역 검색으로 유도(전국 도서관 후보 난사 방지)
GENERIC_PLACE_WORDS = {
    "도서관", "미술관", "박물관", "과학관", "문화센터", "문화회관", "문화의집",
    "키즈카페", "놀이터", "공원", "체육관", "수영장", "복지관", "가족센터", "어린이집",
}


def _match_venues(place_name, by_sido):
    """전국 venue_name 매칭 → [((sido_key, venue_name), [카드...])].
    정확일치 우선, 부분포함 보조. 그룹키에 venue_id 포함(동명 별개 시설 병합 방지)."""
    q = _canon_name(place_name)
    if not q:
        return []
    exact, partial_ = {}, {}
    for sido_key, items in by_sido.items():
        for i in items:
            vn = i.get("venue_name")
            if not vn:
                continue
            c = _canon_name(vn)
            bucket = exact if c == q else (partial_ if q in c else None)
            if bucket is not None:
                key = (sido_key, vn, i.get("venue_id") or f"p{i.get('id')}")
                bucket.setdefault(key, []).append(i)
    chosen = exact or partial_
    return [((sk, vn), grp) for (sk, vn, _vid), grp in chosen.items()]


DESC_PLACE = (
    "특정 시설·장소에서 지금 하는 아이 프로그램과 행사를 한눈에 보여줍니다. "
    "\"북부도서관 뭐 해?\", \"국립대구과학관 이번에 뭐 있어?\", \"OO문화센터 프로그램\"처럼 "
    "시설 이름을 콕 집어 물을 때 쓰세요. 그 시설의 상시 전시·행사와 신청형 강좌를 "
    "가리지 않고 함께 보여주고, 각각 방문형인지 신청형인지 표시합니다. "
    "지역을 몰라도 됩니다: 시설 이름만으로 전국에서 찾아 어느 지역 시설인지 확인해 드립니다. "
    "place_name은 '북부도서관' 같은 고유 이름이어야 합니다: '도서관', '미술관'처럼 "
    "시설 종류만 말하면 이 툴이 아니라 지역 검색 툴(find_drop_in_places 또는 "
    "find_sign_up_programs)을 쓰세요. \"우리 동네 갈 데\"처럼 지역에서 찾을 때도 마찬가지예요. "
    "(EN) Lists current kids' programs and events at one specific named venue, registered in "
    "Dongtori. Region is optional: searches nationwide by venue name alone and confirms the "
    "location. place_name must be a proper venue name, not a facility type like 'library'. "
    "For searching by area or by type, use find_drop_in_places / find_sign_up_programs."
)


@mcp.tool(annotations=READONLY, description=DESC_PLACE)
async def find_place_programs(
    place_name: Annotated[str, F_PLACE],
    sido: Annotated[Optional[Sido], F_PLACE_SIDO] = None,
    sigungu: Annotated[Optional[str], F_PLACE_SIGUNGU] = None,
) -> str:
    return await anyio.to_thread.run_sync(
        partial(_find_place, place_name, sido, sigungu))


def _find_place(place_name, sido, sigungu):
    # 방어선: 시설 '종류' 단어 단독 → 지역 검색 유도(전국 동종시설 후보 난사 방지)
    if _canon_name(place_name) in {_canon_name(w) for w in GENERIC_PLACE_WORDS}:
        return (f"'{place_name}'은 시설 종류라서 이 도구로는 못 찾아요. "
                f"지역을 알려주시면 그 동네의 {place_name} 프로그램을 찾아드릴게요 "
                f"(find_drop_in_places 또는 find_sign_up_programs, sigungu에 동네 입력).")

    # 지역 힌트 있으면 그 시도만, 없으면 전국 스캔(캐시 위라 가벼움).
    if sido or sigungu:
        reg, _ask = _resolve_or_ask(sido, sigungu)
        by_sido = {reg["sido"]: backend.fetch_programs(reg["sido"])} if reg \
            else backend.fetch_all_programs()
    else:
        by_sido = backend.fetch_all_programs()

    matches = _match_venues(place_name, by_sido)
    if not matches:
        return (f"'{place_name}'라는 시설을 동토리에서 아직 찾지 못했어요. "
                f"이름을 조금 다르게 알려주시거나(예: 정식 명칭), "
                f"지역으로 찾아볼까요? (find_drop_in_places / find_sign_up_programs)")

    if len(matches) > 1:
        cands = "\n".join(
            f"- {render.display_region(sk, grp[0].get('sigungu'))}의 **{vn}** ({len(grp)}건)"
            for (sk, vn), grp in matches[:6])
        return (f"'{place_name}' 이름의 시설이 여러 곳 있어요:\n{cands}\n\n"
                f"어느 곳인지 알려주시면 프로그램을 보여드릴게요.")

    (sido_key, vn), grp = matches[0]
    loc = render.display_region(sido_key, grp[0].get("sigungu"))
    today = _today()
    live = [i for i in grp if (
        (i.get("participation_type") == "apply" and _close_ok(i, today))
        or (i.get("participation_type") == "dropin" and _start_ok(i, today)))]
    live.sort(key=lambda i: (i.get("participation_type") != "dropin",
                             i.get("apply_close_at") is None,
                             i.get("apply_close_at") or ""))

    head = f"**{loc}의 {vn}**을 찾았어요"
    if not (sido or sigungu):
        head += ". 여기가 맞나요? (다른 지역 시설이면 지역을 알려주세요)"
    if not live:
        return head + f"\n\n지금 진행·모집 중인 프로그램은 등록돼 있지 않아요."

    # 같은 제목의 회차별 행(예: 주간 반복 강좌) 접기: 참여유형까지 같을 때만 병합
    dedup, seen = [], {}
    for i in live:
        k = ((i.get("title") or "").strip(), i.get("participation_type"))
        if k in seen:
            seen[k] += 1
        else:
            seen[k] = 1
            dedup.append(i)

    shown = dedup[:TOP_N]
    lines = [head, ""]
    for i in shown:
        tag = "바로 방문" if i.get("participation_type") == "dropin" else "신청 필요"
        bits = [f"**{i.get('title')}** (id {i.get('id')}) · {tag}"]
        if i.get("participation_type") == "apply":
            bits.append(render.fmt_deadline(i))
        if i.get("is_free") is True:
            bits.append("무료")
        n = seen[((i.get("title") or "").strip(), i.get("participation_type"))]
        if n > 1:
            bits.append(f"외 {n - 1}회차 더")
        lines.append("- " + " · ".join(bits))
    out = "\n".join(lines)
    if len(dedup) > len(shown):
        out += render.truncation_notice(len(shown), len(dedup), hint="조건을 알려주시면")
    return out + DETAIL_HINT


# ── 툴 4: 상세 ────────────────────────────────────────────────────────────

DESC_DETAIL = (
    "앞서 찾은 장소·프로그램 하나의 자세한 정보를 가져옵니다: "
    "정확한 일정·대상 연령·모집 정원·신청 방법·요금·운영시간·문의처, 그리고 현재 휴관 여부. "
    "program_id는 앞 검색 결과 각 항목에 표시된 id 값입니다. "
    "부모가 \"그거 마감 언제야?\", \"어떻게 신청해?\", \"거기 전화번호 뭐야?\"처럼 "
    "특정 결과를 파고들 때 쓰세요. "
    "목록을 처음 찾을 때는 이 툴이 아니라 find_* 툴을 쓰고, 이 툴은 그 결과의 id가 있을 때만 쓰세요. "
    "(EN) Fetches full details for one program by its integer id from previous search results "
    ": schedule, target age, capacity, how to apply, fees, contact, closure status. "
    "Use the find_* tools first to obtain ids."
)


@mcp.tool(annotations=READONLY, description=DESC_DETAIL)
async def get_program_detail(
    program_id: Annotated[int, F_PROGRAM_ID],
) -> str:
    return await anyio.to_thread.run_sync(partial(_get_detail, program_id))


def _get_detail(program_id):
    try:
        d = backend.fetch_detail(program_id)
    except Exception:
        return "해당 프로그램을 찾지 못했어요. 목록을 다시 검색해 볼까요?"
    if not d or not d.get("id"):
        return "해당 프로그램을 찾지 못했어요. 목록을 다시 검색해 볼까요?"
    return render.detail_md(d)


if __name__ == "__main__":
    mcp.run(transport=os.environ.get("MCP_TRANSPORT", "streamable-http"))
