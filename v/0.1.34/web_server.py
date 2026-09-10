import asyncio
import json
import logging
import logging.handlers
import os
import re
import sys
import threading
import html as _html
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.gzip import GZipMiddleware
from pydantic import BaseModel

sys.path.insert(0, os.path.dirname(__file__))
from paths import DATA_DIR, data_path, resource_path, LOCAL_MODE

from dotenv import load_dotenv, dotenv_values
# 루트 공유 .env(공통 시크릿)를 먼저, 그다음 제품 .env(우선).
# 공개 노출 앱이라 루트에서는 이 앱이 실제 쓰는 키만 골라 올린다(2026-09-06).
_ROOT_ENV = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..', '..', '.env'))
_SHARED_KEYS = ("GOOGLE_CLIENT_ID", "GOOGLE_CLIENT_SECRET")
_root_vals = dotenv_values(_ROOT_ENV) if os.path.exists(_ROOT_ENV) else {}
for _k in _SHARED_KEYS:
    if _root_vals.get(_k) and _k not in os.environ:
        os.environ[_k] = _root_vals[_k]
load_dotenv(os.path.join(os.path.dirname(__file__), '.env'), override=True)
load_dotenv(data_path('.env'), override=True)   # 배포본은 여기에만 계정을 둔다

from korail import (
    _make_korail, set_default_account, layout_cars, layout_seats, _build_candidates, _reserve, align_cand_cars, get_call_log, group_id_by_clsf, train_updown, _reserve_standing, pay_with_toss, pay_with_card, card_check, simple_pay_keys, list_ncards, my_tickets, ticket_detail, history_presets, seat_groups, train_group_id, cancel_reservation, list_trains_preview, prefs_are_strict, GATE, day_calendar, trains_from_timetable, open_time_exceptions, is_not_open_yet, list_events, mark_events_seen,
    hhmm_to_hhmmss, hhmmss_to_min, compute_delay,
    is_valid_yyMMdd, yyMMdd_to_yyyymmdd, is_holiday_block, HOLIDAY_OPEN_AT, reservation_to_dict, list_trains, list_trains_day,
    search_pages, list_cars, seat_map, pick_seats, seats_label, reservation_seats, my_info,
    ReserveOption, TrainType, SoldOutError, NeedToLoginError,
)

# 자동 결제(nodriver, KTX 전용). 미설치/로드실패해도 서버는 부팅.
try:
    from pay import pay_reservation, PayConfig
    _PAY_AVAILABLE = True
except Exception as _pay_err:
    _PAY_AVAILABLE = False
    logging.getLogger("korail.web").warning(f"pay 모듈 로드 실패(자동결제 비활성): {_pay_err}")

# ── 텔레그램 알림 ──────────────────────────────────────────────
_TG_TOKEN   = os.environ.get("KORAIL_BOT_TOKEN", "")
_TG_USER_ID = os.environ.get("ALLOWED_USER_ID", "")


async def _notify(text: str):
    if not _TG_TOKEN or not _TG_USER_ID:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            await c.post(
                f"https://api.telegram.org/bot{_TG_TOKEN}/sendMessage",
                json={"chat_id": _TG_USER_ID, "text": text},
            )
    except Exception:
        pass


# ── 자동 결제 (KTX, nodriver) ──────────────────────────────────
def _env_bool(key: str, default: bool = False) -> bool:
    return os.environ.get(key, "1" if default else "0").strip() not in ("", "0", "false", "False")


# 기본 ON: PIN이 있으면 자동결제. KORAIL_AUTO_PAY=0 으로 끌 수 있음.
_AUTO_PAY = os.environ.get("KORAIL_AUTO_PAY", "1").strip() != "0"


def _pay_cfg_from_params(p: Dict[str, Any]):
    """결제 설정을 만든다(자동결제 비활성/미설정이면 None)."""
    if not (_PAY_AVAILABLE and p.get("auto_pay", _AUTO_PAY)):
        return None
    mid = (p.get("member_no") or os.environ.get("KORAIL_ID", "")).strip()
    pw = (p.get("password") or os.environ.get("KORAIL_PW", "")).strip()
    pin = (p.get("pay_pin") or os.environ.get("KORAIL_PAY_PIN", "")).strip()
    if not (mid and pw and pin):
        return None
    return PayConfig(
        member_no=mid, password=pw, pin=pin,
        cash_receipt=p.get("cash_receipt", _env_bool("KORAIL_CASH_RECEIPT")),
        receipt_type=p.get("receipt_type", os.environ.get("KORAIL_RECEIPT_TYPE", "personal")),
        phone=p.get("phone", os.environ.get("KORAIL_RECEIPT_PHONE", "")),
        smart_ticket=p.get("smart_ticket", _env_bool("KORAIL_SMART_TICKET")),
        use_mileage=p.get("use_mileage", False),
        headless=_env_bool("KORAIL_PAY_HEADLESS"),  # 기본 False(보이게)
    )


def _set_pay(state, pay_state: str, message: str):
    state.pay_state = pay_state
    state.pay_message = message


async def _autopay_toss(state, p: Dict[str, Any], korail, rsv):
    """토스페이 즉시결제 — 예약에 쓴 korail2 세션 그대로 사용(nodriver/재로그인 X)."""
    raw = getattr(rsv, "_reserve_raw", None) if rsv is not None else None
    if korail is None or not raw:
        state.status_text = "토스결제 실패(데이터 없음)"
        _set_pay(state, "error", "토스결제: 예약 데이터 없음 — 코레일 앱에서 직접 결제하세요")
        await _notify("⚠️ 토스결제: 예약 데이터 없음. 코레일 앱에서 직접 결제하세요.")
        return
    # ⚠️ 토스는 결제호출 자체가 즉시 실결제 → dry-run이면 호출하지 않는다.
    if p.get("pay_dryrun", _env_bool("KORAIL_PAY_DRYRUN")):
        state.status_text = "토스결제 DRY(호출 안 함)"
        _set_pay(state, "dry", "DRY 모드 — 토스결제 호출 안 함(미결제)")
        logger.info("[토스결제] DRY — 호출 생략")
        await _notify("※ (DRY) 토스결제 생략. 코레일 앱에서 결제하세요.")
        return
    loop = asyncio.get_event_loop()
    try:
        state.status_text = "토스 자동결제 중..."
        _set_pay(state, "paying", "토스페이 자동 결제 중…")
        logger.info("[토스결제] 시작")
        res = await _ctx_run(loop, _executor, pay_with_toss, korail, raw)
        state.status_text = "결제 완료(토스)"
        _set_pay(state, "paid", f"토스페이 결제 완료 — {res.get('amount')}원 (PNR {res.get('pnr')})")
        await _notify(f"💳 토스페이 자동결제 완료!\n금액 {res.get('amount')}원 (PNR {res.get('pnr')})")
    except Exception as e:
        state.status_text = "토스결제 오류(수동 필요)"
        _set_pay(state, "error", f"토스결제 오류: {e}")
        logger.error(f"[토스결제] 오류: {e!r}")
        await _notify(f"⚠️ 토스결제 오류: {e}\n※ 코레일 앱에서 직접 결제하세요.")


async def _autopay_card(state, p: Dict[str, Any], korail, rsv):
    """신용카드 즉시결제 — 토스와 같은 경로(예약에 쓴 세션 그대로). 카드 정보는 기기에서 매번 넘어온다."""
    raw = getattr(rsv, "_reserve_raw", None) if rsv is not None else None
    if korail is None or not raw:
        state.status_text = "카드결제 실패(데이터 없음)"
        _set_pay(state, "error", "카드결제: 예약 데이터 없음 — 코레일 앱에서 직접 결제하세요")
        await _notify("⚠️ 카드결제: 예약 데이터 없음. 코레일 앱에서 직접 결제하세요.")
        return
    card = p.get("card") or {}
    bad = card_check(card)
    if bad:
        state.status_text = "카드결제 실패(정보 미비)"
        _set_pay(state, "error", f"카드 정보 오류: {bad}")
        await _notify(f"⚠️ 카드 정보 오류: {bad}")
        return
    # ⚠️ 카드결제도 호출 즉시 실결제 → dry-run이면 호출하지 않는다.
    if p.get("pay_dryrun", _env_bool("KORAIL_PAY_DRYRUN")):
        state.status_text = "카드결제 DRY(호출 안 함)"
        _set_pay(state, "dry", "DRY 모드 — 카드결제 호출 안 함(미결제)")
        logger.info("[카드결제] DRY — 호출 생략")
        await _notify("※ (DRY) 카드결제 생략. 코레일 앱에서 결제하세요.")
        return
    loop = asyncio.get_event_loop()
    try:
        state.status_text = "카드 자동결제 중..."
        _set_pay(state, "paying", "신용카드 자동 결제 중…")
        logger.info("[카드결제] 시작")
        res = await _ctx_run(loop, _executor, pay_with_card, korail, raw, card)
        state.status_text = "결제 완료(카드)"
        _set_pay(state, "paid", f"신용카드 결제 완료 — {res.get('amount')}원 (PNR {res.get('pnr')})")
        await _notify(f"\U0001F4B3 신용카드 자동결제 완료!\n금액 {res.get('amount')}원 (PNR {res.get('pnr')})")
    except Exception as e:
        state.status_text = "카드결제 오류(수동 필요)"
        _set_pay(state, "error", f"카드결제 오류: {e}")
        logger.error(f"[카드결제] 오류: {e!r}")
        await _notify(f"⚠️ 카드결제 오류: {e}\n※ 코레일 앱에서 직접 결제하세요.")


async def _maybe_autopay(state, p: Dict[str, Any], train_no, korail=None, rsv=None):
    """예약 성공 직후 자동 결제. 실패해도 예외를 위로 던지지 않는다(예약은 이미 성공)."""
    if not p.get("auto_pay", _AUTO_PAY):
        _set_pay(state, "none", "자동결제 꺼짐 — 구입기한 내 코레일 앱에서 결제하세요")
        await _notify("※ 코레일 앱에서 결제하세요. (자동결제 비활성)")
        return
    method = p.get("pay_method", "cash")
    if method == "toss":                       # 토스페이 — 세션 직접결제
        await _autopay_toss(state, p, korail, rsv)
        return
    if method == "card":                       # 신용카드 — 세션 직접결제
        await _autopay_card(state, p, korail, rsv)
        return
    # 간편현금결제(기본) — nodriver
    cfg = _pay_cfg_from_params(p)
    if cfg is None:
        _set_pay(state, "none", "결제 정보 미설정 — 구입기한 내 코레일 앱에서 결제하세요")
        await _notify("※ 코레일 앱에서 결제하세요. (자동결제 미설정/비활성)")
        return
    # 결제 직전까지만(실제 돈 안 나감) — UI 토글 우선, 없으면 .env
    dry = p.get("pay_dryrun", _env_bool("KORAIL_PAY_DRYRUN"))
    loop = asyncio.get_event_loop()
    lock = _pay_lock_for(cfg.member_no)   # 계정별 락(다른 계정은 병렬)
    if lock.locked():
        state.status_text = "결제 대기 중 (동일계정 순번)"
        _set_pay(state, "waiting", "같은 계정 결제가 진행 중 — 순번 대기")
        logger.info("[자동결제] 같은 계정 결제 진행 중 — 순번 대기")
    try:
        # 같은 계정만 순차(프로필 충돌 방지), 다른 계정은 전용 풀에서 병렬.
        async with lock:
            state.status_text = "자동 결제 중..." + (" (DRY)" if dry else "")
            _set_pay(state, "paying", "간편현금결제 자동 결제 중…" + (" (DRY)" if dry else ""))
            logger.info(f"[자동결제] 시작 (train_no={train_no}, dry_run={dry})")
            res = await _ctx_run(loop, _pay_executor,
                                 lambda: pay_reservation(cfg, train_no=train_no,
                                                         dep_date=p.get("date_yyyymmdd"), dry_run=dry))
    except Exception as e:
        state.status_text = "결제 오류(수동 필요)"
        _set_pay(state, "error", f"자동결제 오류: {e}")
        logger.error(f"[자동결제] 오류: {e!r}")
        await _notify(f"⚠️ 자동결제 오류: {e}\n※ 코레일 앱에서 직접 결제하세요.")
        return
    if res.get("ok") and dry:
        state.status_text = "결제 DRY 검증 완료"
        _set_pay(state, "dry", "DRY 검증만 완료 — 실제 결제 안 됨(미결제)")
        logger.info("[자동결제] DRY-RUN 파이프라인 검증 완료(실제 결제 안 함)")
        await _notify("🧪 자동결제 DRY-RUN 완료(결제 직전까지 OK). 실제 결제하려면 KORAIL_PAY_DRYRUN=0.\n※ 이 예약은 아직 미결제입니다.")
    elif res.get("ok"):
        state.status_text = "결제 완료!"
        _set_pay(state, "paid", "간편현금결제 완료 — 발권됐습니다")
        logger.info("[자동결제] 완료 ✅")
        await _notify("💳 자동 결제 완료! 발권됐습니다.")
    else:
        state.status_text = "결제 실패(수동 필요)"
        _set_pay(state, "failed", f"자동결제 실패({res.get('stage')}): {res.get('message')}")
        logger.warning(f"[자동결제] 실패: {res}")
        await _notify(f"⚠️ 자동결제 실패({res.get('stage')}): {res.get('message')}\n"
                      "※ 코레일 앱에서 직접 결제하세요.")


# ── 로그 캡처 (SSE 스트리밍용) ─────────────────────────────────
# KTX 감시는 세션별인데 로그 스트림은 루트 로거 전체였음 → 다른 사용자 로그가 섞여 보임.
# 러너 태스크가 CUR_SID를 설정하고, 실행기 호출은 _ctx_run으로 컨텍스트를 복사해 넘겨
# 스레드 안 로그(korail.py)도 같은 sid를 달고 나온다. 스트림은 내 sid + 공용(sid 없음)만.
import contextvars, functools
import korail as _kmod
CUR_SID: contextvars.ContextVar = contextvars.ContextVar("ktx_sid", default=None)
CUR_MODE: contextvars.ContextVar = contextvars.ContextVar("ktx_mode", default=None)

def _ctx_run(loop, executor, fn, *args):
    return loop.run_in_executor(executor, functools.partial(contextvars.copy_context().run, fn, *args))

_log_subs: List[asyncio.Queue] = []
_log_history: deque = deque(maxlen=300)
# 감시별 로그: {"sid:job": deque[line]} — 새로고침해도, 서버가 재시작해도 남는다
_JOB_LOGS: Dict[str, deque] = {}
_JOBLOG_FILE = data_path(".joblogs.json")
_JOBLOG_MAX = 300
_joblog_dirty = False


def _joblog_key(sid, job) -> str:
    return f"{sid or 'default'}:{job}"


# 조회 현황은 결과 탭 카드가 따로 보여주므로 로그에는 남기지 않는다(파일도 그만큼 가벼워진다)
_HEARTBEAT_RE = re.compile(r"(조회 \d+회 · 다음 \d+초|#\d+ 후보 없음 · 다음 \d+초)\s*$")


def _joblog_add(sid, job, line: str) -> None:
    global _joblog_dirty
    if _HEARTBEAT_RE.search(line or ""):
        return
    dq = _JOB_LOGS.setdefault(_joblog_key(sid, job), deque(maxlen=_JOBLOG_MAX))
    dq.append(line)
    _joblog_dirty = True


def _joblog_save() -> None:
    global _joblog_dirty
    if not _joblog_dirty:
        return
    try:
        tmp = _JOBLOG_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({k: list(v) for k, v in _JOB_LOGS.items()}, f, ensure_ascii=False)
        os.replace(tmp, _JOBLOG_FILE)
        _joblog_dirty = False
    except Exception:
        pass


def _joblog_load() -> None:
    try:
        with open(_JOBLOG_FILE, encoding="utf-8") as f:
            for k, v in (json.load(f) or {}).items():
                keep = [x for x in v if not _HEARTBEAT_RE.search(x or "")]
                _JOB_LOGS[k] = deque(keep[-_JOBLOG_MAX:], maxlen=_JOBLOG_MAX)
    except Exception:
        pass


class _QueueHandler(logging.Handler):
    def emit(self, record):
        line = self.format(record)
        m = CUR_MODE.get()
        item = (line, CUR_SID.get(), m or "")
        _log_history.append(item)
        if m:
            _joblog_add(CUR_SID.get(), m, line)
        for q in list(_log_subs):
            try:
                q.put_nowait(item)
            except Exception:
                pass


_qh = _QueueHandler()
_qh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
logging.getLogger().addHandler(_qh)

# 파일 로그: 100MB × 50개 로테이션(최대 ~5GB 하드 천장). 무제한 로그 폭주 방지.
_LOG_FILE = os.environ.get("REZV_LOG_FILE") or data_path("rezv_web.log")
try:
    _fh = logging.handlers.RotatingFileHandler(
        _LOG_FILE, maxBytes=100 * 1024 * 1024, backupCount=50, encoding="utf-8")
    _fh.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logging.getLogger().addHandler(_fh)
except Exception as _e:
    logging.getLogger().warning(f"파일 로그 핸들러 설정 실패: {_e}")

