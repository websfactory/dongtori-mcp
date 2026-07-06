# 동토리 MCP 서버

우리 동네 아이 프로그램 큐레이션 MCP — 도서관·문화센터 등 전국 공공기관의 어린이 프로그램을
부모의 자연어 질문("주말에 5살이랑 갈 만한 데 있어?")에 맞춰 찾아준다.

카카오 **AGENTIC PLAYER 10** 공모전 출품작. 데이터는 [동토리](https://dongtori-api.datachat.kr)
공개 API(전국 17개 시도, 매일 갱신)를 사용한다.

## 툴 4개

| 툴 | 역할 |
|---|---|
| `find_drop_in_places` | 신청 없이 방문하는 상시 개방 장소 (키즈카페·도서관 상시 프로그램 등) |
| `find_sign_up_programs` | 신청·접수가 필요한 프로그램 (마감일 중심) |
| `find_place_programs` | 특정 시설 이름으로 그곳의 프로그램 전부 (전국 검색, 동명 시설은 되묻기) |
| `get_program_detail` | 프로그램 1건 상세 |

특징: 시도 17값 enum + 시군구 자유입력 서버 정규화(접두 매칭, 랜드마크 별칭),
무료 필터(요금 미상은 "요금 미확인" 표시), 발달장애 배려 시설 부스팅.

## 실행

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python server.py                 # Streamable HTTP, :8080 /mcp
MCP_TRANSPORT=stdio .venv/bin/python server.py   # stdio
```

환경변수: `DONGTORI_API_BASE` (기본 `https://dongtori-api.datachat.kr`), `PORT` (기본 8080).

## 배포

`Dockerfile` 포함 (linux/amd64). PlayMCP in KC의 Git 소스 빌드로 배포한다.
