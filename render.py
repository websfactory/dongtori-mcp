#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""동토리 MCP 어댑터 — 응답 마크다운 조립.

★단정 금지 9개 준수:
  "주말" 단어 생성 금지 / "지금 열려 있어요" 단정 금지 / 편의시설 "없음" 단정 금지 /
  admission_fee.note 원문 인용 금지 / 기준일 병기 / "오늘 휴관"은 서버 closed_today만 /
  transport 요약·인용 금지(상세 전용·clamp) / 공고문 LLM 추출 금지 / 확인처=전화·홈페이지.
★파생 라벨(hours_label·closed_label·venue_closed_now·closed_today)은 서버가 조립 완료
  → 여기서는 얹기만 한다. raw 재조립 금지."""

from regions import short_sido

VENUE_TYPE_KO = {
    "library": "도서관", "art": "미술관·전시", "museum": "박물관",
    "culture": "문화시설", "family_center": "가족센터", "welfare_center": "복지관",
    "public_facility": "공공시설", "gu_office": "구청", "gu_venue": "구청 운영시설",
    "childcare_center": "육아지원시설", "disability": "발달·장애 배려시설",
}
PROGRAM_TYPE_KO = {
    "course": "강좌", "experience": "체험", "event": "행사",
    "facility": "시설 이용", "exhibition": "전시",
}
DEADLINE_KIND_KO = {   # apply_close_at 없을 때 보강 신호(Phase E §3)
    "first_come": "선착순·조기 마감될 수 있어요",
    "until_capacity": "정원이 차면 마감돼요",
    "ongoing": "상시 모집",
    "fixed": "정해진 기간에 모집해요",
}


def display_sido(sido_key):
    """내부키 → 사용자 표기. 전남광주통합특별시는 '광주·전남'으로 풀어쓴다."""
    if sido_key == "전남광주통합특별시":
        return "광주·전남"
    return short_sido(sido_key)


def display_region(sido_key, sigungu):
    """카드 위치 표기: '대구 수성구'. sigungu가 'OO 전역'이면 그대로."""
    if not sigungu:
        return display_sido(sido_key)
    if sigungu.endswith("전역"):
        return sigungu
    return f"{display_sido(sido_key)} {sigungu}"


def _ref_month(item):
    """카드 정보 기준 시점 = fetched_at의 YYYY.MM (카드에 reference_date 없음)."""
    f = item.get("fetched_at") or ""
    return f"{f[:4]}.{f[5:7]}" if len(f) >= 7 else None


def fmt_fee(item, free_only=False):
    """is_free 3-state. True만 '무료'(가족 기준). None은 미확인 — 유료 단정 금지."""
    if item.get("is_free") is True:
        return "무료"
    if item.get("is_free") is False:
        return item.get("cost") or "유료 (금액은 확인 필요)"
    return "요금 미확인" + (" (무료가 아닐 수도 있어요)" if free_only else "")


def fmt_deadline(item):
    close = item.get("apply_close_at")
    close = close[:10] if close else close   # datetime 문자열이 와도 날짜만
    kind = DEADLINE_KIND_KO.get(item.get("deadline_kind"))
    if close and item.get("deadline_kind") == "first_come":
        return f"{close}까지 · 선착순 마감"
    if close:
        return f"{close}까지"
    if kind:
        return f"마감일 미등록 · {kind}"
    return "마감일이 등록돼 있지 않아요"


def dropin_venue_card(sido_key, group):
    """dropin 카드 = 장소 중심. group = 같은 venue의 카드 리스트(첫 항목이 대표)."""
    v = group[0]
    closed_now = bool(v.get("venue_closed_now"))
    name = v.get("venue_name") or v.get("title") or "이름 미상"
    head = f"### 🚧 {name} (현재 휴관 중)" if closed_now else f"### {name}"
    lines = [head, f"- 위치: {display_region(sido_key, v.get('sigungu'))}"]
    ref = _ref_month(v)
    if closed_now:
        lines.append(f"- 현재 휴관 중으로 등록돼 있어요"
                     + (f" (정보 기준 {ref})" if ref else "") + ". 방문 전 꼭 확인해 주세요.")
    if v.get("hours_label"):
        lines.append(f"- 운영: {v['hours_label']}" + (f" (정보 기준 {ref})" if ref else ""))
    if v.get("closed_label"):
        cl = v["closed_label"]
        # 서버 라벨이 이미 "휴관: …" 접두를 가진 경우 있음 → "휴관: 휴관:" 중복 방지
        lines.append(f"- {cl}" if cl.startswith("휴관") else f"- 휴관: {cl}")
    if v.get("closed_today"):
        lines.append("- 오늘은 정기 휴관일로 등록돼 있어요.")
    lines.append(f"- 요금: {fmt_fee(v)}")
    cat = VENUE_TYPE_KO.get(v.get("venue_type"))
    if cat:
        lines.append(f"- 분류: {cat}")
    progs = [f"{it.get('title')} (id {it.get('id')})" for it in group[:3] if it.get("title")]
    if progs:
        more = f" 외 {len(group) - 3}건" if len(group) > 3 else ""
        lines.append(f"- 진행 중: {' · '.join(progs)}{more}")
    return "\n".join(lines)


def apply_card(sido_key, item):
    lines = [f"### {item.get('title')} (id {item.get('id')})"]
    venue = item.get("venue_name")
    loc = display_region(sido_key, item.get("sigungu"))
    lines.append(f"- 장소: {venue} ({loc})" if venue else f"- 지역: {loc}")
    lines.append(f"- 마감: {fmt_deadline(item)}")
    lines.append(f"- 요금: {fmt_fee(item)}")
    if item.get("apply_url"):
        lines.append(f"- 신청: {item['apply_url']}")
    if item.get("support_badge"):
        lines.append(f"- 배려: {item['support_badge']} 프로그램이에요")
    return "\n".join(lines)


def _clamp(s, n):
    s = (s or "").strip().replace("\n", " ")
    return s if len(s) <= n else s[: n - 1] + "…"


def _unescape(s):
    """소스 URL에 &amp; 등 HTML 엔티티가 섞여 옴 — 클릭 가능한 원형으로 복원."""
    import html
    return html.unescape(str(s))


def detail_md(d):
    """상세. ★detail만 closed_now 게이트를 통과하므로 휴관 뱃지가 유일한 안전장치."""
    lines = []
    if d.get("venue_closed_now"):
        lines.append("> 🚧 **이 장소는 현재 휴관 중으로 등록돼 있어요.** 방문 전 꼭 확인해 주세요.")
        lines.append("")
    lines.append(f"## {d.get('title')}")
    if d.get("summary"):
        lines.append(_clamp(d["summary"], 200))

    when = []
    if d.get("event_start_date"):
        end = d.get("event_end_date")
        when.append(f"{d['event_start_date']}~{end}" if end and end != d["event_start_date"]
                    else d["event_start_date"])
    et = d.get("event_time")
    if et and et not in when:   # event_time이 날짜범위 문자열과 동일한 데이터 존재 → 중복 방지
        when.append(et)
    if when:
        lines.append(f"**언제** {' · '.join(when)}")

    where = d.get("venue_name") or ""
    if d.get("sigungu"):
        where += f" ({d['sigungu']})"
    if where:
        lines.append(f"**어디서** {where}")

    if d.get("target_raw"):
        lines.append(f"**누구** {_clamp(d['target_raw'], 80)}")

    if d.get("participation_type") == "apply":   # 모집 개념은 신청형에만(dropin 전시에 "상시 모집" 방지)
        recruit = []
        kind = DEADLINE_KIND_KO.get(d.get("deadline_kind"))
        if kind:
            recruit.append(kind)
        if d.get("capacity"):
            recruit.append(f"정원 {d['capacity']}")
        if d.get("apply_close_at"):
            recruit.append(f"{d['apply_close_at'][:10]} 마감")
        if d.get("apply_method"):
            recruit.append(_clamp(d["apply_method"], 60))
        if recruit:
            lines.append(f"**모집** {' · '.join(recruit)}")

    lines.append(f"**요금** {fmt_fee(d)}")
    fee = d.get("admission_fee") or {}
    if isinstance(fee, dict) and fee.get("note"):
        # note 원문 인용 금지(%·줄바꿈 소실) — 존재 사실만 알린다.
        lines.append("_감면 대상이 있을 수 있어요. 방문 전 확인해 보세요._")

    if d.get("hours_label"):
        lines.append(f"**운영시간** {d['hours_label']}")
    if d.get("closed_label"):
        lines.append(f"**휴관** {d['closed_label']}")
    if d.get("apply_url"):
        lines.append(f"**신청** {_unescape(d['apply_url'])}")

    ask = [x for x in (d.get("venue_phone"), d.get("homepage_url")) if x]
    if ask:
        lines.append(f"**문의** {' · '.join(_unescape(x) for x in ask)}")

    # amenities = {'raw':…, 'tags':[…]} — tags만 노출(raw 인용 금지, "없음" 단정 금지)
    am = d.get("amenities") or {}
    tags = am.get("tags") if isinstance(am, dict) else (am if isinstance(am, list) else [])
    if tags:
        lines.append(f"**편의** {' · '.join(str(t) for t in tags[:8])} (등록된 정보 기준이에요)")
    if d.get("transport_info"):
        lines.append(f"**가는 길** {_clamp(d['transport_info'], 150)}")

    lines.append("")
    ref = d.get("reference_date")
    tail = f"기준일 {ref} · " if ref else ""
    lines.append(f"_{tail}신청·방문 전 안내 페이지나 전화로 최신 정보를 꼭 확인해 주세요._")
    return "\n".join(lines)


# ── 공통 고지 문구 (Phase E §3) ───────────────────────────────────────────

def truncation_notice(shown, total, hint="지역을 좁히거나(예: 수성구) 조건을 더 알려주시면"):
    return (f"\n_{total}건 중 {shown}건만 보여드렸어요. "
            f"{hint} 다시 찾아볼게요._")


def empty_notice(region_label, what):
    return (f"아직 '{region_label}'에서 조건에 맞는 {what}을 찾지 못했어요.\n"
            f"- 이웃 지역으로 넓혀볼까요? (예: {region_label.split()[0]} 전체)\n"
            f"- 조건을 풀어볼까요? (무료만 → 전체, 나이 조건 완화)\n\n"
            f"_동토리는 지역마다 수집 깊이가 달라, 아직 얕은 지역이 있어요._")


def wide_fallback_notice(sigungu_input, sido_key):
    return (f"_'{sigungu_input}' 동네를 정확히 찾지 못해 "
            f"{display_sido(sido_key)} 전체에서 찾았어요._\n")