logging.getLogger().setLevel(logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
# nodriver/asyncio가 결제 후 정리하며 남기는 무해한 잡음 숨김
# ("terminated browser ... successfully", "Loop ... that handles pid ... is closed")
logging.getLogger("nodriver").setLevel(logging.WARNING)
logging.getLogger("uc").setLevel(logging.WARNING)
logging.getLogger("asyncio").setLevel(logging.ERROR)

logger = logging.getLogger("korail.web")

# ── 상태 ───────────────────────────────────────────────────────
@dataclass
class WebState:
    running: bool = False
    await_open: bool = False       # 예매 개시를 기다리는 중(오픈런) — '감시'가 아니라 '예약'
    expired: bool = False          # 출발일이 지나 되살리면 안 되는 작업
    saved_trains: list = field(default_factory=list)
    task: Optional[asyncio.Task] = None
    params: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[str] = None
    last_check_at: Optional[str] = None
    next_delay_sec: Optional[int] = None
    attempts: int = 0
    last_candidates: int = 0
    last_error: Optional[str] = None
    last_success: Optional[str] = None
    status_text: str = "대기중"
    # ── UI 카드용 구조화 상태 (2026-09 UX 개편) ──
    phase: str = "idle"                     # idle | scheduled | running | done
    started_ts: Optional[float] = None      # 감시 시작 epoch(경과시간 표시)
    scheduled_at: Optional[str] = None      # 예약 시작 시각(ISO, phase=scheduled)
    coverage: Optional[str] = None          # 조회 커버 범위 "08:12~21:28 (8페이지)"
    holiday_block: bool = False             # 명절 특별수송기간 차단 대기 중
    pay_state: Optional[str] = None         # none|skipped|waiting|paying|paid|dry|failed|error
    pay_message: Optional[str] = None
    result: Optional[dict] = None           # reservation_to_dict()
    req: Optional[dict] = None              # 시작 요청 원본(서버 재시작 후 재개용)


# KTX는 세션별 state(여러 명 동시 사용). 나머지는 단일(결제 없음).
KTX_SESSIONS: Dict[str, WebState] = {}
# UI 요청용(열차 목록·좌석맵·내 티켓 …)과 감시 루프용을 분리한다.
# 하나로 쓰면 감시가 4칸을 다 물고 있는 동안 사용자가 누른 조회가 뒤에서 기다린다.
_executor = ThreadPoolExecutor(max_workers=6, thread_name_prefix="ui")
_job_executor = ThreadPoolExecutor(max_workers=12, thread_name_prefix="job")
# 결제(nodriver/크롬)는 무겁고 오래 걸리므로 전용 풀로 분리(감시 루프 starvation 방지).
_pay_executor = ThreadPoolExecutor(max_workers=3)
RELOGIN_EVERY = 30          # (열차 재조회 등 횟수 기준 작업에 사용)
RELOGIN_AFTER_SEC = 900     # 감시 세션은 15분마다 갱신 — 주기가 짧아도 로그인 횟수는 그대로

# 결제 크롬 프로필은 계정별로 분리돼 있으므로 락도 계정별.
# → 다른 계정은 병렬 결제, 같은 계정만 순차(같은 프로필 충돌 방지).
_PAY_LOCKS: Dict[str, asyncio.Lock] = {}

def _pay_lock_for(member_no) -> asyncio.Lock:
    key = (member_no or os.environ.get("KORAIL_ID", "") or "default").strip()
    lk = _PAY_LOCKS.get(key)
    if lk is None:
        lk = asyncio.Lock(); _PAY_LOCKS[key] = lk
    return lk


MODES = ("urgent", "normal", "special")   # (구버전 탭) 긴급/보통/특수
_JOB_RE = re.compile(r"^[A-Za-z0-9_-]{1,40}$")


def _job_id(mode: str) -> str:
    """작업 식별자. 예매 탭에서 여러 감시를 동시에 돌릴 수 있으므로 임의의 id를 허용한다."""
    mode = (mode or "urgent").strip()
    return mode if _JOB_RE.match(mode) else "urgent"


def _ktx_state(request, mode: str = "urgent") -> WebState:
    sid = getattr(request.state, "sid", None) or "default"
    key = f"{sid}:{_job_id(mode)}"
    st = KTX_SESSIONS.get(key)
    if st is None:
        st = WebState(); KTX_SESSIONS[key] = st
    return st


_JOBS_FILE = data_path(".jobs.json")


def _save_jobs() -> None:
    """작업 목록(요청 본문 + 마지막 상태)을 파일로. 재시작 후 목록·상태가 남고 재개도 된다."""
    try:
        out = {}
        for key, st in KTX_SESSIONS.items():
            pr = st.params or {}
            out[key] = {
                "req": st.req,
                "info": {k: pr.get(k) for k in ("dep", "arr", "date_yyyymmdd", "start_hhmm", "end_hhmm",
                                                "adults", "interval_sec", "label", "mode", "opt_text",
                                                "train_nos", "train_times", "fixed_train_no")},
                "watch_trains": pr.get("_watch_trains") or [],
                "status": {"status_text": st.status_text, "last_error": st.last_error,
                           "attempts": st.attempts, "last_check_at": st.last_check_at,
                           "result": st.result, "phase": st.phase,
                           "was_running": bool(st.running or (st.task and not st.task.done()))},
            }
        tmp = _JOBS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(out, f, ensure_ascii=False)
        os.replace(tmp, _JOBS_FILE)
    except Exception as e:
        logger.debug(f"[작업] 저장 실패: {e}")
    _joblog_save()


def _load_jobs() -> None:
    """부팅 시 복원 — 실행 중이던 작업은 '재시작으로 중단됨'으로 표시하고 재개 버튼을 살려둔다."""
    try:
        with open(_JOBS_FILE, encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return
    for key, rec in (data or {}).items():
        st = WebState()
        st.req = rec.get("req")
        st.params = {k: v for k, v in (rec.get("info") or {}).items() if v is not None}
        sd = rec.get("status") or {}
        st.attempts = sd.get("attempts") or 0
        st.last_check_at = sd.get("last_check_at")
        st.result = sd.get("result")
        st.last_error = sd.get("last_error")
        st.status_text = "서버 재시작으로 중단됨 — 재개 가능" if sd.get("was_running") else (sd.get("status_text") or "종료됨")
        d0 = (st.params or {}).get("date_yyyymmdd") or ""
        if d0 and d0 < datetime.now().strftime("%Y%m%d"):
            st.status_text = "만료됨 — 출발일이 지났습니다"
            st.expired = True
        elif LOCAL_MODE and "재시작" in (st.status_text or ""):
            # 윈도우 단독 실행: PC를 껐다 켜도 마음대로 되살리지 않는다(사용자가 눌러야 시작)
            st.status_text = "PC 종료로 중단됨 — 재개 가능"
        st.saved_trains = rec.get("watch_trains") or []   # 조회를 못 하는 동안에도 목록은 보여준다
        if st.saved_trains:
            st.params["_saved_trains"] = st.saved_trains
        st.phase = "done" if st.result else "idle"
        KTX_SESSIONS[key] = st
    if KTX_SESSIONS:
        was = [k for k, v in KTX_SESSIONS.items() if "재시작" in (v.status_text or "")]
        logger.info(f"이전 감시 {len(KTX_SESSIONS)}개 복원"
                    + (f" · {len(was)}개 재개" if was else ""))
        for key in was:   # 그 작업 로그에도 이유를 남긴다(sid까지 맞춰야 그 카드의 로그에 들어간다)
            sid, _, job = key.rpartition(":")
            tm, ts = CUR_MODE.set(job), CUR_SID.set(sid or None)
            try:
                logger.warning("서버 재시작, 감시 재개")
            finally:
                CUR_MODE.reset(tm); CUR_SID.reset(ts)


def _session_jobs(request) -> List[tuple]:
    """이 세션의 작업들 [(job_id, WebState)] — 결과 탭에서 한 줄씩 보여준다."""
    sid = getattr(request.state, "sid", None) or "default"
    pre = f"{sid}:"
    return sorted(((k[len(pre):], v) for k, v in KTX_SESSIONS.items() if k.startswith(pre)), key=lambda x: x[0])


# ── 공통 헬퍼 ──────────────────────────────────────────────────
def _state_to_dict(state: WebState) -> dict:
    p = state.params
    return {
        "running":         state.running,
        "status_text":     state.status_text,
        "started_at":      state.started_at,
        "last_check_at":   state.last_check_at,
        "attempts":        state.attempts,
        "last_candidates": state.last_candidates,
        "next_delay_sec":  state.next_delay_sec,
        "last_error":      state.last_error,
        "last_success":    state.last_success,
        "phase":           state.phase,
        "started_ts":      state.started_ts,
        "scheduled_at":    state.scheduled_at,
        "coverage":        state.coverage,
        "holiday_block":   state.holiday_block,
        "holiday_open_at": HOLIDAY_OPEN_AT,
        "pay_state":       state.pay_state,
        "pay_message":     state.pay_message,
        "result":          state.result,
        "now":             time.time(),
        "params": {k: p.get(k) for k in ("dep", "arr", "date_yyyymmdd", "start_hhmm", "end_hhmm", "adults", "interval_sec")} if p else None,
    }


def _validate_train_request(dep, arr, date, adults, interval_sec, start_hhmm, end_hhmm):
    if not dep or not arr:
        return "출발역/도착역을 입력하세요"
    if not is_valid_yyMMdd(date):
        return f"날짜 형식 오류: {date}"
    if not (1 <= adults <= 6):
        return "인원은 1~6명"
    if not (3 <= interval_sec <= 600):
        return "주기는 3~600초"
    if hhmmss_to_min(hhmm_to_hhmmss(end_hhmm)) < hhmmss_to_min(hhmm_to_hhmmss(start_hhmm)):
        return "종료 시간이 시작 시간보다 빠릅니다"
    return None


def _build_train_params(date, dep, arr, start_hhmm, end_hhmm, adults, interval_sec) -> Dict[str, Any]:
    sh = start_hhmm.zfill(4)
    eh = end_hhmm.zfill(4)
    return {
        "date_yyMMdd":   date,
        "date_yyyymmdd": yyMMdd_to_yyyymmdd(date),
        "dep":           dep.strip(),
        "arr":           arr.strip(),
        "start_hhmm":    sh,
        "end_hhmm":      eh,
        "start_hhmmss":  hhmm_to_hhmmss(sh),
        "end_hhmmss":    hhmm_to_hhmmss(eh),
        "adults":        adults,
        "interval_sec":  interval_sec,
        "jitter_sec":    3,
    }


# 좌석 지정 예약: pref면 좌석맵을 조회해 선호 조건에 맞는 자리를 고르고, fixed면 지정 좌석으로 시도.
# 지정/선택 좌석으로 실패하면(이미 팔림 등) 설정에 따라 같은 열차 아무 좌석으로 폴백. (rsv, seats) 반환.
def _reserve_with_seats(korail, train, p: Dict[str, Any], seat: str):
    mode = p.get("seat_mode")
    seats = []
    if mode == "cand":
        # 여러 그룹을 함께 감시할 때, 이 열차의 편성(clsf)에 맞는 템플릿을 고른다.
        gid = group_id_by_clsf(getattr(train, "train_type", ""), train_updown(getattr(train, "train_no", "")))
        base_seats = (p.get("cand_templates") or {}).get(gid) or p.get("cand_seats") or []
        if not base_seats:
            logger.info(f"{getattr(train,'train_no','?')}편({gid or '편성미상'}) 선호 좌석 없음 — 다음 열차")
            return None, None
        # 템플릿은 편성 기준 번호라, 그날 이 열차의 실제 번호대(1~8/11~18)로 먼저 옮긴다
        want = align_cand_cars(korail, train, seat, base_seats, p)
        need = int(p["adults"])
        free = []
        for car_no in dict.fromkeys(x["car_no"] for x in want):
            try:
                m = seat_map(korail, train, car_no, seat, need, after_cars=True)
            except Exception as e:
                logger.debug(f"[좌석] 후보 호차 {car_no} 좌석맵 실패({e})")
                continue
            ok = {str(x.get("label", "")).upper(): x["no"] for x in m["seats"] if x["avail"]}
            for rank, x in enumerate(want):   # 등록 순서 = 우선순위. free에 _no를 더하면 want에서
                if x["car_no"] != car_no:      # 되찾을 수 없으므로(값이 달라짐) 순번을 함께 담는다
                    continue
                hit = ok.get(str(x.get("label") or x["seat_no"]).upper()) or (
                    str(x["seat_no"]) if str(x["seat_no"]) in set(ok.values()) else None)
                if hit:
                    free.append({**x, "_no": hit, "_rank": rank})
        free.sort(key=lambda x: x["_rank"])   # 등록 순서 = 우선순위
        if len(free) >= need:
            seats = [{"car_no": x["car_no"], "seat_no": x.get("_no") or x["seat_no"], "label": x.get("label", "")} for x in free[:need]]
            ranks = ", ".join(f"{x['_rank'] + 1}순위" for x in free[:need])
            logger.info(f"후보 {ranks} 확보 → {seats_label(seats)}")
        elif p.get("cand_fallback") == "auto":
            logger.info(f"후보 {len(free)}/{need}석뿐 — 조건 자동 선택")
            mode = "pref"
        else:
            logger.info(f"후보 {len(free)}/{need}석 — 계속 감시")
            return None, None
    if mode == "fixed":
        seats = p.get("fixed_seats") or []
    elif mode == "pref" and not seats:
        prefs = p.get("seat_prefs") or {}
        strict = prefs_are_strict(prefs)
        stats: Dict[str, int] = {}
        try:
            seats = pick_seats(korail, train, seat, int(p["adults"]), prefs, stats)
            if seats:
                logger.info(f"자동 선택 → {seats_label(seats)}")
            elif strict:
                # '창측만/역방향만'처럼 강제 조건이면 아무 자리나 잡지 않고 다음 사이클에 다시 본다
                free = stats.get("free", 0)
                logger.info(f"빈자리 {free}개 — 선호 옵션과 달라 건너뜀"
                            if free else "빈자리 없음 — 계속 감시")
                return None, None
            else:
                logger.info("조건 맞는 자리 없음 — 일반 예약")
        except Exception as e:
            if strict:
                logger.warning(f"좌석맵 실패({e}) — 이번 사이클 건너뜀")
                return None, None
            logger.warning(f"좌석맵 실패({e}) — 일반 예약"); seats = []
    if seats:
        try:
            rsv = _reserve(korail, train, p["adults"], p["option"], seat, False, seats=seats, ncard=p.get("ncard") or None)
            logger.info(f"지정 좌석 예약 성공 · {seats_label(seats)}")
            return rsv, seats
        except Exception as e:
            if mode == "fixed" and not p.get("fixed_fallback", True):
                raise
            logger.warning(f"지정 좌석 실패({e}) — 아무 좌석으로 폴백")
    return _reserve(korail, train, p["adults"], p["option"], seat, False, ncard=p.get("ncard") or None), None


# 특수-지정좌석 감시: 지정 좌석들의 현재 판매가능 여부를 좌석맵으로 확인해, 인원수만큼 비어 있으면 그 좌석으로 예약.
# 아직 부족하면 (None, None) — 호출부가 이번 사이클을 건너뛰고 계속 감시한다.
def _reserve_watch_seats(korail, train, p: Dict[str, Any], seat: str, attempt: int):
    want = p.get("watch_seats") or []
    need = int(p["adults"])
    free = []
    # 웹 좌석맵은 호차 목록 선행 조회 없이도 동작한다(감시 대상인 매진 열차는 호차 목록이
    # ERI411321 '잔여석이 없습니다'로 실패하므로 미리 부르면 낭비이자 실패 원인). 모바일 폴백이
    # 필요할 때만 after_cars=False로 한 번 더 시도해 선행 조회를 태운다.
    def _map(car_no):
        try:
            return seat_map(korail, train, car_no, seat, need, after_cars=True)
        except Exception as e:
            logger.debug(f"웹 좌석맵 실패({e}) — 재시도")
            return seat_map(korail, train, car_no, seat, need, after_cars=False)
    for car_no in dict.fromkeys(x["car_no"] for x in want):
        m = _map(car_no)
        ok = {x["no"] for x in m["seats"] if x["avail"]}
        free.extend(x for x in want if x["car_no"] == car_no and str(x["seat_no"]) in ok)
    if len(free) < need:
        if attempt == 1 or attempt % 10 == 0:
            logger.info(f"지정 좌석 {len(free)}/{need}석 비었음 ({', '.join(x.get('label', '') for x in free) or '없음'}) — 계속 감시")
        return None, None
    chosen = free[:need]
    rsv = _reserve(korail, train, p["adults"], p["option"], seat, False, seats=chosen, ncard=p.get("ncard") or None)
    logger.info(f"지정 좌석 확보 → {seats_label(chosen)}")
    return rsv, chosen


# ── KTX 감시 루프 (세션별 state) ───────────────────────────────
async def runner_loop(st: WebState, p: Dict[str, Any], sid: Optional[str] = None, mode: str = "urgent"):
    CUR_SID.set(sid)   # 이 태스크(+_ctx_run 실행기 호출)의 로그를 세션에 태깅
    CUR_MODE.set(mode)
    _kmod.CUR_JOB.set(mode)   # 코레일 호출 로그에 발신 작업 태그
    st.running = True
    st.started_at = datetime.now().strftime("%m/%d %H:%M:%S")
    st.started_ts = time.time()
    st.attempts = 0
    st.last_error = None
    st.last_success = None
    st.result = None; st.pay_state = None; st.pay_message = None
    st.coverage = None; st.holiday_block = False
    st.phase = "running"
    loop = asyncio.get_event_loop()
    mid, pw = p.get("member_no"), p.get("password")   # 웹폼 계정(없으면 .env 폴백)
    try:
        # ── 예약 시작: 지정 시각까지 대기(중단 가능). 로그인은 시작 45초 전에 해서 세션을 데워 둔다.
        start_at = p.get("start_at")
        if start_at:
            st.phase = "scheduled"; st.scheduled_at = start_at
            target = datetime.fromisoformat(start_at)
            st.status_text = f"예약됨 — {target.strftime('%m/%d %H:%M')} 시작"
            logger.info(f"예약 대기 — {target.strftime('%m/%d %H:%M:%S')} 시작 예정")
            wait = (target - datetime.now()).total_seconds() - 45
            if wait > 0:
                await asyncio.sleep(wait)
        st.status_text = "로그인 중..."
        logger.debug("로그인 중" + (f" · {mid[:4]}***" if mid else " · .env 계정"))
        korail = await _ctx_run(loop, _job_executor, lambda: _make_korail(mid, pw))
        p["_login_ts"] = time.time()
        if start_at:
            remain = (datetime.fromisoformat(start_at) - datetime.now()).total_seconds()
            if remain > 0:
                await asyncio.sleep(remain)
            st.phase = "running"; st.scheduled_at = None
            st.started_at = datetime.now().strftime("%m/%d %H:%M:%S"); st.started_ts = time.time()
            if p.get("open_burst"):   # 오픈 직후 10분은 5초 주기로 집중 감시
                p["_burst_until"] = time.time() + 600
                logger.info("집중 감시 — 5초 주기, 10분")
        st.status_text = "감시 중"
    except asyncio.CancelledError:
        st.running = False; st.task = None; st.phase = "idle"; st.scheduled_at = None
        st.status_text = "중단됨"; logger.info("예약 시작 취소됨"); return
    except Exception as e:
        st.running = False; st.task = None; st.phase = "idle"; st.scheduled_at = None
        st.status_text = "로그인 실패"; st.last_error = str(e)
        logger.error(f"로그인 실패 · {e}")
        logger.info("감시 종료 · 로그인 실패")
        _save_jobs(); return
    holiday_hits = 0   # 명절 특별수송기간 차단 연속 횟수(0=차단 아님)
    not_open_hits = 0  # 예매 개시 전(WRD000058) 연속 횟수
    try:
        while True:
            st.last_check_at = datetime.now().strftime("%H:%M:%S")
            st.attempts += 1
            if st.attempts == 1:
                tgt = (", ".join(p["train_nos"]) + "편") if p.get("train_nos") else "시간대 전체"
                # 실제로 코레일에 물어보는 구간을 항상 한 번 남긴다(고른 열차가 없으면 사용자가 정한 범위 그대로)
                if p.get("search_start_hhmmss"):
                    ss = p["search_start_hhmmss"]; se = p["search_end_min"]
                    win = f"{ss[:2]}:{ss[2:4]}~{se // 60:02d}:{se % 60:02d}"
                else:
                    win = f"{p['start_hhmm'][:2]}:{p['start_hhmm'][2:]}~{p['end_hhmm'][:2]}:{p['end_hhmm'][2:]}"
                logger.info(f"감시 시작 · {p['dep']}→{p['arr']} {p['date_yyyymmdd'][4:6]}/{p['date_yyyymmdd'][6:]}"
                            f" · {p['adults']}명 · {p['interval_sec']}초 · {tgt} · 조회 {win}")
                _save_jobs()   # '실행 중' 상태를 즉시 기록(재시작 시 자동 재개 근거)
            if time.time() - p.get("_login_ts", st.started_ts or 0) >= RELOGIN_AFTER_SEC:
                try:
                    korail = await _ctx_run(loop, _job_executor, lambda: _make_korail(mid, pw))
                    p["_login_ts"] = time.time()
                    logger.info(f"세션 갱신 (#{st.attempts})")
                except Exception as e:
                    p["_login_ts"] = time.time()   # 실패해도 바로 또 시도하지 않는다(계정 잠금 방지)
                    logger.warning(f"세션 갱신 실패: {e}")
            try:
                p["_attempt"] = st.attempts
                watch_fixed = bool(p.get("watch_seats")) and bool(p.get("fixed_train_no"))
                if watch_fixed:
                    # 열차는 고정이고 좌석 상태만 바뀌므로 열차 객체를 캐시해 매 사이클 검색(2콜)을 생략한다.
                    # 세션 갱신 주기마다 한 번씩만 다시 찾는다.
                    if p.get("_wtrain") is None or st.attempts % RELOGIN_EVERY == 0:
                        tr = await _ctx_run(loop, _job_executor, _resolve_watch_train, korail, p)
                        if tr is not None:
                            p["_wtrain"] = tr
                        elif p.get("_wtrain") is None:
                            logger.warning(f"{p['fixed_train_no']}편을 찾지 못함")
                    candidates = [(p["_wtrain"], p.get("seat_cls", "1"), False, False)] if p.get("_wtrain") is not None else []
                else:
                    candidates = await _ctx_run(loop, _job_executor, _build_candidates, korail, p)
                    if p.get("seat_mode") == "fixed" and p.get("fixed_train_no"):
                        candidates = [c for c in candidates if getattr(c[0], "train_no", None) == p["fixed_train_no"]]
                if p.get("train_nos"):   # 긴급예매: 고른 열차들만
                    candidates = [c for c in candidates if getattr(c[0], "train_no", None) in p["train_nos"]]
                st.coverage = p.get("_coverage")
                if holiday_hits:
                    # 잔여석 판매가 열려 정상 응답이 돌아옴 → 상태 복구
                    holiday_hits = 0; st.holiday_block = False
                    st.status_text = "감시 중"; st.last_error = None
                    logger.info(f"#{st.attempts} 명절 차단 해제 — 정상 감시")
                st.await_open = False
                st.last_candidates = len(candidates)
                if candidates:
                    logger.info(f"#{st.attempts} 후보 {len(candidates)}개")
                elif st.attempts == 1 or time.time() - p.get("_last_beat", 0) >= 60:
                    p["_last_beat"] = time.time()
                    logger.info(f"#{st.attempts} 후보 없음 · 다음 {st.next_delay_sec or p['interval_sec']}초")
                for (train, seat, is_waiting, is_standing) in candidates:
                    used_seats = None
                    try:
                        for retry in (0, 1):
                            try:
                                if is_standing:
                                    rsv = await _ctx_run(loop, _job_executor, _reserve_standing, korail, train, p["adults"])
                                elif p.get("watch_seats") and not is_waiting:
                                    # 특수-지정좌석 감시: 지정 좌석 중 인원수만큼 비어야만 예약, 아니면 이번 사이클은 건너뜀
                                    rsv, used_seats = await _ctx_run(loop, _job_executor, _reserve_watch_seats, korail, train, p, seat, st.attempts)
                                    if rsv is None:
                                        break
                                elif is_waiting or p.get("seat_mode", "none") == "none":
                                    rsv = await _ctx_run(loop, _job_executor, functools.partial(_reserve, korail, train, p["adults"], p["option"], seat, is_waiting, ncard=(p.get("ncard") or None) if not is_waiting else None))
                                else:
                                    rsv, used_seats = await _ctx_run(loop, _job_executor, _reserve_with_seats, korail, train, p, seat)
                                break
                            except NeedToLoginError:
                                # 세션 사망(P058, 예: 코레일 야간 점검 후). 정기 갱신(30사이클)까지 기다리면
                                # 눈앞의 좌석을 놓치므로(2026-06-17 24분 사례) 즉시 재로그인 후 같은 열차 1회 재시도.
                                if retry:
                                    raise
                                logger.warning("세션 만료(P058) — 재로그인 후 재시도")
                                korail = await _ctx_run(loop, _job_executor, lambda: _make_korail(mid, pw))
                        if rsv is None:
                            continue   # 지정 좌석 감시: 아직 조건 미충족 → 다음 사이클
                        st.last_success = str(rsv)
                        st.running = False; st.task = None
                        st.phase = "done"
                        kind = "waiting" if is_waiting else ("standing" if is_standing else "seat")
                        st.result = reservation_to_dict(rsv, kind, "입석" if is_standing else ("일반실" if seat == "1" else "특실"))
                        if used_seats:
                            st.result["seat_pick"] = seats_label(used_seats)
                        if p.get("ncard") and not is_waiting and not is_standing:
                            st.result["ncard"] = True
                            logger.info(f"N카드 할인 요청 · ****{p['ncard'][-4:]} · {getattr(rsv, 'price', '?')}원")
                        if kind == "seat" and rsv is not None and getattr(rsv, "rsv_id", None):
                            # 실제 배정 좌석(예약 상세 API) — 지정 여부와 무관하게 결과 카드에 표시
                            try:
                                got = await _ctx_run(loop, _job_executor, reservation_seats, korail, rsv.rsv_id)
                                if got.get("seats"):
                                    cars = {x["car_no"] for x in got["seats"]}
                                    st.result["seat_pick"] = (f"{int(list(cars)[0])}호차 " if len(cars) == 1 else "") + "·".join((f"{int(x['car_no'])}호차 " if len(cars) > 1 else "") + x["seat_no"] for x in got["seats"])
                                    logger.info(f"배정 좌석 · {st.result['seat_pick']}")
                            except Exception as e:
                                logger.warning(f"배정 좌석 조회 실패 · {e}")
                        if is_waiting:
                            _set_pay(st, "skipped", "예약대기는 자동결제 미지원 — 좌석 확보 시 코레일 앱에서 결제하세요")
                        elif is_standing:
                            _set_pay(st, "skipped", "입석은 자동결제 미적용 — 코레일 앱에서 결제하세요")
                        if is_waiting:
                            st.status_text = "예약대기 등록!"
                            logger.info(f"예약대기 등록 · {rsv}")
                            await _notify(f"⌛ KTX 예약대기 등록!\n{p['dep']}→{p['arr']} {p['date_yyyymmdd']} {p['start_hhmm']}~{p['end_hhmm']}\n\n{rsv}\n\n※ 예약대기는 자동결제 미지원입니다. 좌석 확보 시 코레일 앱에서 직접 결제하세요.")
                        elif is_standing:
                            st.status_text = "입석 예약!"
                            logger.info(f"입석 예약 · {rsv}")
                            await _notify(f"🚏 KTX 입석 예약!\n{p['dep']}→{p['arr']} {p['date_yyyymmdd']} {p['start_hhmm']}~{p['end_hhmm']}\n\n{rsv}\n\n※ 입석은 자동결제 미적용입니다. 코레일 앱에서 직접 결제하세요.")
                        else:
                            st.status_text = "예약 성공!"
                            logger.info(f"예약 성공 · {rsv}")
                            await _notify(f"🎉 KTX 예약 성공!\n{p['dep']}→{p['arr']} {p['date_yyyymmdd']} {p['start_hhmm']}~{p['end_hhmm']}\n\n{rsv}")
                            await _maybe_autopay(st, p, getattr(train, "train_no", None), korail, rsv)
                        return
                    except SoldOutError:
                        continue
                    except Exception as e:
                        st.last_error = repr(e)
                        # N카드 안내는 코레일이 거절한 경우(KorailError, code 있음)에만. 파이썬 예외(우리 버그)에
                        # 붙이면 사용자가 엉뚱한 곳을 의심한다(2026-09-09: ValueError에 N카드 안내가 붙어 혼선).
                        if p.get("ncard") and not is_waiting and not is_standing and hasattr(e, "code"):
                            logger.warning(f"예약 실패 · {e} — N카드 조건(구간·유효기간·잔여횟수·1명) 확인 필요")
                        else:
                            logger.warning(f"예약 실패 · {e}")
                        continue
            except Exception as e:
                if is_holiday_block(e):
                    # 코레일이 명절 대상일 조회를 아직 막는 중. 잔여석 판매 개시 후 같은 조회가 정상화되므로
                    # 루프는 계속 돌리고, 긴 안내문 대신 한 줄 요약만 상태/로그에 남긴다.
                    holiday_hits += 1; st.holiday_block = True
                    st.status_text = "명절 예매 기간 — 잔여석 판매 개시 후 자동 감시"
                    st.last_error = ("명절 특별수송기간: 코레일이 아직 이 날짜 조회를 막고 있습니다. "
                                     "잔여석 판매 개시(9/11 15:00 예정) 후 자동으로 잡기 시작합니다.")
                    if holiday_hits == 1 or holiday_hits % 30 == 0:
                        logger.warning(f"#{st.attempts} 명절 차단({getattr(e, 'code', '?')}) — 잔여석 개시까지 대기 (누적 {holiday_hits})")
                elif is_not_open_yet(e):
                    # 아직 예매 개시 전. 개시 시각을 계산할 수 있으므로 그때까지는 조회하지 않는다.
                    # (예전에는 10초마다 계속 두드려, 출발 두 달 전 작업이 수만 번을 헛호출했다)
                    not_open_hits += 1
                    st.status_text = "예매 개시 전 — 열리면 자동 예매"
                    st.last_error = "아직 예매가 시작되지 않았습니다(코레일: 승차권 예약발매 준비 중)."
                    st.await_open = True
                    oa = _open_at_for(p.get("date_yyyymmdd") or "")
                    wait = ((oa - datetime.now()).total_seconds() - 180) if oa else 0
                    if wait > 60:
                        if not p.get("_open_wait_logged"):
                            p["_open_wait_logged"] = True
                            logger.info(f"예매 개시 {oa:%m/%d %H:%M} — 3분 전까지 조회를 멈춥니다")
                        st.status_text = f"예매 개시 대기 — {oa:%m/%d %H:%M} 시작"
                        st.next_delay_sec = int(wait)
                        _save_jobs()
                        await asyncio.sleep(wait)
                        continue
                    if not_open_hits == 1 or not_open_hits % 30 == 0:
                        logger.info(f"#{st.attempts} 예매 개시 전 — 대기 (누적 {not_open_hits})")
                else:
                    st.last_error = repr(e); logger.warning(f"조회 실패 · {e}")
            if p.get("one_shot"):
                # 보통예매: 폴링 없이 1회 시도. 여기 왔으면 예약이 안 된 것.
                st.status_text = "좌석 없음 — 다시 시도하세요"
                st.last_error = st.last_error or "선택한 열차에 예약 가능한 좌석이 없습니다 (매진이거나 지정 좌석이 팔렸을 수 있음)"
                logger.info("1회 시도 실패 — 종료")
                return
            burst = p.get("_burst_until") and time.time() < p["_burst_until"]
            delay = 5 if burst else compute_delay(p["interval_sec"], p.get("jitter_sec", 3))
            st.next_delay_sec = delay
            if int(p["interval_sec"]) >= 20 or st.attempts % 3 == 0:
                logger.info(f"조회 {st.attempts}회 · 다음 {delay}초")
            await asyncio.sleep(delay)
    except asyncio.CancelledError:
        st.status_text = "중단됨"
        logger.info(f"감시 중단 · 조회 {st.attempts}회")
        raise
    except Exception as e:
        st.status_text = "오류로 종료"; st.last_error = repr(e)
        logger.error(f"감시 종료 · 오류 {e!r}")
    finally:
        st.running = False; st.task = None
        if st.phase != "done":
            st.phase = "idle"
        if st.status_text in ("", None) or st.running:
            st.status_text = "종료됨"
        if st.phase == "done":
            logger.info(f"감시 종료 · 예약 성공 · 조회 {st.attempts}회")
        elif "중단" not in (st.status_text or ""):
            logger.info(f"감시 종료 · {st.status_text} · 조회 {st.attempts}회 · "
                        f"{int(time.time() - (st.started_ts or time.time()))}초")
        _save_jobs()


# ── FastAPI ────────────────────────────────────────────────────
app = FastAPI()
_joblog_load()   # 감시별 로그 복원(새로고침·재시작에도 유지)
_load_jobs()     # 이전 감시 목록·상태 복원


async def _joblog_flusher() -> None:
    """감시 로그를 30초마다 디스크에 내린다.
    예전에는 작업 상태가 바뀔 때만 저장돼(첫 조회 뒤 몇 시간 동안 저장 없음),
    서버가 강제 종료되면 그 사이 로그가 통째로 사라졌다."""
    while True:
        await asyncio.sleep(30)
        try:
            _joblog_save()
        except Exception as e:
            logger.debug(f"로그 저장 실패: {e}")


@app.on_event("startup")
async def _start_hub_loop():
    if LOCAL_MODE:
        asyncio.create_task(_hub_loop())


@app.on_event("startup")
async def _start_joblog_flusher():
    asyncio.create_task(_joblog_flusher())


@app.on_event("startup")
async def _resume_jobs():
    """서버가 재시작돼 끊긴 감시를 그대로 다시 돌린다(시작 요청을 저장해 뒀다)."""
    if LOCAL_MODE:
        n = sum(1 for v in KTX_SESSIONS.values() if "중단" in (v.status_text or ""))
        if n:
            logger.info(f"이전 감시 {n}건 — 결과 탭에서 직접 재개하세요")
        return
    for key, st in list(KTX_SESSIONS.items()):
        if "재시작" not in (st.status_text or "") or not st.req:
            continue
        try:
            req = TrainStartRequest(**st.req)
            sid, mode = key.split(":", 1)
            sh, eh = req.start_hhmm.zfill(4), req.end_hhmm.zfill(4)
            p = _build_params_from_req(req, mode, sh, eh)
            if getattr(st, "saved_trains", None):
                p["_saved_trains"] = st.saved_trains
            st.params = p
            st.status_text = "재개 중…"
            st.task = asyncio.create_task(runner_loop(st, p, sid, mode))   # mode=작업 ID(로그 태그)
            logger.info(f"자동 재개 · {p['dep']}→{p['arr']} {p['date_yyyymmdd'][4:6]}/{p['date_yyyymmdd'][6:]}")
        except Exception as e:
            st.status_text = f"자동 재개 실패 — 결과 탭에서 재개하세요"
            logger.warning(f"자동 재개 실패 · {e}")
        await asyncio.sleep(2)   # 로그인 몰림 방지
    _save_jobs()


def _adopt_synced_account() -> None:
    """설정 화면에 저장한 KTX 계정을 서버 기본 계정으로 채택(.env 없이도 동작).
    ⚠️ 공용 버킷(소유자 본인)에서만 읽는다. 사용자별 요청은 _acct_for_request 로 각자 계정을 쓴다."""
    try:
        blob = _sync_get(_SYNC_KEY) or {}
        acc = _json.loads(blob.get("kor_ktx_account") or "{}")
        if acc.get("memberNo") and acc.get("password"):
            set_default_account(acc["memberNo"], acc["password"])
    except Exception as e:
        logger.debug(f"[계정] 동기화 계정 채택 실패: {e}")


# ── 인증: Google OAuth + 관리자 승인제 (폴백: HTTP Basic) ───────
import base64 as _b64
import secrets as _secrets
import hmac as _hmac
import hashlib as _hashlib
import json as _json
import sqlite3 as _sqlite3
import urllib.parse as _urlparse
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import Response as _Response, RedirectResponse as _Redirect, HTMLResponse as _HTML

_WEB_USER = os.environ.get("KTX_WEB_USER", "jdent")
_WEB_PASS = os.environ.get("KTX_WEB_PASSWORD", "")
_GG_ID = os.environ.get("GOOGLE_CLIENT_ID", "").strip()
_GG_SECRET = os.environ.get("GOOGLE_CLIENT_SECRET", "").strip()
# 배포본은 전용 "Desktop app" 클라이언트를 쓴다. 웹 클라이언트는 loopback 리다이렉트를
# redirect_uri_mismatch로 거부하며(실측), 게다가 다른 제품과 공유하는 자격증명이라 넣으면 안 된다.
# 구글 공식 문서: installed app 의 client_secret 은 비밀로 취급되지 않는다(그래도 전용 클라이언트여야 한다).
_DESKTOP_ID = os.environ.get("TRAINER_GOOGLE_ID", "").strip()
_DESKTOP_SECRET = os.environ.get("TRAINER_GOOGLE_SECRET", "").strip()
if LOCAL_MODE and _DESKTOP_ID and _DESKTOP_SECRET:
    _GG_ID, _GG_SECRET = _DESKTOP_ID, _DESKTOP_SECRET
_OAUTH_ON = bool(_GG_ID and _GG_SECRET)


def _redirect_uri(request) -> str:
    """로컬은 실제로 열린 loopback 포트를 그대로 쓴다(데스크톱 클라이언트는 포트를 가리지 않는다 — 실측).
    접속에 쓴 호스트를 유지해야 쿠키 오리진이 갈리지 않는다."""
    if not LOCAL_MODE:
        return _BASE_URL + "/auth/callback"
    u = request.base_url
    host, port = (u.hostname or "127.0.0.1"), (u.port or 8767)
    if host not in _LOCAL_HOSTS:
        host = "127.0.0.1"
    return f"http://{host}:{port}/auth/callback"


# PKCE + state — 로컬 콜백은 같은 PC의 아무 페이지나 흉내낼 수 있어 필수
_PKCE: Dict[str, tuple] = {}


def _pkce_new(redirect_uri: str):
    v = _secrets.token_urlsafe(64)
    ch = _b64.urlsafe_b64encode(_hashlib.sha256(v.encode()).digest()).decode().rstrip("=")
    st = _secrets.token_urlsafe(24)
    now = time.time()
    for k, (_, _, ts) in list(_PKCE.items()):
        if now - ts > 600:
            _PKCE.pop(k, None)
    _PKCE[st] = (v, redirect_uri, now)
    return st, ch
_BASE_URL = os.environ.get("KTX_BASE_URL", "https://ktx.jdent28.com").rstrip("/")
_ADMIN_EMAILS = {"dr.bj.toooth@gmail.com", "dr.bj.tooth@gmail.com", "jdent28@gmail.com", "jdent0228@gmail.com"}
_ALLOW_FILE = data_path("approved.json")
_SEC_FILE = data_path(".session_secret")
logger.info(f"인증 모드: {'Google OAuth(승인제)' if _OAUTH_ON else ('Basic' if _WEB_PASS else '없음')}")

try:
    _SESSION_SECRET = open(_SEC_FILE).read().strip() or _secrets.token_hex(32)
except Exception:
    _SESSION_SECRET = _secrets.token_hex(32)
    try: open(_SEC_FILE, "w").write(_SESSION_SECRET)
    except Exception: pass


def _allow_load():
    try:
        with open(_ALLOW_FILE) as f: return _json.load(f)
    except Exception:
        return {"approved": [], "pending": []}

def _allow_save(d):
    try:
        with open(_ALLOW_FILE, "w") as f: _json.dump(d, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"approved.json 저장 실패: {e}")


# ── 허브(중앙 웹) 연결 — 윈도우 exe가 맥미니의 설정 DB를 함께 쓰기 위한 통로 ──
# exe는 브라우저 쿠키가 없으므로 자기 구글 로그인의 refresh_token 으로 ID 토큰을 만들어 Bearer 로 보낸다.
# 서버 쪽 입구는 /api/device/* 하나뿐이고, 거기서 토큰을 구글에 검증한다(비밀값을 exe 에 넣지 않는 방식).
HUB_URL = (os.environ.get("TRAINER_HUB_URL") or "https://ktx.jdent28.com").rstrip("/")
_HUB_TOKEN_FILE = data_path(".gtoken.json")
# ⚠️ 화면 요청은 절대 허브를 기다리면 안 된다. 중앙이 느리거나 끊기면 /api/sync·/api/jobs 가
#    그대로 멈춰 앱 전체가 먹통이 됐다(2026-09-10 v0.1.16 사고 — 호출당 20초).
#    그래서 ① 타임아웃을 짧게 ② 실패하면 한동안 쉬고 ③ 실제 주고받기는 배경 루프가 한다.
_HUB_TIMEOUT = 4.0
_HUB_COOLDOWN = 60.0
_HUB_PERIOD = 10.0   # 재개 지시를 받아 실행하기까지의 지연이 이만큼이다
_hub_id_token = {"tok": "", "exp": 0.0}
_hub_state = {"ok": False, "email": "", "error": "", "at": 0.0, "skip_until": 0.0}
_hub_jobs_cache = {"jobs": [], "at": 0.0}
_hub_logs_cache: Dict[str, list] = {}      # exe가 받아 둔 '웹 작업'의 로그
_hub_runners_cache: Dict[str, list] = {}
_hub_dirty = {"on": False}


def _hub_save_token(email: str, refresh_token: str) -> None:
    try:
        with open(_HUB_TOKEN_FILE, "w", encoding="utf-8") as f:
            _json.dump({"email": email, "refresh_token": refresh_token}, f)
        try:
            os.chmod(_HUB_TOKEN_FILE, 0o600)
        except Exception:
            pass
        _hub_id_token.update(tok="", exp=0.0)
        logger.info("[허브] 이 PC를 계정에 연결했습니다 — 설정이 웹과 함께 저장됩니다")
    except Exception as e:
        logger.warning(f"[허브] 토큰 저장 실패: {e}")


def _hub_refresh_token() -> str:
    try:
        with open(_HUB_TOKEN_FILE, encoding="utf-8") as f:
            return (_json.load(f) or {}).get("refresh_token") or ""
    except Exception:
        return ""


async def _hub_token() -> str:
    """중앙에 보낼 구글 ID 토큰(1시간짜리). 없으면 빈 문자열 — 그때는 로컬 저장만 쓴다."""
    fixed = os.environ.get("TRAINER_HUB_TOKEN", "").strip()
    if fixed:   # 자체 호스팅·점검용으로 토큰을 직접 지정. 보내는 쪽만 바뀌고 서버 검증은 그대로다.
        return fixed
    if _hub_id_token["tok"] and _hub_id_token["exp"] > time.time() + 60:
        return _hub_id_token["tok"]
    rt = _hub_refresh_token()
    if not rt or not (_GG_ID and _GG_SECRET):
        return ""
    try:
        async with httpx.AsyncClient(timeout=_HUB_TIMEOUT) as c:
            r = await c.post("https://oauth2.googleapis.com/token",
                             data={"client_id": _GG_ID, "client_secret": _GG_SECRET,
                                   "refresh_token": rt, "grant_type": "refresh_token"})
        j = r.json()
        idt = j.get("id_token") or ""
        if not idt:
            _hub_state.update(ok=False, error=str(j.get("error") or "토큰 갱신 실패"), at=time.time())
            return ""
        _hub_id_token.update(tok=idt, exp=time.time() + float(j.get("expires_in") or 3600))
        return idt
    except Exception as e:
        _hub_state.update(ok=False, error=f"연결 실패: {e}", at=time.time())
        return ""


def _hub_down(msg: str):
    _hub_state.update(ok=False, error=msg, at=time.time(), skip_until=time.time() + _HUB_COOLDOWN)


async def _hub_call(method: str, path: str, body=None):
    """중앙 API 호출. 연결이 안 되면 None — 호출부는 로컬 저장으로 돌아간다.
    최근에 실패했으면 아예 시도하지 않는다(먹통 방지)."""
    if not LOCAL_MODE or time.time() < _hub_state["skip_until"]:
        return None
    idt = await _hub_token()
    if not idt:
        return None
    try:
        async with httpx.AsyncClient(timeout=_HUB_TIMEOUT) as c:
            r = await c.request(method, HUB_URL + path, json=body,
                                headers={"Authorization": "Bearer " + idt})
        j = r.json()
    except Exception as e:
        _hub_down(f"연결 실패: {e}")
        return None
    if not j.get("ok"):
        _hub_down(str(j.get("error") or "요청 거부"))
        return None if j.get("auth") is False else j
    _hub_state.update(ok=True, error="", at=time.time(), skip_until=0.0)
    return j


def _local_jobs_payload(sid: str) -> list:
    """이 서버에서 도는 감시를 중앙에 올릴 형태로. 비밀은 _clean_req 가 걷어낸다."""
    out, pre = [], f"{sid}:"
    for key, st in KTX_SESSIONS.items():
        if not key.startswith(pre):
            continue
        job = key[len(pre):]
        p = st.params or {}
        out.append({
            "job": job,
            "payload": {"running": bool(st.running), "phase": st.phase, "status_text": st.status_text,
                        "label": p.get("label") or "", "dep": p.get("dep"), "arr": p.get("arr"),
                        "date": p.get("date_yyyymmdd"), "start_hhmm": p.get("start_hhmm"),
                        "end_hhmm": p.get("end_hhmm"), "adults": p.get("adults"),
                        "attempts": st.attempts, "last_check_at": st.last_check_at,
                        "last_error": st.last_error, "result": st.result,
                        "opt_text": p.get("opt_text") or "", "trains": _job_train_rows(p),
                        "started_ts": st.started_ts, "pay_state": getattr(st, "pay_state", None),
                        "auto_pay": bool(p.get("auto_pay")), "pay_method": p.get("pay_method") or "toss",
                        "expired": bool(getattr(st, "expired", False)),
                        "resumable": bool(not st.running and not st.result
                                          and not getattr(st, "expired", False))},
            "req": _clean_req(st.req or {}),   # 결제 비밀은 기기 밖으로 내보내지 않는다(보내지도 않는다)
            "lines": list(_JOB_LOGS.get(_joblog_key(sid, job)) or []),
        })
    return out


async def _hub_tick() -> None:
    """배경에서만 중앙과 주고받는다 — 화면 요청은 언제나 로컬 사본으로 즉시 답한다."""
    if not LOCAL_MODE or not (_hub_refresh_token() or os.environ.get("TRAINER_HUB_TOKEN", "").strip()):
        return
    key = f"u:{_hub_state['email']}" if _hub_state.get("email") else None
    if _hub_dirty["on"]:
        blob, meta = _sync_get_full(key) if key else (None, {})
        if blob is None:
            blob, meta = _sync_get_full(_SYNC_KEY)
        if blob is not None and await _hub_call("POST", "/api/device/sync", {"data": blob, "ts": meta}):
            _hub_dirty["on"] = False
    j = await _hub_call("GET", "/api/device/sync")
    if j and isinstance(j.get("data"), dict):
        _hub_state["email"] = (j.get("key") or "u:").split("u:", 1)[-1]
        _sync_merge(j.get("key") or _SYNC_KEY, j["data"], j.get("ts") or {})
    j = await _hub_call("GET", "/api/device/jobs")
    if j and isinstance(j.get("jobs"), list):
        for x in j["jobs"]:
            x.setdefault("runner", "web"); x.setdefault("runner_label", "웹"); x["runner_online"] = True
        _hub_jobs_cache.update(jobs=j["jobs"], at=time.time())
        for x in j["jobs"][:5]:   # 웹 작업 로그도 미리 받아 둔다(펼칠 때 기다리지 않게)
            lg = await _hub_call("GET", f"/api/device/joblog?job={_urlparse.quote(str(x.get('job') or ''))}")
            if lg and isinstance(lg.get("lines"), list):
                _hub_logs_cache[str(x.get("job"))] = lg["lines"]
    j = await _hub_call("GET", "/api/device/runners")
    if j and isinstance(j.get("runners"), list):
        _hub_runners_cache["runners"] = j["runners"]
    # 이 PC의 감시 목록·로그를 올리고, 나에게 온 지시를 받아 실행한다
    email = _hub_state.get("email") or ""
    if email:
        j = await _hub_call("POST", "/api/device/jobs/push",
                            {"runner": _runner_id(), "label": _runner_label(),
                             "jobs": _local_jobs_payload(f"u:{email}")})
        for c in ((j or {}).get("commands") or []):
            try:
                r = await _run_command_here(f"u:{email}", str(c.get("job") or ""), str(c.get("cmd") or ""), c.get("body") or {})
                logger.info(f"[공용작업] 웹에서 온 '{c.get('cmd')}' 실행 · {c.get('job')} · {r}")
            except Exception as e:
                logger.warning(f"[공용작업] 지시 실행 실패: {e}")


async def _hub_loop() -> None:
    while True:
        try:
            await _hub_tick()
        except Exception as e:
            logger.debug(f"[허브] 주기 동기화 실패: {e}")
        await asyncio.sleep(_HUB_PERIOD)


# ── 계정별 입력값/프리셋 동기화 저장(다른 기기에서도 로드) ──
# 인증된 Google 이메일 단위로 격리 저장. ⚠️ 본인 계정 자격증명(비번/PIN 포함) 평문 저장.
_SYNC_DB = data_path("userdata.db")

# 동기화에서 아예 빼는 키.
#  ① 레거시(구모델) — 코레일 비밀번호가 평문으로 들어 있고 소유권 검사(_member_owner)도 안 거쳐,
#     2026-09-09 공용 상속 사고 때 남의 계정으로 새어 나간 통로다. 신모델(kor_ktx_account)로 이전 완료.
#  ② 기기 캐시 — 열차/좌석맵 캐시는 기기마다 다시 채우면 그만인데 사용자당 250KB를 계속 실어 날랐다.
_SYNC_DROP_EXACT = frozenset((
    "kor_ktx", "kor_ktx_last", "kor_receipt", "kor_ktx_resv",
    "kor_ktx_resv_urgent", "kor_ktx_resv_normal", "kor_ktx_resv_special",
    "kor_ktx_traincache", "kor_ktx_traincache2", "kor_ktx_seatcache",
    "kor_ktx_tab",   # 마지막 본 탭은 기기별 상태다 — 함께 옮기면 새 기기가 엉뚱한 탭으로 열리고
                     # 그 탭이 '내 티켓'이면 접속만 해도 코레일 조회가 한 번 더 난다(2026-09-10)
))
_SYNC_DROP_PREFIX = ("kor_ktx_acct_",)


def _sync_droppable(k: str) -> bool:
    return k in _SYNC_DROP_EXACT or k.startswith(_SYNC_DROP_PREFIX)


def _sync_db():
    con = _sqlite3.connect(_SYNC_DB)
    con.execute("CREATE TABLE IF NOT EXISTS user_sync (email TEXT PRIMARY KEY, blob TEXT, updated TEXT)")
    cols = {r[1] for r in con.execute("PRAGMA table_info(user_sync)")}
    if "meta" not in cols:   # 키별 최종 수정 시각 {키: epoch_ms} — 기기 간 병합의 기준
        con.execute("ALTER TABLE user_sync ADD COLUMN meta TEXT")
        con.commit()
    return con

def _sync_get(email):
    return _sync_get_full(email)[0]

def _sync_get_full(email):
    """(blob, meta) — meta는 {키: epoch_ms}. 없으면 빈 dict."""
    try:
        con = _sync_db()
        row = con.execute("SELECT blob, meta FROM user_sync WHERE email=?", (email,)).fetchone()
        con.close()
        if not row:
            return None, {}
        blob = _json.loads(row[0]) if row[0] else None
        try:
            meta = _json.loads(row[1]) if row[1] else {}
        except Exception:
            meta = {}
        return blob, (meta if isinstance(meta, dict) else {})
    except Exception as e:
        logger.warning(f"sync get 실패: {e}"); return None, {}

def _sync_set(email, blob, meta=None):
    try:
        con = _sync_db()
        con.execute(
            "INSERT INTO user_sync(email,blob,updated,meta) VALUES(?,?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET blob=excluded.blob, updated=excluded.updated, meta=excluded.meta",
            (email, _json.dumps(blob, ensure_ascii=False), datetime.now().isoformat(),
             _json.dumps(meta or {}, ensure_ascii=False)))
        con.commit(); con.close()
        return True
    except Exception as e:
        logger.warning(f"sync set 실패: {e}"); return False


def _sync_purge_legacy() -> None:
    """저장된 blob에서 레거시·캐시 키를 걷어낸다(부팅 시 1회).
    레거시 키에는 남의 코레일 자격증명이 섞여 있을 수 있어 보관 자체가 위험하다."""
    try:
        con = _sync_db()
        rows = con.execute("SELECT email, blob FROM user_sync").fetchall()
        con.close()
    except Exception:
        return
    for email, blob in rows:
        try:
            d = _json.loads(blob or "{}")
        except Exception:
            continue
        drop = [k for k in d if _sync_droppable(k)]
        if not drop:
            continue
        for k in drop:
            d.pop(k, None)
        cur, meta = _sync_get_full(email)
        _sync_set(email, d, {k: v for k, v in (meta or {}).items() if not _sync_droppable(k)})
        secret = [k for k in drop if k.startswith("kor_ktx_acct_") or k in ("kor_ktx", "kor_receipt")]
        logger.warning(f"[동기화] {email} 레거시·캐시 키 {len(drop)}개 정리"
                       + (f" (자격증명 포함 {len(secret)}개: {', '.join(secret)})" if secret else ""))

def _audit_account_isolation() -> None:
    """부팅 시 격리 점검 — 서로 다른 구글 계정이 같은 코레일 회원번호를 들고 있으면 경고.
    2026-09-09 사고(공용 버킷 상속으로 자격증명이 새 계정에 복사됨)의 재발을 즉시 알아채기 위한 장치.
    같은 사람이 구글 계정 두 개로 같은 코레일 계정을 쓰는 정상 상황도 있으므로 차단이 아니라 경고만 한다."""
    try:
        con = _sync_db()
        rows = con.execute("SELECT email, blob FROM user_sync").fetchall()
        con.close()
    except Exception:
        return
    by_member: Dict[str, List[str]] = {}
    for email, blob in rows:
        if not email.startswith("u:"):
            continue                      # __shared__ / __local__ 은 소유자 자신의 것
        try:
            acc = _json.loads((_json.loads(blob or "{}")).get("kor_ktx_account") or "{}")
        except Exception:
            continue
        mno = (acc.get("memberNo") or "").strip()
        if mno:
            by_member.setdefault(mno, []).append(email[2:])
    for mno, emails in by_member.items():
        if len(emails) > 1:
            logger.warning(f"[격리점검] 코레일 회원번호 {mno[:4]}****를 구글 계정 {len(emails)}개가 공유: "
                           f"{', '.join(emails)} — 의도한 것이 아니면 /admin 에서 확인하세요")


_adopt_synced_account()   # 부팅 시 1회 — 설정 화면 계정을 기본값으로
_sync_purge_legacy()   # 레거시 키에 남은 남의 자격증명을 부팅 때 걷어낸다(2026-09-09 사고 잔재)
_audit_account_isolation()


def _is_approved(email):
    return bool(email) and (email in _ADMIN_EMAILS or email in _allow_load().get("approved", []))

def _sign(email):
    return email + "|" + _hmac.new(_SESSION_SECRET.encode(), email.encode(), _hashlib.sha256).hexdigest()

def _session_email(request):
    c = request.cookies.get("ktx_auth", "")
    if "|" not in c: return None
    email, _, sig = c.partition("|")
    good = _hmac.new(_SESSION_SECRET.encode(), email.encode(), _hashlib.sha256).hexdigest()
    return email if _hmac.compare_digest(sig, good) else None


# ── 로컬(윈도우 단독 실행) ────────────────────────────────────
# 관문은 구글 로그인 + 승인 화이트리스트 하나로 통일한다(별도 비밀번호 없음).
_LOCAL_HOSTS = {"127.0.0.1", "localhost", "::1", "[::1]"}
_LOCAL_EXEMPT = ("/api/device/ping", "/auth/login", "/auth/callback", "/auth/logout",
                 "/pending", "/pending/submit", "/style.css", "/korail.js", "/api/version")


class AuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        if LOCAL_MODE:
            # 이 PC 전용. 외부에서 들어올 길은 없지만, 브라우저가 다른 사이트에서
            # 127.0.0.1로 요청을 쏘는 것(CSRF·DNS 리바인딩)은 막아야 한다.
            host = (request.headers.get("host") or "").rsplit(":", 1)[0]
            if host and host not in _LOCAL_HOSTS:
                return _Response(status_code=421)
            if request.method not in ("GET", "HEAD", "OPTIONS"):
                origin = request.headers.get("origin") or request.headers.get("referer") or ""
                if origin and _urlparse.urlparse(origin).hostname not in _LOCAL_HOSTS:
                    return _Response(status_code=403)
            if _OAUTH_ON and path not in _LOCAL_EXEMPT:
                email = _session_email(request)
                if not email:
                    return _Response(status_code=401) if path.startswith("/api/") else _Redirect("/auth/login")
                if not _is_approved(email):
                    return _Response(status_code=403) if path.startswith("/api/") else _Redirect("/pending")
            request.state.sid = "local"      # 감시 작업은 이 PC 것 — 계정이 바뀌어도 키를 유지
            return await call_next(request)
        via_tunnel = bool(request.headers.get("cf-connecting-ip"))   # 터널(외부)만 인증
        exempt = (path.startswith("/auth/") or path.startswith("/pending")
                  or path.startswith("/api/device/")   # exe 전용 — 자체 구글 ID 토큰 검증(쿠키 없음)
                  or path == "/demo" or path in ("/style.css", "/korail.js"))   # 데모(로그인 없이 UI 평가) + 정적 에셋
        if via_tunnel and not exempt:
            if _OAUTH_ON:
                email = _session_email(request)
                if not email:
                    return _Redirect("/auth/login")
                if not _is_approved(email):
                    return _Redirect("/pending")
            elif _WEB_PASS:
                auth = request.headers.get("Authorization", "")
                ok = False
                if auth.startswith("Basic "):
                    try:
                        u, _, p = _b64.b64decode(auth[6:]).decode().partition(":")
                        ok = _secrets.compare_digest(u, _WEB_USER) and _secrets.compare_digest(p, _WEB_PASS)
                    except Exception:
                        ok = False
                if not ok:
                    return _Response(status_code=401, headers={"WWW-Authenticate": 'Basic realm="ktx"'})
        sid = request.cookies.get("ktx_sid")
        new_sid = not sid
        if new_sid:
            sid = _secrets.token_hex(16)
        # 같은 Google 계정은 어느 PC/브라우저에서 접속해도 같은 봇 상태를 공유한다.
        # → 상태 키를 (인증 이메일) 우선, 없으면 브라우저 쿠키 sid로.
        email = _session_email(request) if _OAUTH_ON else None
        request.state.sid = ("u:" + email) if email else sid
        resp = await call_next(request)
        if new_sid:
            resp.set_cookie("ktx_sid", sid, max_age=60 * 60 * 24 * 30, httponly=True, samesite="lax")
        return resp


class _AssetCacheMiddleware(BaseHTTPMiddleware):
    """버전이 붙은 정적 자산(?v=…)은 브라우저가 오래 들고 있게 한다.
    내용이 바뀌면 _asset_ver()가 URL의 v를 바꾸므로 옛 파일을 붙들 위험이 없다 —
    그래서 no-store 로 막을 이유도 없다. 예전에는 접속할 때마다 js+css 444KB를 다시 받았다(2026-09-10)."""
    async def dispatch(self, request, call_next):
        resp = await call_next(request)
        path = request.url.path
        if request.query_params.get("v") and (path.endswith(".js") or path.endswith(".css")):
            resp.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return resp


app.add_middleware(AuthMiddleware)
app.add_middleware(_AssetCacheMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1024)   # js·css·json 전송량을 1/4 이하로


def _page(title, body, center=False):
    cardcls = "card center" if center else "card"
    return (f"<!doctype html><html lang=ko><head><meta charset=utf-8>"
            f"<meta name=viewport content='width=device-width,initial-scale=1'><title>{title}</title><style>"
            "body{font-family:-apple-system,system-ui,'Pretendard',sans-serif;background:#F2F4F6;color:#191F28;margin:0;padding:44px 20px;display:flex;justify-content:center}"
            ".card{background:#fff;border-radius:20px;box-shadow:0 6px 24px rgba(0,0,0,.07);padding:30px 26px;max-width:440px;width:100%}"
            ".card.center{text-align:center}.card.center .row{justify-content:center}"
            ".card.center .btnrow{display:flex;justify-content:center;gap:10px;margin-top:20px}"
            "h2{margin:0 0 16px;font-size:22px;letter-spacing:-.4px}h4{margin:18px 0 8px;font-size:14px;color:#6B7684}"
            ".row{display:flex;justify-content:space-between;align-items:center;gap:8px;padding:12px 0;border-bottom:1px solid #EEF1F4;font-size:14px}"
            ".btn{display:inline-block;background:#3182F6;color:#fff;text-decoration:none;padding:10px 16px;border-radius:12px;font-weight:700;font-size:14px;border:none;cursor:pointer}"
            ".btn.sm{padding:7px 13px;font-size:13px;border-radius:9px}.btn.ghost{background:#EEF1F4;color:#4E5968}"
            ".field{width:100%;box-sizing:border-box;padding:13px 14px;border:1.5px solid #E5E8EB;border-radius:12px;font-size:15px;margin:8px 0 4px;text-align:center}"
            ".frow{display:flex;align-items:center;gap:12px;padding:11px 0;border-bottom:1px solid #EEF1F4;text-align:left}"
            ".frow .k{flex:0 0 52px;font-size:14px;color:#6B7684;font-weight:600}"
            ".frow .v{flex:1;min-width:0;font-size:15px;color:#191F28;word-break:break-all}"
            ".frow input{flex:1;min-width:0;box-sizing:border-box;padding:10px 12px;border:1.5px solid #E5E8EB;border-radius:10px;font-size:15px;text-align:left}"
            ".frow input:focus{outline:none;border-color:#3182F6}"
            ".lead{margin-bottom:18px}"
            "p{line-height:1.6;font-size:15px;color:#4E5968}b{color:#191F28}"
            f"</style></head><body><div class='{cardcls}'><h2>{title}</h2>{body}</div></body></html>")


@app.get("/auth/login")
async def auth_login(request: Request):
    if not _OAUTH_ON:
        return _HTML(_page("설정 필요", "<p>구글 로그인이 설정되지 않았습니다.</p>"), status_code=503)
    ru = _redirect_uri(request)
    params = {"client_id": _GG_ID, "redirect_uri": ru, "response_type": "code",
              "scope": "openid email profile", "prompt": "select_account"}
    if LOCAL_MODE:
        st, ch = _pkce_new(ru)
        params.update({"state": st, "code_challenge": ch, "code_challenge_method": "S256"})
        # exe는 설정을 중앙(웹)과 주고받아야 해서, 창을 닫아도 쓸 수 있는 refresh_token 이 필요하다.
        # 구글은 access_type=offline + consent 일 때만 refresh_token 을 준다.
        params.update({"access_type": "offline", "prompt": "select_account consent"})
    return _Redirect("https://accounts.google.com/o/oauth2/v2/auth?" + _urlparse.urlencode(params))


@app.get("/auth/callback")
async def auth_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    ent = _PKCE.pop(state, None) if LOCAL_MODE else None
    if error or not code or (LOCAL_MODE and not ent):
        return _HTML(_page("로그인 실패", "<p>취소되었거나 실패했습니다.</p><a class=btn href='/auth/login'>다시 시도</a>"), status_code=400)
    data = {"code": code, "client_id": _GG_ID, "client_secret": _GG_SECRET,
            "redirect_uri": ent[1] if ent else (_BASE_URL + "/auth/callback"),
            "grant_type": "authorization_code"}
    if ent:
        data["code_verifier"] = ent[0]
    try:
        async with httpx.AsyncClient(timeout=15) as c:
            r = await c.post("https://oauth2.googleapis.com/token", data=data)
        tok = r.json()
        idt = tok.get("id_token", "")
        payload = _json.loads(_b64.urlsafe_b64decode(idt.split(".")[1] + "==").decode())
        email = (payload.get("email") or "").lower()
        if not email or payload.get("email_verified") is False:
            return _HTML(_page("오류", "<p>이메일을 확인할 수 없습니다.</p>"), status_code=400)
    except Exception as e:
        logger.warning(f"OAuth 콜백 오류: {e!r}")
        return _HTML(_page("오류", "<p>로그인 처리 중 오류가 발생했습니다.</p>"), status_code=500)
    if LOCAL_MODE and tok.get("refresh_token"):
        _hub_save_token(email, tok["refresh_token"])
    # 미승인자는 /pending에서 이름을 입력받은 뒤 대기열에 추가한다(관리자 식별용)
    resp = _Redirect("/" if _is_approved(email) else "/pending")
    # http://127.0.0.1 에는 Secure 쿠키를 구울 수 없다(브라우저가 버린다)
    resp.set_cookie("ktx_auth", _sign(email), max_age=60 * 60 * 24 * 30,
                    httponly=True, samesite="lax", secure=not LOCAL_MODE)
    return resp


@app.get("/auth/logout")
async def auth_logout():
    resp = _Redirect("/auth/login")
    resp.delete_cookie("ktx_auth")
    return resp


@app.get("/pending")
async def pending_page(request: Request):
    email = _session_email(request)
    if not email:
        return _Redirect("/auth/login")
    if _is_approved(email):
        return _Redirect("/")
    d = _allow_load()
    name = d.get("names", {}).get(email)
    esc = _html.escape
    rejected = email in d.get("rejected", [])
    waiting = email in d.get("pending", [])
    if rejected or not waiting:
        # 아직 요청 전: 관리자 식별용 이름을 받아 대기 목록에 올린다
        lead = ("승인이 거절되었습니다. 다시 신청하세요."
                if rejected else "이용하려면 관리자 승인이 필요합니다.")
        val = f" value='{esc(name)}'" if name else ""
        body = (f"<p class=lead>{lead}</p>"
                "<form method='post' action='/pending/submit'>"
                f"<div class=frow><span class=k>이메일</span><span class=v>{esc(email)}</span></div>"
                "<div class=frow><span class=k>이름</span>"
                f"<input name='name' placeholder='이름을 입력하세요' maxlength='30' required autofocus{val}></div>"
                "<div class='btnrow'><button class='btn' type='submit'>승인 요청</button>"
                "<a class='btn ghost' href='/auth/logout'>로그아웃</a></div></form>")
        title = "승인이 거절되었습니다" if rejected else "승인되지 않은 사용자입니다"
        return _HTML(_page(title, body, center=True))
    body = ("<p class=lead>관리자가 승인하면 바로 이용할 수 있습니다.</p>"
            f"<div class=frow><span class=k>이메일</span><span class=v>{esc(email)}</span></div>"
            f"<div class=frow><span class=k>이름</span><span class=v>{esc(name)}</span></div>"
            "<div class='btnrow'><a class=btn href='/'>새로고침</a>"
            "<a class='btn ghost' href='/auth/logout'>로그아웃</a></div>")
    return _HTML(_page("승인 대기 중입니다", body, center=True))


@app.post("/pending/submit")
async def pending_submit(request: Request):
    email = _session_email(request)
    if not email:
        return _Redirect("/auth/login", status_code=303)
    # request.form()은 python-multipart 를 요구한다(없으면 500). 폼이 한 칸뿐이라 직접 읽는다.
    body = (await request.body()).decode("utf-8", "replace")
    form = _urlparse.parse_qs(body, keep_blank_values=True)
    name = (form.get("name", [""])[0] or "").strip()[:30]
    if name:
        d = _allow_load()
        d.setdefault("names", {})[email] = name
        d["rejected"] = [x for x in d.get("rejected", []) if x != email]   # 다시 신청 → 거절 해제
        if not _is_approved(email) and email not in d.get("pending", []):
            d.setdefault("pending", []).append(email)
        _allow_save(d)
        logger.info(f"[승인대기] {name} <{email}>")
    # 303 — 307이면 브라우저가 POST를 유지한 채 /pending 으로 가서 405가 난다
    return _Redirect("/pending", status_code=303)


@app.get("/api/me")
async def api_me(request: Request):
    email = _session_email(request)
    return {"oauth": _OAUTH_ON, "email": email,
            "approved": _is_approved(email), "admin": email in _ADMIN_EMAILS,
            # 단독 실행본에는 nodriver(크롬 자동화)가 없어 간편현금결제 자동결제를 못 한다
            "local": LOCAL_MODE, "cash_autopay": _PAY_AVAILABLE}


# 설정은 로그인한 구글 계정에 묶는다 — 다른 PC·폰에서 같은 계정으로 들어오면 그대로 따라온다.
# 로그인이 없으면(개발/로컬) 예전처럼 한 벌을 공유한다.
_SYNC_KEY = "__shared__"

# 비밀번호 성격의 필드만 그 기기에 남긴다. 스마트티켓·마일리지·현금영수증 같은
# 설정은 계정에 묶여 다른 기기에서도 그대로 쓰인다.
# 이 기기에만 남는 값 — 계정 동기화에서 제외한다(카드번호·유효기간·비밀번호·생년월일/사업자번호)
_SECRET_FIELDS = ("payPin", "cardNo", "card_no", "cardPw", "cardPassword",
                  "cardExp", "cardAuthVal")
# pay.card 안에서 뺄 값. 카드 종류(개인/법인)와 할부 개월은 설정이라 동기화한다.
_CARD_SECRET_FIELDS = ("cardNo", "cardExp", "cardPw", "cardAuthVal")


def _sync_key_for(request) -> str:
    email = _session_email(request) if _OAUTH_ON else None
    return f"u:{email}" if email else _SYNC_KEY


def _sync_merge(key: str, data: dict, incoming_ts: dict) -> bool:
    """통째로 덮으면 다른 기기가 방금 바꾼 값이 소리 없이 사라진다(두 기기 동시 사용).
    키마다 최종 수정 시각을 비교해 더 최신인 쪽만 남긴다(2026-09-10)."""
    cur, cur_meta = _sync_get_full(key)
    merged = dict(cur or {})
    merged_meta = {k: v for k, v in (cur_meta or {}).items() if not _sync_droppable(k)}
    now_ms = int(time.time() * 1000)
    kept = 0
    for k, v in (data or {}).items():
        try:
            t_in = float((incoming_ts or {}).get(k) or 0)
        except Exception:
            t_in = 0.0
        try:
            t_cur = float(merged_meta.get(k) or 0)
        except Exception:
            t_cur = 0.0
        if t_in < t_cur:   # 서버 쪽이 더 최신 — 이 기기 값은 버린다
            kept += 1
            continue
        merged[k] = v
        merged_meta[k] = int(t_in) or now_ms
    for k in list(merged):
        if _sync_droppable(k):
            merged.pop(k, None); merged_meta.pop(k, None)
    if kept:
        logger.info(f"[동기화] {key} · 다른 기기의 더 최신 값 {kept}개 유지")
    return _sync_set(key, merged, merged_meta)


def _strip_device_only(data: dict) -> dict:
    """계정에 저장하기 전 비밀 필드만 걷어낸다(중첩된 pay 안까지)."""
    def clean(v):
        try:
            o = json.loads(v)
        except Exception:
            return v
        if not isinstance(o, dict):
            return v
        for f in _SECRET_FIELDS:
            o.pop(f, None)
        pay = o.get("pay")
        if isinstance(pay, dict):
            for f in _SECRET_FIELDS:
                pay.pop(f, None)
            if isinstance(pay.get("card"), dict):
                for f in _CARD_SECRET_FIELDS:
                    pay["card"].pop(f, None)
        return json.dumps(o, ensure_ascii=False)
    return {k: (clean(v) if isinstance(v, str) else v)
            for k, v in data.items() if not _sync_droppable(k)}


# ── 공용 감시 작업 보관 ────────────────────────────────────────
# 감시는 각 서버(맥미니 웹 / 각자의 PC exe)에서 돌지만, 목록과 로그는 계정 단위로 여기 모은다.
# 그래야 폰으로 접속해도 PC에서 돌던 감시의 진행·로그를 이어서 볼 수 있다(2026-09-10).
# 저장하는 요청 본문(req)에서는 비밀번호·결제 비밀을 뺀다 — 재개할 때 그 기기 값으로 채운다.
_JOB_SECRET_FIELDS = ("password", "pay_pin", "card_no", "card_exp", "card_pw", "card_auth_val")
_RUNNER_ALIVE = 90.0    # 이 시간 안에 소식이 있으면 그 러너가 살아 있다고 본다


def _runner_id() -> str:
    return "web" if not LOCAL_MODE else f"pc:{_kmod._device_id()[:8]}"


def _runner_label() -> str:
    if not LOCAL_MODE:
        return "웹"
    try:
        import socket as _sock
        return _sock.gethostname().split(".")[0][:20] or "내 PC"
    except Exception:
        return "내 PC"


def _jobs_db():
    con = _sqlite3.connect(_SYNC_DB)
    con.execute("CREATE TABLE IF NOT EXISTS remote_jobs (email TEXT, job TEXT, runner TEXT, label TEXT,"
                " payload TEXT, req TEXT, updated REAL, PRIMARY KEY(email, job))")
    con.execute("CREATE TABLE IF NOT EXISTS remote_logs (email TEXT, job TEXT, lines TEXT, updated REAL,"
                " PRIMARY KEY(email, job))")
    con.execute("CREATE TABLE IF NOT EXISTS job_cmds (email TEXT, job TEXT, runner TEXT, cmd TEXT,"
                " body TEXT, created REAL)")
    return con


def _clean_req(req) -> dict:
    if not isinstance(req, dict):
        return {}
    return {k: v for k, v in req.items() if k not in _JOB_SECRET_FIELDS}


def _rjobs_put(email: str, runner: str, label: str, items: list) -> int:
    """다른 서버(exe)가 올린 자기 작업 목록·로그를 갈아 끼운다."""
    n = 0
    try:
        con = _jobs_db()
        now = time.time()
        keep = set()
        for it in items or []:
            job = str(it.get("job") or "").strip()
            if not job:
                continue
            keep.add(job)
            con.execute("INSERT INTO remote_jobs(email,job,runner,label,payload,req,updated) VALUES(?,?,?,?,?,?,?)"
                        " ON CONFLICT(email,job) DO UPDATE SET runner=excluded.runner, label=excluded.label,"
                        " payload=excluded.payload, req=excluded.req, updated=excluded.updated",
                        (email, job, runner, label, _json.dumps(it.get("payload") or {}, ensure_ascii=False),
                         _json.dumps(_clean_req(it.get("req")), ensure_ascii=False), now))
            lines = it.get("lines")
            if isinstance(lines, list):
                con.execute("INSERT INTO remote_logs(email,job,lines,updated) VALUES(?,?,?,?)"
                            " ON CONFLICT(email,job) DO UPDATE SET lines=excluded.lines, updated=excluded.updated",
                            (email, job, _json.dumps(lines[-_JOBLOG_MAX:], ensure_ascii=False), now))
            n += 1
        # 그 러너가 더는 들고 있지 않은 작업은 지운다(삭제가 반영되도록)
        rows = con.execute("SELECT job FROM remote_jobs WHERE email=? AND runner=?", (email, runner)).fetchall()
        for (job,) in rows:
            if job not in keep:
                con.execute("DELETE FROM remote_jobs WHERE email=? AND job=?", (email, job))
                con.execute("DELETE FROM remote_logs WHERE email=? AND job=?", (email, job))
        con.commit(); con.close()
    except Exception as e:
        logger.warning(f"[공용작업] 저장 실패: {e}")
    return n


def _rjobs_list(email: str, exclude_runner: str = "") -> list:
    out = []
    try:
        con = _jobs_db()
        rows = con.execute("SELECT job, runner, label, payload, updated FROM remote_jobs WHERE email=?",
                           (email,)).fetchall()
        con.close()
    except Exception:
        return out
    now = time.time()
    for job, runner, label, payload, updated in rows:
        if exclude_runner and runner == exclude_runner:
            continue
        try:
            p = _json.loads(payload or "{}")
        except Exception:
            continue
        p["job"] = job
        p["remote"] = True
        p["runner"] = runner
        p["runner_label"] = label or runner
        p["runner_online"] = bool(now - (updated or 0) < _RUNNER_ALIVE)
        out.append(p)
    return out


def _rjob_log(email: str, job: str) -> Optional[list]:
    try:
        con = _jobs_db()
        row = con.execute("SELECT lines FROM remote_logs WHERE email=? AND job=?", (email, job)).fetchone()
        con.close()
        return _json.loads(row[0]) if row and row[0] else None
    except Exception:
        return None


def _rjob_req(email: str, job: str) -> Optional[dict]:
    try:
        con = _jobs_db()
        row = con.execute("SELECT req FROM remote_jobs WHERE email=? AND job=?", (email, job)).fetchone()
        con.close()
        return _json.loads(row[0]) if row and row[0] else None
    except Exception:
        return None


def _runners_for(email: str) -> list:
    """이 계정에서 감시를 돌릴 수 있는 서버 목록(지금 살아 있는 것만)."""
    out = []
    if not LOCAL_MODE:
        out.append({"runner": "web", "label": "웹", "online": True, "here": True})
    else:
        out.append({"runner": _runner_id(), "label": _runner_label(), "online": True, "here": True})
    seen = {x["runner"] for x in out}
    try:
        con = _jobs_db()
        rows = con.execute("SELECT runner, label, MAX(updated) FROM remote_jobs WHERE email=? GROUP BY runner",
                           (email,)).fetchall()
        con.close()
    except Exception:
        rows = []
    now = time.time()
    for runner, label, updated in rows:
        if runner in seen:
            continue
        out.append({"runner": runner, "label": label or runner,
                    "online": bool(now - (updated or 0) < _RUNNER_ALIVE), "here": False})
    return out


def _cmd_push(email: str, job: str, runner: str, cmd: str, body: dict) -> None:
    try:
        con = _jobs_db()
        con.execute("INSERT INTO job_cmds(email,job,runner,cmd,body,created) VALUES(?,?,?,?,?,?)",
                    (email, job, runner, cmd, _json.dumps(body or {}, ensure_ascii=False), time.time()))
        con.execute("DELETE FROM job_cmds WHERE created < ?", (time.time() - 600,))
        con.commit(); con.close()
        logger.info(f"[공용작업] {runner} 에게 '{cmd}' 지시 전달 · {job}")
    except Exception as e:
        logger.warning(f"[공용작업] 지시 저장 실패: {e}")


def _cmd_pop(email: str, runner: str) -> list:
    out = []
    try:
        con = _jobs_db()
        rows = con.execute("SELECT rowid, job, cmd, body FROM job_cmds WHERE email=? AND runner=? ORDER BY created",
                           (email, runner)).fetchall()
        for rid, job, cmd, body in rows:
            try:
                out.append({"job": job, "cmd": cmd, "body": _json.loads(body or "{}")})
            except Exception:
                pass
            con.execute("DELETE FROM job_cmds WHERE rowid=?", (rid,))
        con.commit(); con.close()
    except Exception:
        pass
    return out


# ── 장치(윈도우 exe) 전용 API ─────────────────────────────────
# exe는 브라우저 세션이 없어 쿠키를 못 쓴다. 대신 자기 구글 로그인에서 받은 ID 토큰을 Bearer 로 보낸다.
# 이 경로만 Cloudflare Access 를 Bypass 로 열어 두고(호스트 전체가 아니라 /api/device/* 만),
# 대신 여기서 토큰을 구글에 검증하고 승인 목록까지 확인한다 — exe 안에 비밀값을 넣지 않는 방식.
_TOKEN_CACHE: Dict[str, tuple] = {}   # id_token → (email, exp_epoch)


async def _email_from_bearer(request: Request) -> Optional[str]:
    auth = request.headers.get("authorization") or ""
    if not auth.lower().startswith("bearer "):
        return None
    tok = auth[7:].strip()
    # 이 경로는 공개(CF Access Bypass)라 아무 문자열이나 날아올 수 있다. JWT 모양이 아니면
    # 구글에 물어보지도 않는다 — 남의 장난에 우리 아웃바운드 호출을 태우지 않기 위해.
    if not tok or tok.count(".") != 2 or len(tok) > 4096:
        return None
    hit = _TOKEN_CACHE.get(tok)
    if hit and hit[1] > time.time() + 30:
        return hit[0]
    try:
        async with httpx.AsyncClient(timeout=10) as c:
            r = await c.get("https://oauth2.googleapis.com/tokeninfo", params={"id_token": tok})
        if r.status_code != 200:
            return None
        j = r.json()
    except Exception as e:
        logger.warning(f"[장치] 토큰 검증 실패: {e!r}")
        return None
    aud = (j.get("aud") or "").strip()
    ok_aud = {x for x in (_GG_ID, _DESKTOP_ID) if x}
    if ok_aud and aud not in ok_aud:
        logger.warning(f"[장치] 다른 앱의 토큰 거부(aud={aud[:12]}…)")
        return None
    if str(j.get("email_verified")).lower() not in ("true", "1"):
        return None
    email = (j.get("email") or "").lower()
    if not email:
        return None
    try:
        exp = float(j.get("exp") or 0)
    except Exception:
        exp = time.time() + 300
    _TOKEN_CACHE[tok] = (email, exp)
    if len(_TOKEN_CACHE) > 50:
        for k, v in list(_TOKEN_CACHE.items()):
            if v[1] <= time.time():
                _TOKEN_CACHE.pop(k, None)
    return email


async def _device_email(request: Request):
    """(email, 오류응답) — 오류응답이 있으면 그대로 돌려주면 된다."""
    email = await _email_from_bearer(request)
    if not email:
        return None, {"ok": False, "error": "인증 필요(구글 로그인)", "auth": False}
    if not _is_approved(email):
        return None, {"ok": False, "error": "승인 대기 중인 계정입니다", "auth": False}
    return email, None


@app.get("/api/device/ping")
async def api_device_ping(request: Request):
    email, err = await _device_email(request)
    if err: return err
    return {"ok": True, "email": email}


@app.get("/api/device/sync")
async def api_device_sync_get(request: Request):
    email, err = await _device_email(request)
    if err: return err
    blob, meta = _sync_get_full(f"u:{email}")
    return {"ok": True, "data": blob, "ts": meta, "key": f"u:{email}"}


@app.post("/api/device/sync")
async def api_device_sync_set(request: Request):
    email, err = await _device_email(request)
    if err: return err
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "bad json"}
    data = body.get("data")
    if not isinstance(data, dict):
        return {"ok": False, "error": "no data"}
    return {"ok": _sync_merge(f"u:{email}", _strip_device_only(data),
                              body.get("ts") if isinstance(body.get("ts"), dict) else {})}


@app.post("/api/device/jobs/push")
async def api_device_jobs_push(request: Request):
    """exe가 자기 감시 목록·로그를 중앙에 올린다 — 폰·다른 PC에서도 이어서 보게."""
    email, err = await _device_email(request)
    if err: return err
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "bad json"}
    runner = str(body.get("runner") or "").strip() or "pc:?"
    label = str(body.get("label") or "").strip()[:20] or runner
    n = _rjobs_put(email, runner, label, body.get("jobs") or [])
    return {"ok": True, "saved": n, "commands": _cmd_pop(email, runner)}


@app.get("/api/device/joblog")
async def api_device_joblog(request: Request, job: str = ""):
    """웹에서 도는 감시의 로그 — exe 결과 탭이 펼쳐 볼 때 쓴다."""
    email, err = await _device_email(request)
    if err: return err
    dq = _JOB_LOGS.get(_joblog_key(f"u:{email}", _job_id(job)))
    if dq is None:
        return {"ok": True, "lines": _rjob_log(email, job) or []}
    return {"ok": True, "lines": list(dq)}


@app.get("/api/device/runners")
async def api_device_runners(request: Request):
    email, err = await _device_email(request)
    if err: return err
    return {"ok": True, "runners": _runners_for(email)}


@app.post("/api/device/command")
async def api_device_command(request: Request):
    """exe가 '웹에서 이어서 실행' 을 요청할 때."""
    email, err = await _device_email(request)
    if err: return err
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "bad json"}
    job, runner, cmd = str(body.get("job") or ""), str(body.get("runner") or ""), str(body.get("cmd") or "")
    if not job or not runner or cmd not in ("resume", "stop"):
        return {"ok": False, "error": "잘못된 요청"}
    if runner == "web":
        return await _run_command_here(f"u:{email}", job, cmd, body.get("body") or {})
    _cmd_push(email, job, runner, cmd, body.get("body") or {})
    return {"ok": True, "queued": True}


async def _run_command_here(sid: str, job: str, cmd: str, body: dict):
    """이 서버에서 지시를 실행한다(재개/중단)."""
    st = KTX_SESSIONS.get(f"{sid}:{_job_id(job)}")
    if cmd == "stop":
        if st and st.task and not st.task.done():
            st.task.cancel()
        if st:
            st.running = False; st.task = None; st.phase = "idle"; st.status_text = "사용자가 중단함"
        _save_jobs()
        return {"ok": True}
    # 같은 작업이 다른 서버에서 돌고 있으면 시작하지 않는다 — 두 곳이 동시에 잡으면 예약·결제가 두 번 된다.
    # 자격증명 확인보다 먼저 본다(어느 쪽이든 거부지만, 사용자에게는 이 사유가 정확하다).
    if not LOCAL_MODE and sid.startswith("u:"):
        other = next((x for x in _rjobs_list(sid[2:], exclude_runner=_runner_id())
                      if x.get("job") == job and x.get("running") and x.get("runner_online")), None)
        if other:
            return {"ok": False, "error": f"{other.get('runner_label') or '다른 기기'}에서 이미 감시 중입니다"}
    req = body if body.get("dep") else None
    if req is None and st and st.req:
        req = st.req
    if req is None and sid.startswith("u:"):
        req = _rjob_req(sid[2:], job)   # 다른 서버에서 돌던 작업을 여기서 이어받는 경우
    if req is None:
        return {"ok": False, "error": "재개할 요청 정보가 없습니다"}
    req = dict(req)
    if not req.get("password") or not req.get("member_no"):
        # 보관본에는 비밀을 안 담는다 — 이 계정에 저장된 값으로 채운다
        blob = _sync_get(sid) or {}
        try:
            acc = _json.loads(blob.get("kor_ktx_account") or "{}")
        except Exception:
            acc = {}
        req["member_no"] = req.get("member_no") or acc.get("memberNo") or ""
        req["password"] = req.get("password") or acc.get("password") or ""
        if not req["password"]:
            return {"ok": False, "error": "이 계정의 코레일 로그인 정보가 없습니다(설정에서 등록)"}
    if req.get("auto_pay") and req.get("pay_method") == "card" and not req.get("card_no"):
        req["auto_pay"] = False   # 카드 정보는 기기에만 있다 — 여기선 예매까지만 한다
        logger.info(f"[공용작업] {job} · 카드 정보가 없어 예매만 진행합니다")
    try:
        r = TrainStartRequest(**req)
    except Exception as e:
        return {"ok": False, "error": f"요청 형식 오류: {e}"}
    if st is None:
        st = WebState(); KTX_SESSIONS[f"{sid}:{_job_id(job)}"] = st
    if st.running:
        return {"ok": False, "error": "이미 감시 중입니다"}
    sh, eh = r.start_hhmm.zfill(4), r.end_hhmm.zfill(4)
    p = _build_params_from_req(r, _job_id(job), sh, eh)
    st.params = p; st.req = r.model_dump()
    st.task = asyncio.create_task(runner_loop(st, p, sid, _job_id(job)))
    _save_jobs()
    return {"ok": True}


@app.get("/api/device/jobs")
async def api_device_jobs(request: Request):
    """이 계정이 웹(맥미니)에서 돌리는 감시 목록 — exe 결과 탭에 읽기 전용으로 비춰 준다."""
    email, err = await _device_email(request)
    if err: return err
    pre = f"u:{email}:"
    out = []
    for key, st in KTX_SESSIONS.items():
        if not key.startswith(pre):
            continue
        p = st.params or {}
        out.append({"job": key[len(pre):], "running": bool(st.running), "phase": st.phase,
                    "status_text": st.status_text, "label": p.get("label") or "",
                    "dep": p.get("dep"), "arr": p.get("arr"), "date": p.get("date_yyyymmdd"),
                    "start_hhmm": p.get("start_hhmm"), "end_hhmm": p.get("end_hhmm"),
                    "adults": p.get("adults"), "attempts": st.attempts,
                    "last_check_at": st.last_check_at, "last_error": st.last_error,
                    "result": st.result, "opt_text": p.get("opt_text") or "",
                    "trains": _job_train_rows(p), "started_ts": st.started_ts,
                    "pay_state": getattr(st, "pay_state", None),
                    "auto_pay": bool(p.get("auto_pay")), "pay_method": p.get("pay_method") or "toss",
                    "remote": True})
    return {"ok": True, "jobs": out}


@app.get("/api/hubstate")
async def api_hubstate():
    """exe에서 '웹과 연결됨' 여부를 보여주기 위한 상태."""
    if not LOCAL_MODE:
        return {"ok": True, "local": False}
    linked = bool(_hub_refresh_token() or os.environ.get("TRAINER_HUB_TOKEN", "").strip())
    return {"ok": True, "local": True, "linked": linked, "hub": HUB_URL,
            "connected": bool(_hub_state["ok"]), "error": _hub_state["error"]}


@app.get("/api/sync")
async def api_sync_get(request: Request):
    """구글 계정별로 완전히 격리된 설정만 돌려준다.
    ⚠️ 예전에는 계정 데이터가 없으면 공용(__shared__) 값을 물려줬는데, 거기에 코레일
    회원번호·비밀번호가 들어 있어 새로 로그인한 다른 사람에게 그대로 넘어갔다
    (2026-09-09 실측 확인). 상속을 없앤다 — 새 계정은 빈 상태로 시작해 직접 등록한다."""
    key = _sync_key_for(request)
    blob, meta = _sync_get_full(key)
    if LOCAL_MODE and not blob:
        # 첫 실행이라 이 PC에 아무것도 없을 때만 중앙을 잠깐 기다린다(그 외에는 배경 루프가 채워 둔다).
        try:
            j = await asyncio.wait_for(_hub_call("GET", "/api/device/sync"), timeout=_HUB_TIMEOUT)
        except Exception:
            j = None
        if j and isinstance(j.get("data"), dict):
            _sync_set(key, j["data"], j.get("ts") or {})
            return {"ok": True, "data": j["data"], "ts": j.get("ts") or {}, "key": key, "hub": True}
    return {"ok": True, "data": blob, "ts": meta, "key": key, "hub": bool(_hub_state["ok"])}


def _member_owner(member_no: str, me_key: str):
    """이 코레일 회원번호를 이미 쓰고 있는 다른 구글 계정 키. 없으면 None.
    한 코레일 계정은 먼저 등록한 구글 계정에 묶는다 — 기기에 남은 남의 사본이
    다시 올라와 퍼지는 것을 막는다(2026-09-09 사고)."""
    if not member_no:
        return None
    try:
        con = _sync_db()
        rows = con.execute("SELECT email, blob FROM user_sync").fetchall()
        con.close()
    except Exception:
        return None
    for key, blob in rows:
        if key == me_key or not key.startswith("u:"):
            continue
        try:
            acc = _json.loads(_json.loads(blob or "{}").get("kor_ktx_account") or "{}")
        except Exception:
            continue
        if (acc.get("memberNo") or "").strip() == member_no:
            return key
    return None


@app.post("/api/sync")
async def api_sync_set(request: Request):
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "bad json"}
    data = body.get("data")
    if not isinstance(data, dict):
        return {"ok": False, "error": "no data"}
    data = _strip_device_only(data)
    try:   # 데모 계정이 실계정을 덮어쓰지 못하게(2026-09-08 사고 방지)
        acc = json.loads(data.get("kor_ktx_account") or "{}")
        if _is_demo_acct(acc.get("memberNo", ""), acc.get("password", "")):
            logger.warning("[동기화] 데모 계정 저장 요청 거부")
            return {"ok": False, "error": "데모 계정은 저장하지 않습니다"}
    except Exception:
        pass
    key = _sync_key_for(request)
    try:   # 남의 코레일 계정이 딸려 올라오면 그 항목만 떼고 나머지는 저장한다
        acc = json.loads(data.get("kor_ktx_account") or "{}")
        mno = (acc.get("memberNo") or "").strip()
        owner = _member_owner(mno, key)
        if owner:
            logger.warning(f"[동기화] {key} 가 {owner} 의 코레일 계정({mno[:4]}****)을 저장하려 해 제외했습니다")
            data.pop("kor_ktx_account", None)
    except Exception:
        pass
    ts_in = body.get("ts") if isinstance(body.get("ts"), dict) else {}
    ok = _sync_merge(key, data, ts_in)
    if LOCAL_MODE:   # exe — 중앙 반영은 배경 루프에 맡긴다(여기서 기다리면 저장할 때마다 화면이 멈춘다)
        _hub_dirty["on"] = True
        _hub_state["email"] = key[2:] if key.startswith("u:") else _hub_state.get("email", "")
    if key == _SYNC_KEY:
        _adopt_synced_account()   # 서버 기본 계정은 소유자 버킷에서만 바꾼다
    return {"ok": ok}


# 로그인 없이 UI/UX만 평가하기 위한 데모 페이지. 동기화·로그·계정조회는 프론트에서 __DEMO__로 비활성(실데이터 노출/덮어쓰기 없음).
_DEMO_SEED = (
    "<script>window.__DEMO__=true;"
    "(function(){var m={};var shim={getItem:function(k){return k in m?m[k]:null},"
    "setItem:function(k,v){m[k]=String(v)},removeItem:function(k){delete m[k]},"
    "clear:function(){m={}},key:function(i){return Object.keys(m)[i]||null}};"
    "Object.defineProperty(shim,'length',{get:function(){return Object.keys(m).length}});"
    "try{Object.defineProperty(window,'localStorage',{value:shim,configurable:true});}catch(e){}"
    "})();"
    "try{var L=localStorage;"
    # 새 데이터 모델: 계정(설정), 예약조건(메인), 영수증, 프리셋
    "L.setItem('kor_ktx_account',JSON.stringify({memberNo:'1234567890',password:'demo-pass'}));"
    "L.setItem('kor_ktx_pay',JSON.stringify({method:'cash',payPin:'123456',smartTicket:true,useMileage:false,cashReceipt:true,receiptType:'personal',bizIdKind:'biz',phone:'010-1234-5678',bizNo:''}));"
    "L.setItem('kor_ktx_resv',JSON.stringify({dep:'서울',arr:'부산',date:'2026-07-01',startTime:'08:00',endTime:'12:00',adults:1,interval:60,seatClass:'general_first',allowWaiting:false,runMode:'pay'}));"
    "L.setItem('kor_ktx_presets',JSON.stringify(["
    "{id:'d1',dep:'서울',arr:'부산',weekday:5,startTime:'08:00',endTime:'12:00',adults:1,interval:10,seatClass:'general_first',allowWaiting:false},"
    "{id:'d2',dep:'수서',arr:'동대구',weekday:1,startTime:'18:00',endTime:'22:00',adults:2,interval:10,seatClass:'special_first',allowWaiting:false},"
    "{id:'d3',dep:'용산',arr:'광주송정',weekday:6,startTime:'09:00',endTime:'15:00',adults:1,interval:30,seatClass:'general_only',allowWaiting:true}"
    "]));}catch(e){}</script>"
)


@app.get("/demo")
async def demo_page():
    try:
        html = _index_html()
    except Exception as e:
        return _HTML(f"<h1>demo unavailable</h1><p>{e}</p>", status_code=500)
    html = html.replace('<script src="/korail.js', _DEMO_SEED + '<script src="/korail.js')
    return _HTML(html, headers={"Cache-Control": "no-store"})


@app.get("/admin")
async def admin_page(request: Request):
    if _session_email(request) not in _ADMIN_EMAILS:
        return _HTML(_page("권한 없음", "<p>관리자만 접근할 수 있습니다.</p>"), status_code=403)
    d = _allow_load()
    names = d.get("names", {})
    def _who(e):
        nm = names.get(e)
        return (f"<b>{nm}</b><br><span style='color:#8B95A1;font-size:12px'>{e}</span>") if nm else e
    pend = "".join(
        f"<div class=row><span>{_who(e)}</span><span>"
        f"<a class='btn sm' href='/admin/approve?email={_urlparse.quote(e)}'>승인</a> "
        f"<a class='btn sm ghost' href='/admin/reject?email={_urlparse.quote(e)}'>거절</a></span></div>"
        for e in d.get("pending", []))
    appr = "".join(
        f"<div class=row><span>{_who(e)}</span>"
        f"<a class='btn sm ghost' href='/admin/reject?email={_urlparse.quote(e)}'>제거</a></div>"
        for e in d.get("approved", []))
    body = (f"<h4>승인 대기 ({len(d.get('pending', []))})</h4>{pend or '<p>대기 중인 요청이 없습니다.</p>'}"
            f"<h4>승인된 사용자</h4>{appr or '<p>없음</p>'}"
            "<p style='margin-top:18px'><a class='btn ghost' href='/auth/logout'>로그아웃</a></p>")
    return _HTML(_page("관리자", body))


@app.get("/api/admin/users")
async def api_admin_users(request: Request):
    """관리자 모달용 — 승인/대기/거절 목록을 JSON 으로(페이지 이동 없이 처리하려고)."""
    if _session_email(request) not in _ADMIN_EMAILS:
        return {"ok": False, "error": "관리자만 볼 수 있습니다"}
    d = _allow_load()
    names = d.get("names", {})
    def rows(key):
        return [{"email": e, "name": names.get(e, "")} for e in (d.get(key) or [])]
    return {"ok": True, "pending": rows("pending"), "approved": rows("approved"), "rejected": rows("rejected")}


@app.post("/api/admin/user")
async def api_admin_user(request: Request):
    """승인/거절/제거 — body {email, action: approve|reject}."""
    if _session_email(request) not in _ADMIN_EMAILS:
        return {"ok": False, "error": "관리자만 가능합니다"}
    try:
        body = await request.json()
    except Exception:
        return {"ok": False, "error": "bad json"}
    email = (body.get("email") or "").lower().strip()
    action = body.get("action")
    if not email or action not in ("approve", "reject"):
        return {"ok": False, "error": "잘못된 요청"}
    d = _allow_load()
    if action == "approve":
        if email not in d.get("approved", []):
            d.setdefault("approved", []).append(email)
        d["rejected"] = [x for x in d.get("rejected", []) if x != email]
    else:
        d["approved"] = [x for x in d.get("approved", []) if x != email]
        if email not in d.get("rejected", []):
            d.setdefault("rejected", []).append(email)
    d["pending"] = [x for x in d.get("pending", []) if x != email]
    _allow_save(d)
    logger.info(f"[관리자] {email} · {'승인' if action == 'approve' else '거절/제거'}")
    return {"ok": True}


@app.get("/admin/approve")
async def admin_approve(request: Request, email: str = ""):
    if _session_email(request) not in _ADMIN_EMAILS:
        return _HTML(_page("권한 없음", "<p>관리자만 가능합니다.</p>"), status_code=403)
    email = email.lower().strip(); d = _allow_load()
    if email and email not in d.get("approved", []):
        d.setdefault("approved", []).append(email)
    d["pending"] = [x for x in d.get("pending", []) if x != email]
    d["rejected"] = [x for x in d.get("rejected", []) if x != email]
    _allow_save(d)
    return _Redirect("/admin")


@app.get("/admin/reject")
async def admin_reject(request: Request, email: str = ""):
    if _session_email(request) not in _ADMIN_EMAILS:
        return _HTML(_page("권한 없음", "<p>관리자만 가능합니다.</p>"), status_code=403)
    email = email.lower().strip(); d = _allow_load()
    d["approved"] = [x for x in d.get("approved", []) if x != email]
    d["pending"] = [x for x in d.get("pending", []) if x != email]
    # 거절을 기록해 둔다 — 안 그러면 이름만 남아 당사자 화면이 계속 '대기 중'으로 보인다
    if email and email not in d.get("rejected", []):
        d.setdefault("rejected", []).append(email)
    _allow_save(d)
    return _Redirect("/admin")


class TrainStartRequest(BaseModel):
    date: str
    dep: str
    arr: str
    start_hhmm: str
    end_hhmm: str
    adults: int
    interval_sec: int
    seat_class: str = "general_first"   # general_first|general_only|special_first|special_only
    max_duration_min: int = 0           # 최대 소요시간(분). 0=제한 없음
    allow_waiting: bool = False         # 예약대기 허용
    allow_standing: bool = False        # 입석 허용(실험적·미검증)
    start_at: str = ""                  # 예약 시작 시각(ISO 로컬, 예: 2026-09-11T15:00:00). 빈 값=즉시
    # 좌석 지정(고급): none | pref(선호 조건 자동) | fixed(열차·좌석 직접 지정)
    seat_mode: str = "none"
    cand_seats: List[dict] = []
    cand_templates: Dict[str, List[dict]] = {}   # groupId → 좌석 목록(여러 그룹 동시 선택)
    cand_fallback: str = "watch"
    cand_label: str = ""
    seat_side: str = "window"           # window|aisle(우선) / window_only|aisle_only(그 자리만)
    seat_dir: str = "fwd"               # fwd|rev(우선) / fwd_only|rev_only(그 방향만)
    seat_forward: bool = False          # (구버전 호환) 순방향 우선
    fixed_train_no: str = ""            # fixed: 이 열차만 노림
    fixed_seats: list = []              # fixed: [{car_no, seat_no, label}]
    fixed_fallback: bool = True         # fixed: 지정 좌석 못 잡으면 같은 열차 아무 좌석
    # 탭별 옵션
    train_nos: list = []                # 긴급: 이 열차들만 후보(빈 값=범위 내 전부)
    train_times: list = []              # 고른 열차들의 출발시각(HHMM/HHMMSS) — 조회 범위를 그만큼만 좁힌다
    train_rows: list = []               # 고른 열차 정보 [{no,type,dep_time,arr_time}] — 결과 탭 목록용(조회 전에도 보여준다)
    one_shot: bool = False              # 보통: 1회만 시도하고 종료(폴링 없음)
    watch_seats: list = []              # 특수-지정좌석 감시: [{car_no,seat_no,label}] 중 인원수만큼 비면 예약
    open_burst: bool = False            # 특수-오픈 대기: 시작 후 10분간 5초 주기
    label: str = ""                     # 작업 이름(결과 탭 표시용)
    use_ncard: bool = False             # N카드 할인 적용(1명 예매만)
    ncard_no: str = ""                  # N카드 번호
    # 결제/계정 (UI에서 입력; 빈 값이면 .env 폴백)
    member_no: str = ""
    password: str = ""
    pay_pin: str = ""
    cash_receipt: bool = False
    receipt_type: str = "personal"      # personal|business
    phone: str = ""
    smart_ticket: bool = False
    auto_pay: bool = True               # 자동결제 사용
    pay_dryrun: bool = False            # 결제 직전까지만(테스트)
    use_mileage: bool = False           # 마일리지 전액 사용(간편현금결제 단계)
    pay_method: str = "cash"            # "cash"(간편현금결제·nodriver) | "toss"(토스페이·세션직접) | "card"(신용카드·세션직접)
    card_no: str = ""                   # 신용카드 — 기기에만 저장되는 값이라 매 요청에 실려 온다
    card_exp: str = ""                  # YYMM
    card_pw: str = ""                   # 앞 2자리
    card_auth_kind: str = "J"           # J=개인(생년월일 6자리) | S=법인(사업자번호 10자리)
    card_auth_val: str = ""
    card_installment: int = 0           # 0=일시불


_SEAT_OPTION = {
    "general_first": ReserveOption.GENERAL_FIRST,
    "general_only":  ReserveOption.GENERAL_ONLY,
    "special_first": ReserveOption.SPECIAL_FIRST,
    "special_only":  ReserveOption.SPECIAL_ONLY,
}


def _validate_start_req(req, p) -> Optional[str]:
    """시작 요청의 조합 검증 — 문제가 있으면 사용자용 메시지를 돌려준다."""
    if req.auto_pay and req.cash_receipt and not (
            req.phone.strip() or os.environ.get("KORAIL_RECEIPT_PHONE", "").strip()):
        return "현금영수증 신청 시 휴대폰번호를 입력하세요"
    if p["ncard"] and req.adults != 1:
        return "N카드 할인은 1명 예매에서만 적용됩니다(인원 1명으로 바꾸거나 N카드 적용을 끄세요)"
    if p["seat_mode"] == "cand" and not p["cand_seats"]:
        return "후보 좌석이 비어 있습니다(설정 › 좌석 후보에서 등록)"
    if p["seat_mode"] == "fixed" and (not p["fixed_train_no"] or len(p["fixed_seats"]) != req.adults):
        return f"좌석 직접 지정은 열차와 인원수({req.adults}명)만큼의 좌석을 골라야 합니다"
    if p["watch_seats"] and (not p["fixed_train_no"] or len(p["watch_seats"]) < req.adults):
        return f"지정 좌석 감시는 열차 1개와 인원수({req.adults}명) 이상의 좌석을 골라야 합니다"
    return None


_CLS_LABEL  = {"general_first": "일반실 우선", "general_only": "일반실",
                "special_first": "특실 우선", "special_only": "특실"}
_SIDE_LABEL = {"window": "창측 우선", "aisle": "내측 우선", "window_only": "창측만", "aisle_only": "내측만"}
_DIR_LABEL  = {"fwd": "순방향 우선", "rev": "역방향 우선", "fwd_only": "순방향만", "rev_only": "역방향만"}
_MODE_LABEL = {"cand": "선호 좌석", "fixed": "직접 선택"}


def _open_at_for(date_yyyymmdd: str) -> Optional[datetime]:
    """코레일 예매 개시 시각 = 출발일 1개월 전 (기본 07:00, 명절 등 공지 예외 시 그 시각).
    예외를 반영해야 그 시각까지 '자면서' 기다린다 — 07:00 고정이면 예외(10:00)까지 3시간을 헛호출한다
    (2026-09-09 사용자 사례: 추석 10/10 개시 10:00인데 07:00부터 계속 조회)."""
    try:
        d = datetime.strptime(date_yyyymmdd, "%Y%m%d")
    except Exception:
        return None
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    h, mn = 7, 0
    ds = f"{d.year:04d}-{d.month:02d}-{d.day:02d}"   # 출발일 — 예매개시 예외는 출발일 범위로 표기됨
    try:
        for ex in (open_time_exceptions() or []):
            if str(ex.get("from") or "") <= ds <= str(ex.get("to") or ""):
                h, mn = int(ex.get("hour", 7)), int(ex.get("minute", 0)); break
    except Exception:
        pass
    return datetime(y, m, min(d.day, last), h, mn)


def _job_train_rows(p: Dict[str, Any]) -> List[dict]:
    """결과 탭 '조회중인 열차' 목록.
    감시가 실제로 본 열차 > 시작할 때 보낸 정보 > 열차번호(옛 작업). 예매 화면이 열차 선택을
    강제하므로 여기가 비는 건 '시간대 전체'라서가 아니라 정보가 없을 때뿐이다."""
    if p.get("_watch_trains"):
        return p["_watch_trains"]
    if p.get("train_rows"):
        return p["train_rows"]
    if p.get("_saved_trains"):
        return p["_saved_trains"]
    nos = list(p.get("train_nos") or [])
    if not nos and p.get("fixed_train_no"):
        nos = [p["fixed_train_no"]]
    times = [str(x).ljust(6, "0") for x in (p.get("train_times") or [])]
    return [{"no": str(no), "type": "", "dep_time": (times[i] if i < len(times) else ""), "arr_time": ""}
            for i, no in enumerate(nos)]


def _opt_text(req) -> str:
    """결과 탭 카드 '좌석옵션' 요약.
    선호 좌석: '선호 좌석 (실패시 일반실 우선, 창측 우선, 순방향 우선)'
    긴급:      '긴급 (일반실 우선, 창측 우선, 순방향 우선)'
    직접 선택: '직접 선택'"""
    mode = _MODE_LABEL.get(req.seat_mode, "긴급")
    if req.seat_mode == "fixed":
        return mode
    cond = [_CLS_LABEL.get(req.seat_class, "일반실 우선"),
            _SIDE_LABEL.get(req.seat_side, "창측 우선"),
            _DIR_LABEL.get(req.seat_dir, "순방향 우선")]
    if req.allow_waiting:  cond.append("예약대기 허용")
    if req.allow_standing: cond.append("입석 허용")
    prefix = "실패시 " if req.seat_mode == "cand" else ""
    return f"{mode} ({prefix}{', '.join(cond)})"


def _shift_hhmmss(hhmm: str, mins: int) -> str:
    t = max(0, min(1439, int(hhmm[:2]) * 60 + int(hhmm[2:4]) + mins))
    return f"{t // 60:02d}{t % 60:02d}00"


def _narrow_search_window(p: Dict[str, Any], req) -> None:
    """열차를 직접 골랐으면 조회 범위를 그 열차들 구간으로 좁힌다.
    18~22시를 감시해도 고른 게 18:30 한 편뿐이면 매 사이클 4시간을 페이징할 이유가 없다
    (6편·2~3페이지 → 1페이지). 시각을 모르면 원래 범위 그대로."""
    picked = p.get("train_nos") or ([p["fixed_train_no"]] if p.get("fixed_train_no") else [])
    if not picked:
        return
    times = sorted({str(x).strip().ljust(6, "0") for x in (getattr(req, "train_times", None) or [])
                    if str(x).strip().isdigit() and len(str(x).strip()) >= 4})
    if not times:
        return
    # 정각 기준으로 호출한다(사용자 지시) — 시작은 그 시각의 정각(00분)부터
    p["search_start_hhmmss"] = times[0][:2] + "0000"
    p["search_end_min"] = int(times[-1][:2]) * 60 + int(times[-1][2:4]) + 15


def _build_params_from_req(req, mode: str, sh: str, eh: str) -> Dict[str, Any]:
    """시작 요청 → 러너 파라미터. 자동 재개에서도 그대로 쓴다."""
    option = _SEAT_OPTION.get(req.seat_class, ReserveOption.GENERAL_FIRST)
    p = {**_build_train_params(req.date, req.dep, req.arr, sh, eh, req.adults, req.interval_sec),
         "option": option, "train_type": TrainType.ALL,
         "seat_cls": "2" if str(req.seat_class).startswith("special") else "1"}
    # 결제/계정 옵션을 p에 실어 자동결제로 전달(.env 폴백은 _pay_cfg_from_params에서)
    p["auto_pay"]     = req.auto_pay
    p["pay_dryrun"]   = req.pay_dryrun
    p["allow_waiting"] = req.allow_waiting
    p["allow_standing"] = req.allow_standing
    p["max_duration_min"] = req.max_duration_min
    p["cash_receipt"] = req.cash_receipt
    p["receipt_type"] = req.receipt_type
    p["smart_ticket"] = req.smart_ticket
    p["use_mileage"]  = req.use_mileage
    p["pay_method"]   = req.pay_method
    p["card"] = {"no": req.card_no, "exp": req.card_exp, "pw": req.card_pw,
                 "auth_kind": req.card_auth_kind, "auth_val": req.card_auth_val,
                 "installment": req.card_installment}
    if req.pay_pin.strip():    p["pay_pin"]   = req.pay_pin.strip()
    if req.phone.strip():      p["phone"]     = req.phone.strip()
    if req.member_no.strip():  p["member_no"] = req.member_no.strip()
    if req.password.strip():   p["password"]  = req.password
    # 현금영수증 신청 시 휴대폰번호 필수(자동결제 켜진 경우)
    p["mode"] = mode if mode in MODES else "urgent"
    p["train_nos"] = [str(x).strip() for x in (req.train_nos or []) if str(x).strip()]
    p["one_shot"] = bool(req.one_shot)
    p["watch_seats"] = [x for x in (req.watch_seats or []) if isinstance(x, dict) and x.get("car_no") and x.get("seat_no")]
    p["open_burst"] = bool(req.open_burst)
    p["label"] = (req.label or "").strip()[:40]
    p["ncard"] = re.sub(r"\D", "", req.ncard_no) if req.use_ncard else ""
    p["seat_mode"] = req.seat_mode if req.seat_mode in ("pref", "fixed", "cand") else "none"
    p["cand_seats"] = [x for x in (req.cand_seats or []) if isinstance(x, dict) and x.get("car_no") and x.get("seat_no")][:10]
    p["cand_templates"] = {str(g): [x for x in (v or []) if isinstance(x, dict) and x.get("car_no") and x.get("seat_no")][:10]
                           for g, v in (req.cand_templates or {}).items()}
    p["cand_fallback"] = "auto" if req.cand_fallback == "auto" else "watch"
    p["cand_label"] = (req.cand_label or "").strip()[:40]
    _SIDES = ("window", "aisle", "window_only", "aisle_only")
    _DIRS = ("fwd", "rev", "fwd_only", "rev_only")
    p["seat_prefs"] = {"side": req.seat_side if req.seat_side in _SIDES else "window",
                       "dir": req.seat_dir if req.seat_dir in _DIRS else ("fwd" if req.seat_forward else "")}
    p["fixed_train_no"] = req.fixed_train_no.strip()
    p["fixed_seats"] = [x for x in (req.fixed_seats or []) if isinstance(x, dict) and x.get("car_no") and x.get("seat_no")]
    p["fixed_fallback"] = req.fixed_fallback
    p["opt_text"] = _opt_text(req)
    p["train_times"] = [str(x) for x in (req.train_times or []) if str(x).strip().isdigit()]
    p["train_rows"] = [{"no": str(x.get("no") or ""), "type": str(x.get("type") or ""),
                        "dep_time": str(x.get("dep_time") or ""), "arr_time": str(x.get("arr_time") or "")}
                       for x in (req.train_rows or []) if isinstance(x, dict) and x.get("no")][:20]
    _narrow_search_window(p, req)
    return p


@app.post("/api/start")
async def api_start(request: Request, mode: str = "urgent", req: Optional[TrainStartRequest] = None):
    _t0 = time.time()   # 시작 버튼 → 결과 카드까지의 지연이 서버인지 화면인지 가르는 기준점
    st = _ktx_state(request, mode)
    if st.running:
        return {"ok": False, "error": "이미 감시 중입니다" if st.phase != "scheduled" else "이미 예약된 감시가 있습니다"}
    if req is None:   # 결과 탭의 '재개' — 저장해 둔 시작 요청을 그대로 다시 쓴다
        if not st.req:
            return {"ok": False, "error": "예매 탭에서 다시 등록하세요"}
        req = TrainStartRequest(**st.req)
    sh, eh = req.start_hhmm.zfill(4), req.end_hhmm.zfill(4)
    err = _validate_train_request(req.dep.strip(), req.arr.strip(), req.date, req.adults, req.interval_sec, sh, eh)
    if err:
        return {"ok": False, "error": err}
    start_at = None
    if req.start_at.strip():
        try:
            target = datetime.fromisoformat(req.start_at.strip())
        except ValueError:
            return {"ok": False, "error": f"시작 시각 형식 오류: {req.start_at}"}
        if (target - datetime.now()).total_seconds() > 5:
            start_at = target.replace(microsecond=0).isoformat()
    p = _build_params_from_req(req, mode, sh, eh)
    err2 = _validate_start_req(req, p)
    if err2:
        return {"ok": False, "error": err2}
    if start_at:
        p["start_at"] = start_at
    st.params = p
    st.req = req.model_dump()
    st.task = asyncio.create_task(runner_loop(st, p, getattr(request.state, "sid", None), _job_id(mode)))
    _save_jobs()
    logger.info(f"[시작요청] 접수 {(time.time() - _t0) * 1000:.0f}ms")
    return {"ok": True, "start_at": start_at}


@app.get("/api/runners")
async def api_runners(request: Request):
    """이 계정에서 감시를 돌릴 수 있는 서버들(결과 탭 '어디서 이어서 실행할지' 선택용)."""
    sid = getattr(request.state, "sid", "") or ""
    email = sid[2:] if sid.startswith("u:") else ""
    if LOCAL_MODE:
        rs = [{"runner": _runner_id(), "label": _runner_label(), "online": True, "here": True}]
        for x in _hub_runners_cache.get("runners") or []:
            if x.get("runner") != _runner_id():
                rs.append({**x, "here": False})
        return {"ok": True, "runners": rs}
    return {"ok": True, "runners": _runners_for(email) if email else
            [{"runner": "web", "label": "웹", "online": True, "here": True}]}


@app.post("/api/jobs/run")
async def api_jobs_run(request: Request, mode: str = "", runner: str = ""):
    """지정한 서버에서 이어서 실행. 그 서버가 이 서버가 아니면 지시만 남기고 그쪽이 집어간다."""
    sid = getattr(request.state, "sid", "") or ""
    email = sid[2:] if sid.startswith("u:") else ""
    body = {}
    try:
        body = await request.json()
    except Exception:
        pass
    job = _job_id(mode)
    if not runner or runner == _runner_id():
        return await _run_command_here(sid, job, "resume", body)
    if not email:
        return {"ok": False, "error": "로그인이 필요합니다"}
    if LOCAL_MODE:   # exe → 중앙에 지시를 맡긴다
        j = await _hub_call("POST", "/api/device/command",
                            {"job": job, "runner": runner, "cmd": "resume", "body": body})
        return j or {"ok": False, "error": "웹에 연결할 수 없습니다"}
    _cmd_push(email, job, runner, "resume", body)
    return {"ok": True, "queued": True}


@app.post("/api/stop")
async def api_stop(request: Request, mode: str = "urgent"):
    st = _ktx_state(request, mode)
    if st.task and not st.task.done():
        st.task.cancel()
    st.running = False; st.task = None
    st.phase = "idle"; st.scheduled_at = None
    st.status_text = "사용자가 중단함"
    tok = CUR_MODE.set(_job_id(mode))
    try:
        logger.info("■ 사용자가 결과 탭에서 중단했습니다")
    finally:
        CUR_MODE.reset(tok)
    _save_jobs()
    return {"ok": True}


@app.get("/api/status")
async def api_status(request: Request, mode: str = "urgent"):
    return _state_to_dict(_ktx_state(request, mode))


@app.get("/api/jobs")
async def api_jobs(request: Request):
    """이 세션의 감시 작업 목록(결과 탭). 예매 탭에서 여러 개를 동시에 돌릴 수 있다."""
    out = []
    for job, st in _session_jobs(request):
        p = st.params or {}
        out.append({
            "job": job,
            "running": bool(st.running),
            "phase": st.phase,
            "status_text": st.status_text,
            "label": p.get("label") or "",
            "dep": p.get("dep"), "arr": p.get("arr"),
            "date": p.get("date_yyyymmdd"),
            "start_hhmm": p.get("start_hhmm"), "end_hhmm": p.get("end_hhmm"),
            "adults": p.get("adults"),
            "attempts": st.attempts,
            "last_check_at": st.last_check_at,
            "next_delay_sec": st.next_delay_sec,
            "last_error": st.last_error,
            "result": st.result,
            "holiday_block": bool(getattr(st, "holiday_block", False)),
            "opt_text": p.get("opt_text") or "",
            "expired": bool(getattr(st, "expired", False)),
            "resumable": bool(not st.running and not st.result and not getattr(st, "expired", False)
                              and "중단" in (st.status_text or "")),
            "scheduled": bool(st.phase == "scheduled" or st.await_open
                              or (p.get("start_at") and not st.last_check_at)),
            "trains": _job_train_rows(p),
            "won_train": (st.result or {}).get("train_no") if st.result else None,
            "pay_state": getattr(st, "pay_state", None),   # 완료 카드 한 줄 요약(예매/결제 성공·실패)
            "auto_pay": bool((st.params or {}).get("auto_pay")),
            "pay_method": (st.params or {}).get("pay_method") or "toss",
            "remote": False,
            "started_ts": st.started_ts,   # 결과 탭 정렬: 최근 시작이 맨 위
            # 예약 카드의 접힘 버튼 문구 "○년 ○월 ○일 HH:MM 시작 예정" — 사용자가 정한 시각이 없으면 예매 개시 시각
            "start_at": p.get("start_at") or ((_open_at_for(p.get("date_yyyymmdd") or "") or datetime.min).isoformat()
                                             if st.await_open and _open_at_for(p.get("date_yyyymmdd") or "") else None),
        })
    for x in out:   # 이 서버에서 도는 작업
        x.setdefault("runner", _runner_id()); x.setdefault("runner_label", _runner_label())
        x["runner_online"] = True
    have = {x["job"] for x in out}
    if LOCAL_MODE:   # exe — 웹에서 도는 내 감시도 함께(배경 루프가 채운 사본)
        out += [x for x in _hub_jobs_cache["jobs"] if x.get("job") not in have]
    else:            # 웹 — 다른 PC(exe)에서 도는 내 감시도 함께
        sid = getattr(request.state, "sid", "") or ""
        if sid.startswith("u:"):
            out += [x for x in _rjobs_list(sid[2:], exclude_runner=_runner_id()) if x.get("job") not in have]
    return {"ok": True, "jobs": out}


@app.delete("/api/jobs")
async def api_job_delete(request: Request, mode: str = ""):
    """끝난 작업을 목록에서 지운다(실행 중이면 먼저 중단)."""
    sid = getattr(request.state, "sid", None) or "default"
    key = f"{sid}:{_job_id(mode)}"
    st = KTX_SESSIONS.get(key)
    if st is None:
        return {"ok": False, "error": "없는 작업입니다"}
    if st.task and not st.task.done():
        st.task.cancel()
    KTX_SESSIONS.pop(key, None)
    _save_jobs()
    return {"ok": True}


# '조회 대상 열차' 미리보기 — 날짜 선택 시 하루 전체를 병렬로 1회 조회(브라우저가 24시간 캐시). 서버 캐시 없음.
_WINDOW_CACHE: Dict[str, tuple] = {}

@app.get("/api/trains")
async def api_trains(dep: str = "", arr: str = "", date: str = "", start: str = "", end: str = ""):
    dep, arr, date = dep.strip(), arr.strip(), date.strip()
    if not dep or not arr or not (len(date) == 8 and date.isdigit()):
        return {"ok": False, "trains": [], "error": "dep/arr/date(yyyymmdd) 필요"}
    ce = _cooling_error()
    if ce: return {**ce, "trains": []}
    loop = asyncio.get_event_loop()
    sh, eh = re.sub(r"\D", "", start)[:4], re.sub(r"\D", "", end)[:4]
    if sh:   # 시작시각 조회 — end 없으면 시작부터 10편(1페이지=1콜), end 있으면 그 구간. 20초 캐시.
        wkey = f"{dep}|{arr}|{date}|{sh}|{eh or 'p1'}"
        whit = _WINDOW_CACHE.get(wkey)
        if whit and time.time() - whit[0] < 20:
            _kmod.log_cache_hit("열차 조회", f"{dep}→{arr} {date} {sh} · 서버 캐시(20초)")
            return whit[1]
        try:
            if eh:
                wr = await loop.run_in_executor(_executor, lambda: list_trains(dep, arr, date, sh + "00", eh + "00"))
            else:   # 끝시각 없이 시작부터 1페이지(10편)
                wr = await loop.run_in_executor(_executor, lambda: list_trains(dep, arr, date, sh + "00", "235900", max_pages=1))
        except Exception as e:
            wr = {"ok": False, "trains": [], "error": str(e)}
        if wr.get("ok") and wr.get("trains"):
            wr["fetched_at"] = time.time(); wr["window"] = True
            _WINDOW_CACHE[wkey] = (time.time(), wr)
            if len(_WINDOW_CACHE) > 120: _WINDOW_CACHE.pop(next(iter(_WINDOW_CACHE)))
            return wr
        # 창(ScheduleView 1콜)이 비었다 = 예매 전·명절. 하루 전체(3슬라이스)도 똑같이 막히므로
        # list_trains_day를 건너뛰고 바로 시각표 미리보기로 간다(이전 버그: 헛호출 4번). 2026-09-09
        res = {"ok": False, "trains": []}
    else:
        try:
            res = await loop.run_in_executor(_executor, list_trains_day, dep, arr, date)
        except Exception as e:
            res = {"ok": False, "trains": [], "error": str(e)}
    # 아직 예매가 안 열린(또는 명절로 막힌) 날짜는 시각표로 목록을 미리 만들어 준다 — 오픈런에서 대상 열차를 골라두기 위해
    if not res.get("ok") or not res.get("trains"):
        pkey = f"{dep}|{arr}|{date}"
        phit = _PREVIEW_CACHE.get(pkey)
        if phit and time.time() - phit[0] < 600:
            _kmod.log_cache_hit("열차 조회", f"{dep}→{arr} {date} · 예매 전 미리보기 캐시(10분)")
            return phit[1]
        try:
            # 코레일 공식 KTX 시각표 엑셀로 먼저 만든다(파일 1개·7일 캐시 → 사실상 0콜).
            # 그 파일에 없는 노선(ITX·무궁화 전용 등)만 기준일 하루 조회로 폴백한다(약 17콜).
            pv = await loop.run_in_executor(_executor, trains_from_timetable, dep, arr, date)
            if not (pv.get("ok") and pv.get("trains")):
                pv = await loop.run_in_executor(_executor, list_trains_preview, dep, arr, date)
            if pv.get("ok") and pv.get("trains"):
                pv["fetched_at"] = time.time()
                src = "공식 시각표" if pv.get("source") == "timetable" else f"기준일 {pv.get('ref_date')}"
                logger.info(f"[열차목록] {date} {dep}→{arr} 예매 전 — {len(pv['trains'])}편 ({src})")
                _PREVIEW_CACHE[pkey] = (time.time(), pv)
                return pv
        except Exception as e:
            logger.warning(f"열차목록 미리 조회 실패 · {e}")
    res["fetched_at"] = time.time()
    return res


# ── 코레일 공지사항: 웹 게시판 API(www.korail.com/com/userBoard.do, 로그인 없음) + 메인 긴급공지 ──
# 파일에 저장해 재시작해도 남기고, 화면에는 항상 캐시를 먼저 준다. 1시간 지났으면
# 응답을 돌려준 뒤 뒤에서 갱신한다(stale-while-revalidate) — 알림 창이 멈춰 보이지 않게.
_NOTICE_CACHE: Dict[str, Any] = {"ts": 0, "data": None}
_NOTICE_VIEW: Dict[str, Any] = {}
_NOTICE_TTL = 3600
_NOTICE_FILE = data_path(".notices.json")
_NOTICE_LOCK = threading.Lock()


def _notice_disk_load() -> None:
    try:
        with open(_NOTICE_FILE, encoding="utf-8") as f:
            d = _json.load(f)
        if isinstance(d, dict) and d.get("data"):
            _NOTICE_CACHE.update(ts=float(d.get("ts") or 0), data=d["data"])
            _NOTICE_VIEW.update(d.get("views") or {})
            logger.info(f"공지 캐시 복원 · {len((d['data'].get('items') or []))}건 · 본문 {len(_NOTICE_VIEW)}건")
    except Exception:
        pass


def _notice_disk_save() -> None:
    try:
        tmp = _NOTICE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _json.dump({"ts": _NOTICE_CACHE["ts"], "data": _NOTICE_CACHE["data"],
                        "views": dict(list(_NOTICE_VIEW.items())[-60:])}, f, ensure_ascii=False)
        os.replace(tmp, _NOTICE_FILE)
    except Exception as e:
        logger.warning(f"공지 캐시 저장 실패: {e}")


def _notice_refresh() -> dict:
    """실제 조회 + 캐시 갱신. 동시에 여러 번 들어와도 한 번만 나간다."""
    if not _NOTICE_LOCK.acquire(blocking=False):
        return _NOTICE_CACHE["data"] or {"ok": False, "items": []}
    try:
        data = _fetch_notices()
        if data.get("ok"):
            _NOTICE_CACHE.update(ts=time.time(), data=data)
            _notice_disk_save()
        return data
    finally:
        _NOTICE_LOCK.release()
def _strip_html(h: str) -> str:
    import html as _html
    t = re.sub(r"<br\s*/?>|</p>|</div>|</li>", "\n", h or "", flags=re.I)
    t = re.sub(r"<[^>]+>", " ", t)
    t = _html.unescape(t)
    t = re.sub(r"[ \t\r\f\v]+", " ", t)
    return re.sub(r"\n\s*\n+", "\n", t).strip()
_notice_disk_load()   # 재시작해도 공지가 바로 뜨도록 복원


def _fetch_notices() -> dict:
    import requests as _rq
    H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
         "Referer": "https://www.korail.com/ticket/guest/notice"}
    out = {"ok": True, "items": [], "emergency": [], "fetched_at": time.time()}
    try:
        j = _rq.get("https://www.korail.com/com/userBoard.do", params={"schBcid": "ticketNotice", "mode": "list"}, headers=H, timeout=15).json()
        for x in (j.get("boardList") or [])[:30]:
            out["items"].append({"id": x.get("bdIdx"), "title": (x.get("bdTitle") or "").strip(), "date": x.get("regdt") or "",
                                 "pinned": str(x.get("bdNotice")) == "1", "text": _strip_html(x.get("bdContent") or "")[:4000]})
    except Exception as e:
        out["ok"] = False; out["error"] = f"공지 조회 실패: {e}"
    try:
        e = _rq.get("https://www.korail.com/com/userMainEmerNotice.do", headers=H, timeout=10).json()
        for x in (e.get("userMainEmerNotice") or [])[:5]:
            if isinstance(x, dict):
                out["emergency"].append({"title": _strip_html(x.get("bdTitle") or x.get("title") or "")[:200], "text": _strip_html(x.get("bdContent") or x.get("content") or "")[:2000], "raw_keys": sorted(x.keys())[:12]})
    except Exception:
        pass
    return out

def _fetch_notice_view(bd_idx: str) -> dict:
    import requests as _rq
    H = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36", "Referer": "https://www.korail.com/ticket/guest/notice"}
    j = _rq.get("https://www.korail.com/com/userBoard.do", params={"schBcid": "ticketNotice", "mode": "view", "bdIdx": bd_idx}, headers=H, timeout=15).json()
    v = j.get("boardView") or {}
    c = v.get("bdContent") or ""
    # 코레일 공지는 대부분 이미지 한 장(에디터 첨부) — 이미지 URL도 같이 돌려준다
    imgs = [(u if u.startswith("http") else "https://www.korail.com" + u) for u in re.findall(r'<img[^>]+src="([^"]+)"', c, flags=re.I)]
    return {"ok": bool(v), "id": bd_idx, "title": (v.get("bdTitle") or "").strip(), "date": v.get("regdt") or "", "text": _strip_html(c)[:8000], "images": imgs[:10]}

@app.get("/api/notices/{bd_idx}")
async def api_notice_view(bd_idx: str):
    bd_idx = re.sub(r"\D", "", bd_idx)
    if not bd_idx:
        return {"ok": False, "error": "id 필요"}
    if bd_idx in _NOTICE_VIEW:
        return _NOTICE_VIEW[bd_idx]
    loop = asyncio.get_event_loop()
    try:
        d = await loop.run_in_executor(_executor, _fetch_notice_view, bd_idx)
    except Exception as e:
        return {"ok": False, "error": f"공지 본문 조회 실패: {e}"}
    if d.get("ok"):
        _NOTICE_VIEW[bd_idx] = d      # 공지 본문은 바뀌지 않는다 — 받으면 계속 재사용
        _notice_disk_save()
    return d

_MYINFO_CACHE: Dict[str, tuple] = {}

_MYTICKETS_CACHE: Dict[str, tuple] = {}
_HISTORY_CACHE: Dict[str, tuple] = {}
_PAYKEYS_CACHE: Dict[str, tuple] = {}

# 데모 페이지가 심어두는 가짜 계정 — 코레일에 절대 보내지 않는다(로그인 실패 폭주·계정 잠금 위험)
_DEMO_ACCT = ("1234567890", "demo-pass")


def _is_demo_acct(member_no: str, password: str) -> bool:
    return (str(member_no).strip(), password) == _DEMO_ACCT


def _acct_for_request(request):
    """이 요청을 보낸 구글 계정에 저장된 코레일 자격증명.
    자격증명을 안 받는 조회(좌석맵 등)가 프로세스 기본 계정(=소유자)으로 나가면
    다른 사용자의 조회가 소유자 계정으로 섞인다. 요청자 본인 것만 쓴다(2026-09-09)."""
    email = _session_email(request) if _OAUTH_ON else None
    key = f"u:{email}" if email else _SYNC_KEY
    try:
        acc = _json.loads((_sync_get(key) or {}).get("kor_ktx_account") or "{}")
    except Exception:
        return None, None
    mno = (acc.get("memberNo") or "").strip()
    pw = acc.get("password") or ""
    return (mno or None), (pw or None)


def _acct_from(body: dict):
    return (body.get("member_no") or "").strip(), body.get("password") or ""

@app.post("/api/mytickets")
async def api_mytickets(request: Request):
    """내 티켓 탭 — 미결제 예약 + 발권 승차권 + N카드. 180초 캐시(force=true면 무시)."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    member_no, password = _acct_from(body)
    if not member_no or not password:
        return {"ok": False, "error": "계정 정보가 없습니다(설정에서 계정 등록)"}
    if _is_demo_acct(member_no, password):
        return {"ok": False, "error": "데모 계정입니다(코레일 조회 안 함)"}
    hit = _MYTICKETS_CACHE.get(member_no)
    if hit and not body.get("force") and time.time() - hit[0] < 180:
        return hit[1]
    loop = asyncio.get_event_loop()
    def work():
        return _ui_call(member_no, password, lambda k: {"ok": True, **my_tickets(k)})
    try:
        res = await loop.run_in_executor(_executor, work)
    except Exception as e:
        return {"ok": False, "error": _err_text(e)}
    _MYTICKETS_CACHE[member_no] = (time.time(), res)
    return res

@app.post("/api/paykeys")
async def api_paykeys(request: Request):
    """코레일 계정에 등록된 간편결제 수단 — 결제 정보 화면의 토스페이 안내에 쓴다."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    member_no, password = _acct_from(body)
    if not member_no or not password:
        return {"ok": False, "error": "계정 정보가 없습니다(설정에서 계정 등록)"}
    if _is_demo_acct(member_no, password):
        return {"ok": False, "error": "데모 계정입니다(코레일 조회 안 함)"}
    hit = _PAYKEYS_CACHE.get(member_no)
    if hit and not body.get("force") and time.time() - hit[0] < 120:
        return hit[1]
    loop = asyncio.get_event_loop()
    def work():
        return _ui_call(member_no, password, lambda k: {"ok": True, **simple_pay_keys(k)})
    try:
        res = await loop.run_in_executor(_executor, work)
    except Exception as e:
        return {"ok": False, "error": _err_text(e)}
    _PAYKEYS_CACHE[member_no] = (time.time(), res)
    return res


@app.post("/api/history/presets")
async def api_history_presets(request: Request):
    """과거 3개월 이용내역 → 자주 탄 (구간·요일·시간대) 클러스터. 온보딩과 프리셋 '자동 등록'이 쓴다."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    member_no, password = _acct_from(body)
    if not member_no or not password:
        return {"ok": False, "error": "계정 정보가 없습니다(설정에서 계정 등록)"}
    if _is_demo_acct(member_no, password):
        return {"ok": False, "error": "데모 계정입니다(코레일 조회 안 함)"}
    hit = _HISTORY_CACHE.get(member_no)
    if hit and not body.get("force") and time.time() - hit[0] < 300:
        return hit[1]
    loop = asyncio.get_event_loop()
    def work():
        return _ui_call(member_no, password, lambda k: {"ok": True, **history_presets(k)})
    try:
        res = await loop.run_in_executor(_executor, work)
    except Exception as e:
        return {"ok": False, "error": _err_text(e)}
    _HISTORY_CACHE[member_no] = (time.time(), res)
    return res


_UI_KORAIL: Dict[str, tuple] = {}
_UI_LOGIN_LOCK = threading.Lock()
_UI_SESSION_TTL = 600


def _ui_korail(member_no: str, password: str, fresh: bool = False):
    """내 정보·내 티켓·취소용 코레일 세션(계정별 10분 재사용, 로그인은 한 번에 하나씩)."""
    with _UI_LOGIN_LOCK:
        hit = _UI_KORAIL.get(member_no)
        if hit and not fresh and time.time() - hit[0] < _UI_SESSION_TTL:
            return hit[1]
        k = _make_korail(member_no, password)
        _UI_KORAIL[member_no] = (time.time(), k)
        return k


def _err_text(e, prefix: str = "조회 실패") -> str:
    """사용자에게 보일 오류 문구. 로그인 문제는 그 문장 자체가 안내라 접두사를 붙이지 않는다."""
    code = str(getattr(e, "code", "") or "")
    if code.startswith("LOGIN"):
        return str(getattr(e, "msg", "") or e)
    return f"{prefix}: {e}"


def _ui_call(member_no: str, password: str, fn):
    """세션이 만료됐으면(P058 등) 한 번만 다시 로그인해서 재시도."""
    try:
        return fn(_ui_korail(member_no, password))
    except Exception as e:
        code = str(getattr(e, "code", "") or "")
        if code in ("LOGIN_PW", "LOGIN_ID", "LOGIN_ID_FORMAT", "LOGIN_COOLDOWN", "LOGIN_FAIL"):
            raise   # 자격증명이 틀린 것 — 다시 로그인해도 같고, 실패 횟수만 두 배로 쌓인다(2026-09-10)
        if "P058" not in str(e) and "세션" not in str(e):
            raise
        return fn(_ui_korail(member_no, password, fresh=True))


@app.post("/api/ticketdetail")
async def api_ticketdetail(request: Request):
    """승차권 1장의 좌석 상세(지연 로딩) — 내 티켓 카드를 탭할 때만 조회. 계정별 세션 재사용."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    member_no, password = _acct_from(body)
    if not member_no or not password or _is_demo_acct(member_no, password):
        return {"ok": False, "error": "계정 정보가 없습니다"}
    loop = asyncio.get_event_loop()
    def work():
        return _ui_call(member_no, password, lambda k: {"ok": True, **ticket_detail(
            k, body.get("wct", ""), body.get("dt", ""), body.get("sqno", ""), body.get("pwd", ""))})
    try:
        return await loop.run_in_executor(_executor, work)
    except Exception as e:
        return {"ok": False, "error": str(e)[:80]}


@app.post("/api/reservations/cancel")
async def api_reservation_cancel(request: Request):
    """미결제 예약 취소(pnr). 성공 시 내 티켓 캐시를 비운다."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    member_no, password = _acct_from(body)
    pnr = (body.get("pnr") or "").strip()
    if not member_no or not password or not pnr:
        return {"ok": False, "error": "계정/예약번호가 없습니다"}
    if _is_demo_acct(member_no, password):
        return {"ok": False, "error": "데모 계정입니다(코레일 조회 안 함)"}
    loop = asyncio.get_event_loop()
    def work():
        def _do(k):
            target = next((r for r in k.reservations() if r.rsv_id == pnr), None)
            if target is None:
                return {"ok": False, "error": "해당 예약이 없습니다(이미 취소/결제됨)"}
            cancel_reservation(k, target)
            return {"ok": True}
        return _ui_call(member_no, password, _do)
    try:
        res = await loop.run_in_executor(_executor, work)
    except Exception as e:
        return {"ok": False, "error": _err_text(e, "취소 실패")}
    if res.get("ok"):
        _MYTICKETS_CACHE.pop(member_no, None)
        logger.info(f"[내 티켓] 예약 취소: {pnr}")
    return res

@app.post("/api/myinfo")
async def api_myinfo(request: Request):
    """내 정보(이름·포인트·연락처) — 저장된 KTX 계정으로 모바일 로그인 후 조회. 60초 캐시. nodriver 불필요."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    member_no = (body.get("member_no") or "").strip()
    password = body.get("password") or ""
    if not member_no or not password:
        return {"ok": False, "error": "계정 정보가 없습니다(설정에서 계정 등록)"}
    if _is_demo_acct(member_no, password):
        return {"ok": False, "error": "데모 계정입니다(코레일 조회 안 함)"}
    hit = _MYINFO_CACHE.get(member_no)
    if hit and time.time() - hit[0] < 60:
        return hit[1]
    loop = asyncio.get_event_loop()
    def work():
        def _do(k):
            res = {"ok": True, **my_info(k)}
            try:
                res["ncards"] = list_ncards(k)   # 보유 N카드(내부 카드번호·구간·유효기간·미사용횟수)
            except Exception as e:
                res["ncards"] = []; res["ncards_error"] = str(e)[:80]
            return res
        return _ui_call(member_no, password, _do)
    try:
        res = await loop.run_in_executor(_executor, work)
    except Exception as e:
        return {"ok": False, "error": _err_text(e)}
    _MYINFO_CACHE[member_no] = (time.time(), res)
    return res


@app.get("/api/notices")
async def api_notices(force: int = 0):
    loop = asyncio.get_event_loop()
    cached = _NOTICE_CACHE["data"]
    fresh = cached and time.time() - _NOTICE_CACHE["ts"] < _NOTICE_TTL
    if cached and fresh and not force:
        return cached
    if cached and not force:
        # 오래됐지만 있는 건 바로 주고, 갱신은 뒤에서 — 창이 기다리지 않게
        loop.run_in_executor(_executor, _notice_refresh)
        return cached
    return await loop.run_in_executor(_executor, _notice_refresh)


# ── 좌석 지정(고급): 호차 목록 / 좌석맵 — 로그인 없이 조회 ──
_FIND_TRAIN_CACHE: Dict[str, tuple] = {}
_FIND_TRAIN_LOCKS: Dict[str, 'threading.Lock'] = {}
_FIND_TRAIN_LOCKS_GUARD = threading.Lock()

def _find_train(dep: str, arr: str, date: str, train_no: str, dep_time: str, allow_ref: bool = True):
    """열차 객체를 찾는다. 예매가 열리지 않은 날짜면 **같은 열차번호가 다니는 가까운 예매 가능일**로 대신 찾는다.
    좌석 배치는 날짜가 아니라 편성(car_tp_cd)으로 정해지므로(2026-09-07 실측: 같은 열차·다른 날짜 배치 동일),
    오픈런에서도 좌석을 미리 고를 수 있다. 두 번째 반환값이 True면 대체 날짜로 본 '배치 미리보기'다."""
    from korail2.korail2 import Korail as _K
    k = _K("", "", auto_login=False)
    _kmod._guard_session(k._session)   # 호출 로그·과호출 관문을 거치게 한다(이전엔 이 조회가 집계·제한 밖이었다, QA 2026-09-10)
    t = re.sub(r"\D", "", dep_time)[:6].ljust(6, "0")
    # 같은 열차를 호차 목록·좌석맵·호차 전환마다 다시 조회하지 않는다(10분 캐시, QA 실측: 직접 선택 1회에 열차 조회 2콜).
    # 열차 객체는 편성·역 순번 등 예매 무관 정보라 잠깐 묵혀도 안전하다(잔여석은 좌석맵이 따로 본다).
    ckey = f"{dep}|{arr}|{date}|{train_no}|{t}"
    hit = _FIND_TRAIN_CACHE.get(ckey)
    if hit and time.time() - hit[0] < 600:
        return k, hit[1], hit[2]
    # 같은 열차를 동시에 두 번 찾으면(빠른 재선택으로 /api/cars 가 겹칠 때) 뒤 요청은 앞 조회를 기다렸다 캐시를 쓴다
    with _FIND_TRAIN_LOCKS_GUARD:
        lock = _FIND_TRAIN_LOCKS.setdefault(ckey, threading.Lock())
    with lock:
        hit = _FIND_TRAIN_CACHE.get(ckey)
        if hit and time.time() - hit[0] < 600:
            if hit[1] is None:   # 최근 60초 안에 못 찾은 열차 — 기준일 탐색(최대 7콜)을 되풀이하지 않는다
                if time.time() - hit[0] < 60:
                    raise ValueError(f"열차 {train_no}({dep_time[:4]})를 찾지 못했습니다")
            else:
                return k, hit[1], hit[2]
        try:
            return _find_train_uncached(k, ckey, dep, arr, date, train_no, t, allow_ref)
        except ValueError:
            _FIND_TRAIN_CACHE[ckey] = (time.time(), None, False)
            raise


# 예매 전·명절 날짜 → 실제로 조회된 기준일 (날짜별 10분 기억). 같은 날짜의 다음 열차는 실패할 직접 조회와
# 중간 후보일을 건너뛰고 바로 그 기준일로 간다(이전: 열차마다 최대 1+6콜 탐색 — QA 개선 2026-09-10).
_REF_DATE_HINT: Dict[str, tuple] = {}

def _find_train_uncached(k, ckey, dep, arr, date, train_no, t, allow_ref):
    def _try(d):
        trains, _ = search_pages(k, dep, arr, d, t, hhmmss_to_min(t) + 1, max_pages=1, page_sleep=0.1)
        return next((x for x in trains if x.train_no == train_no), None)
    hint = _REF_DATE_HINT.get(date)
    hint_ref = hint[1] if hint and time.time() - hint[0] < 600 else None
    if not hint_ref:   # 이 날짜가 열려 있는지 모르면 먼저 직접 조회
        try:
            tr = _try(date)
        except Exception:
            tr = None
        if tr is not None:
            _FIND_TRAIN_CACHE[ckey] = (time.time(), tr, False)
            if len(_FIND_TRAIN_CACHE) > 200: _FIND_TRAIN_CACHE.pop(next(iter(_FIND_TRAIN_CACHE)))
            return k, tr, False
    if allow_ref:
        target = datetime.strptime(date, "%Y%m%d").date()
        today = datetime.now().date()
        cands = []
        if hint_ref:
            cands.append(hint_ref)
        # 예매 창(오늘+30일) 밖이면 창 안으로 들어오는 첫 같은 요일부터 본다 — 사이 날짜는 어차피 예매 전
        first_back = 1
        limit = today + timedelta(days=30)
        while target - timedelta(days=7 * first_back) > limit and first_back < 6:
            first_back += 1
        for back in range(first_back, 7):
            cand = target - timedelta(days=7 * back)
            if cand <= today:
                break
            cands.append(cand.strftime("%Y%m%d"))
        for d in dict.fromkeys(cands):
            try:
                tr = _try(d)
            except Exception:
                tr = None
            if tr is not None:
                _FIND_TRAIN_CACHE[ckey] = (time.time(), tr, True)
                _REF_DATE_HINT[date] = (time.time(), d)
                return k, tr, True
    raise ValueError(f"열차 {train_no}({dep_time[:4]})를 찾지 못했습니다")

_CARS_CACHE: Dict[str, tuple] = {}
_PREVIEW_CACHE: Dict[str, tuple] = {}

def _resolve_watch_train(korail, p: Dict[str, Any]):
    """지정 좌석 감시 대상 열차를 좌석 유무와 무관하게 찾아온다.
    일반 후보 조회(_build_candidates)는 '지금 예매 가능한' 열차만 남기므로, 감시의 대상인
    매진 열차가 후보에서 통째로 빠져 좌석맵을 보지도 못하는 문제가 있었다(2026-09-07)."""
    trains, _ = search_pages(korail, p["dep"], p["arr"], p["date_yyyymmdd"],
                             p["start_hhmmss"], hhmmss_to_min(p["end_hhmmss"]))
    return next((t for t in trains if t.train_no == p.get("fixed_train_no")), None)


def _cooling_error():
    """과호출 보호로 전면 대기 중이면 UI 조회는 기다리지 말고 바로 알려준다(러너는 계속 대기)."""
    st = GATE.status()
    if st["cooling"] > 0:
        return {"ok": False, "error": f"코레일 과호출 보호 중 — {st['cooling']}초 후 자동 재개", "cooling": st["cooling"]}
    return None

@app.get("/api/events")
async def api_events(limit: int = 100):
    """시스템 기록(알림 탭) — 예매 시각 변경·시각표 개정·명절·호출 차단 등 사람이 알아야 할 변화."""
    evs = list_events(max(1, min(300, limit)))
    return {"ok": True, "events": evs, "unseen": sum(1 for e in evs if not e.get("seen"))}


@app.post("/api/events/seen")
async def api_events_seen():
    return {"ok": True, "marked": mark_events_seen()}


@app.get("/api/gate")
async def api_gate():
    """코레일 호출 사용량·차단 상태(과호출 방어 관문)."""
    return {"ok": True, **GATE.status()}

@app.get("/api/cars")
async def api_cars(dep: str = "", arr: str = "", date: str = "", train_no: str = "", dep_time: str = "", seat_class: str = "1", count: int = 1, full: int = 0):
    ce = _cooling_error()
    if ce: return {**ce, "cars": []}
    ckey = f"{dep}|{arr}|{date}|{train_no}|{dep_time}|{count}|{full}"
    hit = _CARS_CACHE.get(ckey)
    if hit and time.time() - hit[0] < 60:
        _kmod.log_cache_hit("호차 목록", f"{train_no}편 {dep}→{arr} · 서버 캐시(60초)")
        return hit[1]
    loop = asyncio.get_event_loop()
    def work():
        k, tr, ref = _find_train(dep.strip(), arr.strip(), date.strip(), train_no.strip(), dep_time.strip())
        # 기본은 코레일 웹과 같이 등급당 1콜(잔여석 있는 호차만). 매진 호차까지 필요한 지정 좌석 감시만 full=1(호차당 1콜).
        # full=1(지정 좌석 감시)은 호차당 1콜이라, 고른 등급만 훑어 절반으로 줄인다
        classes = (("2",) if seat_class == "2" else ("1",)) if full else ("1", "2")
        # ITX-마음·새마을·무궁화는 특실 객차가 없다 — 특실 호차 조회(1콜)를 생략한다(QA 개선 2026-09-10)
        if not str(getattr(tr, "train_type_name", "") or "").startswith("KTX") and "2" in classes and not full:
            classes = ("1",)
        out, errs = [], []
        for cls in classes:
            try:
                out += [dict(c, cls=cls) for c in list_cars(k, tr, cls, max(1, count), full=bool(full))]
            except Exception as e:
                errs.append(f"{'특실' if cls == '2' else '일반실'}: {e}")
        out.sort(key=lambda c: int(re.sub(r"\D", "", str(c.get("car_no"))) or 0))
        if ref:
            # 예매 전 날짜 — 잔여석은 알 수 없고 편성(호차 구성)만 유효하다
            for c in out:
                c["rest"] = c["total"]
        if not out:
            sold = any("ERI411321" in e for e in errs)
            return {"ok": False, "cars": [], "preview": ref,
                    "error": "이 열차는 지금 잔여석이 없습니다" if sold else ("; ".join(errs)[:150] or "호차 정보를 받지 못했습니다"),
                    "sold_out": sold}
        return {"ok": True, "cars": out, "preview": ref}
    try:
        res = await loop.run_in_executor(_executor, work)
    except Exception as e:
        return {"ok": False, "cars": [], "error": str(e)}
    if res.get("ok") and res.get("cars"):
        _CARS_CACHE[ckey] = (time.time(), res)   # 실패·빈 결과는 캐시하지 않는다
    return res

_DAYSTATUS_CACHE: Dict[str, tuple] = {}

_DAYSTATUS_FILE = data_path(".daystatus.json")


def _daystatus_disk_get(key: str):
    try:
        with open(_DAYSTATUS_FILE, encoding="utf-8") as f:
            rec = json.load(f)
        if rec.get("key") == key:
            return (rec.get("ts", 0), rec.get("value"))
    except Exception:
        pass
    return None


def _daystatus_disk_set(key: str, ts: float, value: dict) -> None:
    try:
        tmp = _DAYSTATUS_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump({"key": key, "ts": ts, "value": value}, f, ensure_ascii=False)
        os.replace(tmp, _DAYSTATUS_FILE)
    except Exception as e:
        logger.debug(f"[달력] 캐시 저장 실패: {e}")


@app.get("/api/daystatus")
async def api_daystatus(days: int = 45):
    """달력용 날짜 상태(명절 특별수송기간 / 아직 예매 전). 30분 캐시."""
    ce = _cooling_error()
    if ce: return {**ce, "days": {}}
    days = max(1, min(60, days))
    key = f"d{days}:{datetime.now().strftime('%Y%m%d')}"
    hit = _DAYSTATUS_CACHE.get(key) or _daystatus_disk_get(key)
    if hit:
        # 평소엔 6시간 캐시. 다만 명절로 막힌 날짜가 있으면 잔여석이 풀리는 순간을 놓치지 않게 30분만.
        ttl = 1800 if any(v.get("status") == "holiday" for v in (hit[1].get("days") or {}).values()) else 21600
        if time.time() - hit[0] < ttl:
            _DAYSTATUS_CACHE[key] = hit
            return hit[1]
    loop = asyncio.get_event_loop()
    try:
        cal = await loop.run_in_executor(_executor, lambda: day_calendar(days))
    except Exception as e:
        return {"ok": False, "error": str(e), "days": {}}
    try:
        exceptions = await loop.run_in_executor(_executor, open_time_exceptions)
    except Exception:
        exceptions = []
    out = {"ok": True, "days": dict(cal["days"]), "calls": cal.get("calls"), "last_open": cal.get("last_open"),
           "open_time_exceptions": exceptions}
    _DAYSTATUS_CACHE[key] = (time.time(), out)
    _daystatus_disk_set(key, time.time(), out)
    n_h = sum(1 for v in cal["days"].values() if v.get("status") == "holiday")
    logger.info(f"[달력] 예매 달력 1콜로 갱신: 명절 {n_h}일, 미오픈 {len(cal['days']) - n_h}일 (한계 {cal.get('last_open')})")
    return out

def _first_bookable_date() -> str:
    """오늘 기준 예매가 열려 있는 가까운 날짜(yyyymmdd) — 배치 샘플 조회용. 명절은 피한다."""
    cal = (_DAYSTATUS_CACHE.get(f"d45:{datetime.now().strftime('%Y%m%d')}") or (0, {}))[1].get("days") or {}
    for i in range(1, 20):
        d = datetime.now() + timedelta(days=i)
        ymd = d.strftime("%Y%m%d")
        st = (cal.get(ymd) or {}).get("status")
        if st in (None, "open"):
            return ymd
    return (datetime.now() + timedelta(days=3)).strftime("%Y%m%d")


@app.get("/docs/architecture")
async def api_docs_architecture():
    """동작 구조 문서 — 원격에서 파일을 못 여는 경우를 위해 앱에서 바로 읽는다."""
    from fastapi.responses import HTMLResponse
    path = resource_path("ARCHITECTURE.md")
    try:
        with open(path, encoding="utf-8") as f:
            md = f.read()
    except Exception as e:
        md = f"문서를 읽지 못했습니다: {e}"
    md = md.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    html = f"""<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>기차놀이 동작 구조</title>
<style>
 body {{ margin:0; background:#F2F4F6; color:#191F28;
   font-family:-apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif; }}
 .top {{ position:sticky; top:0; background:#fff; padding:14px 16px; font-weight:800; font-size:17px;
   box-shadow:0 1px 0 rgba(0,0,0,.06); display:flex; gap:10px; align-items:center; }}
 .top a {{ color:#3182F6; text-decoration:none; font-size:14px; font-weight:700; }}
 pre {{ margin:0; padding:16px 14px 60px; white-space:pre; overflow-x:auto;
   font-family:ui-monospace,SFMono-Regular,Menlo,monospace; font-size:12px; line-height:1.65; }}
</style></head><body>
<div class=top><span>동작 구조</span><a href="/">← 앱으로</a></div>
<pre>{md}</pre></body></html>"""
    return HTMLResponse(html)


@app.get("/api/layout")
async def api_layout(request: Request, train_type: str = "", car_no: str = "", seat_class: str = "1",
                     updown: str = "down", group: str = ""):
    """좌석 배치 그룹의 호차 목록 / 호차 좌석. 후보 좌석을 실열차 없이 고르기 위한 캐시.
    group(그룹 id)을 주면 그 편성으로만 샘플링한다 — 안 주면 편성이 섞인다."""
    train_type = train_type.strip()
    group = (group or "").strip()
    if group:   # 그룹에서 종류·방향을 되읽어 호출자가 틀리게 보내도 맞춘다
        for g in seat_groups().get("groups", []):
            if g["id"] == group:
                train_type, updown = g["train_type"], g["updown"]
                break
    updown = "up" if updown == "up" else "down"
    if not train_type:
        return {"ok": False, "error": "train_type 필요"}
    ce = _cooling_error()
    if ce:
        return ce
    date = _first_bookable_date()
    loop = asyncio.get_event_loop()
    # 캐시에 있으면 로그인 자체를 안 한다. 없을 때만 요청자 본인 계정으로 조회한다.
    mid, pw = _acct_for_request(request)
    def _mk():
        if not mid or not pw:
            raise RuntimeError("계정 정보가 없습니다(설정에서 코레일 계정을 먼저 등록하세요)")
        return _make_korail(mid, pw)
    try:
        if car_no:
            res = await loop.run_in_executor(
                _executor, lambda: layout_seats(_mk, train_type, car_no.zfill(4), seat_class, date, updown, group))
            if isinstance(res, list):   # 구버전 캐시 호환
                res = {"seats": res, "updown": "down"}
            return {"ok": True, **res}
        cars = await loop.run_in_executor(
            _executor, lambda: layout_cars(_mk, train_type, date, updown, group))
        return {"ok": True, "cars": cars}
    except Exception as e:
        msg = str(e)
        # 그 방향 편성에 없는 호차(중련 11~18 vs 단독 1~8) — 코레일 원문 대신 이유를 그대로 알려준다
        if "ERI411092" in msg or "ERI411321" in msg:
            return {"ok": False, "error": f"이 방향 편성에는 {int(car_no)}호차가 없습니다"}
        return {"ok": False, "error": msg[:150]}


@app.get("/api/seats")
async def api_seats(dep: str = "", arr: str = "", date: str = "", train_no: str = "", dep_time: str = "", car_no: str = "", seat_class: str = "1", count: int = 1):
    ce = _cooling_error()
    if ce: return {**ce, "seats": []}
    loop = asyncio.get_event_loop()
    def work():
        k, tr, ref = _find_train(dep.strip(), arr.strip(), date.strip(), train_no.strip(), dep_time.strip())
        m = seat_map(k, tr, car_no.strip(), "2" if seat_class == "2" else "1", max(1, count))
        if ref:
            # 배치만 유효 — 판매 전이라 잔여 여부는 의미가 없으므로 전부 고를 수 있게 연다
            for x in m.get("seats") or []:
                x["avail"] = True
        return {"ok": True, "preview": ref, **m}
    try:
        return await loop.run_in_executor(_executor, work)
    except Exception as e:
        return {"ok": False, "seats": [], "error": str(e)}


@app.get("/api/joblog")
async def api_joblog(request: Request, mode: str = ""):
    """감시별 저장 로그 — 화면을 새로 열어도 지난 기록을 그대로 보여준다."""
    sid = getattr(request.state, "sid", None) or "default"
    dq = _JOB_LOGS.get(_joblog_key(sid, _job_id(mode)))
    if dq is None:   # 다른 서버(다른 PC·웹)에서 돌던 작업 — 보관된 로그를 이어서 보여준다
        if not LOCAL_MODE and sid.startswith("u:"):
            got = _rjob_log(sid[2:], _job_id(mode))
            if got is not None:
                return {"ok": True, "lines": got, "remote": True}
        elif LOCAL_MODE:
            got = _hub_logs_cache.get(_job_id(mode))
            if got is not None:
                return {"ok": True, "lines": got, "remote": True}
    return {"ok": True, "lines": list(dq or [])}


@app.get("/api/logs/stream")
async def api_logs_stream(request: Request):
    q: asyncio.Queue = asyncio.Queue()
    _log_subs.append(q)
    my = getattr(request.state, "sid", None)
    mine = lambda sid: sid is None or sid == my

    async def generate():
        for line, sid, job in list(_log_history):
            if mine(sid):
                yield f"data: {json.dumps({'line': line, 'job': job})}\n\n"
        try:
            while True:
                try:
                    line, sid, job = await asyncio.wait_for(q.get(), timeout=15)
                    if not mine(sid):
                        continue
                    yield f"data: {json.dumps({'line': line, 'job': job})}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'ping': True})}\n\n"
        finally:
            try:
                _log_subs.remove(q)
            except ValueError:
                pass

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )



static_dir = resource_path("static")


def _asset_ver() -> str:
    """정적 파일이 바뀌면 자동으로 올라가는 버전. index.html의 ?v=를 이걸로 바꿔 끼운다.
    예전에는 ?v=171이 손으로 박혀 있어, 코드를 고쳐도 브라우저·CF 캐시가 옛 파일을 계속 줬다."""
    try:
        return str(int(max(os.path.getmtime(os.path.join(static_dir, f))
                           for f in ("korail.js", "style.css", "index.html"))))
    except Exception:
        return "0"


def _index_html() -> str:
    html = open(os.path.join(static_dir, "index.html"), encoding="utf-8").read()
    ver = _asset_ver()
    html = re.sub(r"\?v=\d+", "?v=" + ver, html)
    # 지금 화면이 어느 버전으로 그려졌는지 심어 둔다 — 새 버전이 올라오면 스스로 새로고침한다
    return html.replace("<head>", f"<head><script>window.__ASSET_V='{ver}'</script>", 1)


@app.get("/api/seatgroups")
async def api_seat_groups():
    """좌석 배치 그룹 — 후보 좌석을 그룹 단위로 등록하기 위한 목록."""
    d = seat_groups()
    return {"ok": True, "groups": d.get("groups", []),
            "by_train": d.get("by_train", {}),
            "mirror": d.get("mirror", {}), "mirror_cars": d.get("mirror_cars", {}),
            "by_clsf": d.get("by_clsf", {})}


_WHO_CACHE: Dict[str, tuple] = {}   # 코레일 회원번호 → (표시이름, 시각)


def _who_by_member(member_no: str) -> str:
    """코레일 회원번호로 그 계정의 사람 이름을 찾는다(승인 목록의 이름 → 이메일 → 회원번호)."""
    member_no = (member_no or "").strip()
    if not member_no:
        return ""
    hit = _WHO_CACHE.get(member_no)
    if hit and time.time() - hit[1] < 300:
        return hit[0]
    label = member_no
    try:
        con = _sync_db()
        rows = con.execute("SELECT email, blob FROM user_sync").fetchall()
        con.close()
        names = (_allow_load() or {}).get("names", {})
        for key, blob in rows:
            if not key.startswith("u:"):
                continue
            try:
                acc = _json.loads(_json.loads(blob or "{}").get("kor_ktx_account") or "{}")
            except Exception:
                continue
            if (acc.get("memberNo") or "").strip() == member_no:
                email = key[2:]
                label = names.get(email) or email.split("@")[0]
                break
    except Exception:
        pass
    _WHO_CACHE[member_no] = (label, time.time())
    return label


def _call_who(c: dict) -> str:
    """호출 한 줄이 '누가 보낸 것'인지 — 코레일 계정 우선, 없으면 작업의 구글 계정, 그것도 없으면 화면."""
    acct = (c.get("acct") or "").strip()
    if acct:
        return _who_by_member(acct)
    job = (c.get("job") or "")
    if job:
        email = job.rsplit(":", 1)[0]
        if email.startswith("u:"):
            email = email[2:]
        if "@" in email:
            names = (_allow_load() or {}).get("names", {})
            return names.get(email) or email.split("@")[0]
    return "화면"


@app.get("/calllog")
async def calllog_page(request: Request):
    """실시간 호출을 새 탭에서 — 앱 전체를 띄우지 않는 가벼운 단독 페이지(관리자용)."""
    loopback = bool(request.client) and request.client.host in ("127.0.0.1", "::1") and not request.headers.get("cf-connecting-ip")
    if _session_email(request) not in _ADMIN_EMAILS and not loopback:
        return _HTML(_page("권한 없음", "<p>관리자만 볼 수 있습니다.</p>"), status_code=403)
    html = """<!doctype html><html lang=ko><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>코레일 API 호출</title><style>
 :root{--bg:#F2F4F6;--line:#E5E8EB;--muted:#8B95A1;--blue:#3182F6}
 *{box-sizing:border-box} body{margin:0;font:13px/1.5 -apple-system,BlinkMacSystemFont,'Apple SD Gothic Neo',sans-serif;background:var(--bg);color:#191F28}
 header{position:sticky;top:0;background:#fff;border-bottom:1px solid var(--line);padding:10px 12px}
 h1{margin:0 0 8px;font-size:15px}
 .chips{display:flex;gap:6px;flex-wrap:wrap}
 .chip{border:1px solid var(--line);background:#fff;border-radius:999px;padding:4px 10px;font-size:12px;cursor:pointer}
 .chip.on{background:#EEF3FF;border-color:#C9DDFF;color:var(--blue);font-weight:600}
 .rows{padding:8px 12px}
 .r{display:grid;grid-template-columns:64px 90px 110px 1fr;gap:8px;padding:5px 0;border-bottom:1px solid var(--line);align-items:baseline}
 .who{font-weight:700;font-size:11.5px;color:#4E5968;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .t{color:var(--muted);font-variant-numeric:tabular-nums;font-size:11.5px}
 .p{font-weight:600}.p.web{color:#0CA678}.p.mob{color:var(--blue)}.p.cache{color:#F08C00}
 .ep{color:#4E5968;font-size:12px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
 .pa{color:var(--muted);font-size:11.5px;word-break:break-all}
 .r.cache{background:#FFFBF0}
 @media(max-width:640px){.r{grid-template-columns:56px 1fr}.ep,.pa{grid-column:1/-1}}
</style></head><body>
<header><h1>코레일 API 호출 <span id=cnt style="color:#8B95A1;font-weight:400"></span></h1>
<div class=chips id=chips></div></header><div class=rows id=rows></div>
<script>
var after=0, all=[], sel='', seen={};
function chips(){var c=document.getElementById('chips');var names=Object.keys(seen).sort();
 c.innerHTML='';[['','전체']].concat(names.map(function(n){return [n,n+' ('+seen[n]+')']})).forEach(function(p){
  var b=document.createElement('button');b.className='chip'+(sel===p[0]?' on':'');b.textContent=p[1];
  b.onclick=function(){sel=p[0];chips();draw()};c.appendChild(b)});}
function draw(){var r=document.getElementById('rows');r.innerHTML='';
 var list=all.filter(function(x){return !sel||x.who===sel});
 list.slice(-400).forEach(function(x){var d=document.createElement('div');d.className='r'+(x.cached?' cache':'');
  d.innerHTML='<span class=t></span><span class=who></span><span class="p '+(x.cached?'cache':(x.host==='web'?'web':'mob'))+'"></span><span class=ep></span><span class=pa></span>';
  d.children[0].textContent=x.t;d.children[1].textContent=x.who||'';d.children[2].textContent=x.purpose;
  d.children[3].textContent=x.ep;d.children[4].textContent=x.params||'';r.appendChild(d)});
 document.getElementById('cnt').textContent='· '+list.length+'건';
 window.scrollTo(0,document.body.scrollHeight);}
function poll(){fetch('/api/calllog?after='+after).then(function(r){return r.json()}).then(function(d){
 if(!d||!d.ok)return;(d.calls||[]).forEach(function(c){after=Math.max(after,c.n);all.push(c);
  seen[c.who||'?']=(seen[c.who||'?']||0)+1});
 if(all.length>1200)all=all.slice(-800);
 chips();draw()}).catch(function(){})}
poll();setInterval(poll,1500);
</script></body></html>"""
    return _HTML(html, headers={"Cache-Control": "no-store"})


@app.get("/api/calllog")
async def api_calllog(request: Request, after: int = 0):
    """관리자 실시간 호출 보기 — 최근 코레일 API 호출 목록. 비관리자에겐 빈 목록."""
    # 같은 PC(루프백)에서 온 요청은 허용 — 로컬 QA 하네스가 호출 귀속을 확인한다(터널 요청은 관리자만)
    loopback = bool(request.client) and request.client.host in ("127.0.0.1", "::1") and not request.headers.get("cf-connecting-ip")
    if _session_email(request) not in _ADMIN_EMAILS and not loopback:
        return {"ok": False, "calls": []}
    calls = get_call_log(after)
    for c in calls:
        c["who"] = _call_who(c)
    return {"ok": True, "calls": calls}


@app.get("/api/version")
async def api_version():
    """정적 파일 버전. 화면이 옛 CSS/JS를 들고 있는지 클라이언트가 스스로 확인한다."""
    return {"ok": True, "v": _asset_ver()}


@app.get("/api/update")
async def api_update_status():
    """자동 업데이트 상태. 윈도우 단독 실행에서만 의미가 있다(맥 운영본은 git 배포)."""
    if not LOCAL_MODE:
        return {"ok": True, "enabled": False}
    import updater
    st = dict(updater.STATE)
    st["current"] = st.get("current") or updater.current_version()
    return {"ok": True, "enabled": True, **st}


@app.post("/api/update/check")
async def api_update_check():
    if not LOCAL_MODE:
        return {"ok": False, "error": "이 환경에서는 자동 업데이트를 쓰지 않습니다"}
    import updater
    loop = asyncio.get_event_loop()
    st = await loop.run_in_executor(_executor, updater.check_and_download)
    return {"ok": True, "enabled": True, **st}


@app.post("/api/update/apply")
async def api_update_apply():
    """받아 둔 파일을 적용하고 프로그램을 다시 띄운다. 감시 중이면 거절한다."""
    if not LOCAL_MODE:
        return {"ok": False, "error": "이 환경에서는 자동 업데이트를 쓰지 않습니다"}
    import updater
    running = [k for k, st in KTX_SESSIONS.items() if getattr(st, "running", False)]
    if running:
        return {"ok": False, "error": f"감시 {len(running)}건이 돌고 있습니다. 중단한 뒤 적용하세요"}
    try:
        if not updater.apply_staged():
            return {"ok": False, "error": "적용할 업데이트가 없습니다"}
    except Exception as e:
        return {"ok": False, "error": f"적용 실패: {e}"}
    exe = _parent_exe()
    can_restart = bool(exe) and "python" not in os.path.basename(exe).lower()
    threading.Thread(target=_restart_self, daemon=True).start()
    return {"ok": True, "restarting": can_restart,
            "manual": not can_restart,
            "message": ("업데이트를 적용했습니다. 다시 시작합니다." if can_restart
                        else "업데이트를 적용했습니다. 창을 닫았다가 다시 실행해 주세요.")}


def _parent_exe() -> str:
    """이 프로세스를 띄운 부모(=SFX 실행 파일 Trainer-x.y.z.exe)의 경로. 윈도우 전용."""
    if sys.platform != "win32":
        return ""
    try:
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x2

        class PE32(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_char * 260)]

        me = k32.GetCurrentProcessId()
        snap = k32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        entry = PE32(); entry.dwSize = ctypes.sizeof(PE32)
        ppid = 0
        if k32.Process32First(snap, ctypes.byref(entry)):
            while True:
                if entry.th32ProcessID == me:
                    ppid = entry.th32ParentProcessID
                    break
                if not k32.Process32Next(snap, ctypes.byref(entry)):
                    break
        k32.CloseHandle(snap)
        if not ppid:
            return ""
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, ppid)
        if not h:
            return ""
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        ok = k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size))
        k32.CloseHandle(h)
        return buf.value if ok else ""
    except Exception:
        return ""


def _restart_self():
    """업데이트 적용 후 다시 띄운다.

    ⚠️ os.execv 로 python.exe 를 다시 부르면 안 된다 — 단독 실행본은 SFX 가 임시 폴더에
    풀어 놓은 python.exe 라, 원래 프로세스가 끝나는 순간 SFX 가 그 폴더를 지운다.
    (2026-09-09 실측: 재시작 누르면 서버가 죽고 ERR_CONNECTION_REFUSED)
    그래서 부모인 원본 exe 를 새로 띄우고, 그게 성공했을 때만 이 프로세스를 끝낸다."""
    time.sleep(1.0)
    exe = _parent_exe()
    if exe and exe.lower().endswith(".exe") and "python" not in os.path.basename(exe).lower():
        try:
            import subprocess
            DETACHED = 0x00000008 | 0x00000200      # DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
            subprocess.Popen([exe], close_fds=True, creationflags=DETACHED,
                             cwd=os.path.dirname(exe) or None)
            time.sleep(2.0)                          # 새 창이 뜰 시간을 준다
            os._exit(0)
        except Exception as e:
            logger.warning(f"[업데이트] 재시작 실패({e}) — 수동 재실행 안내로 넘어간다")
    # 되살릴 방법이 없으면 절대 죽지 않는다. 사용자가 직접 껐다 켜면 새 버전이 뜬다.
    _UPDATE_MANUAL_RESTART["need"] = True
    logger.warning("[업데이트] 적용 완료 — 프로그램을 닫았다 다시 열면 새 버전으로 시작합니다")


_UPDATE_MANUAL_RESTART = {"need": False}


@app.get("/")
async def index_page():
    try:
        return _HTML(_index_html(), headers={"Cache-Control": "no-store"})
    except Exception as e:
        return _HTML(f"<h1>index unavailable</h1><p>{e}</p>", status_code=500)


class _NoStoreStatic(StaticFiles):
    """정적 파일에 캐시를 남기지 않는다 — 코드를 고쳐도 브라우저가 옛 파일을 붙들고 있어
    '반영이 안 된다'로 보이는 일이 반복돼서(2026-09-08, 09-09) 아예 no-store로 못 박는다."""
    def file_response(self, *a, **kw):
        resp = super().file_response(*a, **kw)
        resp.headers["Cache-Control"] = "no-store, must-revalidate"
        return resp


app.mount("/", _NoStoreStatic(directory=static_dir, html=True), name="static")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", "8767")), log_level="warning")
