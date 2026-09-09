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
    _make_korail, set_default_account, layout_cars, layout_seats, _build_candidates, _reserve, _reserve_standing, pay_with_toss, pay_with_card, card_check, simple_pay_keys, list_ncards, my_tickets, history_presets, cancel_reservation, list_trains_preview, prefs_are_strict, GATE, day_calendar, trains_from_timetable, open_time_exceptions, is_not_open_yet, list_events, mark_events_seen,
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
        want = p.get("cand_seats") or []
        need = int(p["adults"])
        free = []
        for car_no in dict.fromkeys(x["car_no"] for x in want):
            try:
                m = seat_map(korail, train, car_no, seat, need, after_cars=True)
            except Exception as e:
                logger.debug(f"[좌석] 후보 호차 {car_no} 좌석맵 실패({e})")
                continue
            ok = {str(x.get("label", "")).upper(): x["no"] for x in m["seats"] if x["avail"]}
            for x in want:
                if x["car_no"] != car_no:
                    continue
                hit = ok.get(str(x.get("label") or x["seat_no"]).upper()) or (
                    str(x["seat_no"]) if str(x["seat_no"]) in set(ok.values()) else None)
                if hit:
                    free.append({**x, "_no": hit})
        free.sort(key=lambda x: want.index(x))   # 등록 순서 = 우선순위
        if len(free) >= need:
            seats = [{"car_no": x["car_no"], "seat_no": x.get("_no") or x["seat_no"], "label": x.get("label", "")} for x in free[:need]]
            ranks = ", ".join(f"{want.index(x) + 1}순위" for x in free[:need])
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
                tgt = (", ".join(p["train_nos"]) + "편") if p.get("train_nos") else "전체"
                if p.get("search_start_hhmmss"):   # 고른 열차만 조회하도록 좁힌 범위
                    ss = p["search_start_hhmmss"]; se = p["search_end_min"]
                    tgt += f" (조회 {ss[:2]}:{ss[2:4]}~{se // 60:02d}:{se % 60:02d})"
                logger.info(f"감시 시작 · {p['dep']}→{p['arr']} {p['date_yyyymmdd'][4:6]}/{p['date_yyyymmdd'][6:]} "
                            f"{p['start_hhmm'][:2]}:{p['start_hhmm'][2:]}~{p['end_hhmm'][:2]}:{p['end_hhmm'][2:]} "
                            f"· {p['adults']}명 · {p['interval_sec']}초 · {tgt}")
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
                        if p.get("ncard") and not is_waiting and not is_standing:
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


# ── 계정별 입력값/프리셋 동기화 저장(다른 기기에서도 로드) ──
# 인증된 Google 이메일 단위로 격리 저장. ⚠️ 본인 계정 자격증명(비번/PIN 포함) 평문 저장.
_SYNC_DB = data_path("userdata.db")

def _sync_db():
    con = _sqlite3.connect(_SYNC_DB)
    con.execute("CREATE TABLE IF NOT EXISTS user_sync (email TEXT PRIMARY KEY, blob TEXT, updated TEXT)")
    return con

def _sync_get(email):
    try:
        con = _sync_db()
        row = con.execute("SELECT blob FROM user_sync WHERE email=?", (email,)).fetchone()
        con.close()
        return _json.loads(row[0]) if row and row[0] else None
    except Exception as e:
        logger.warning(f"sync get 실패: {e}"); return None

def _sync_set(email, blob):
    try:
        con = _sync_db()
        con.execute(
            "INSERT INTO user_sync(email,blob,updated) VALUES(?,?,?) "
            "ON CONFLICT(email) DO UPDATE SET blob=excluded.blob, updated=excluded.updated",
            (email, _json.dumps(blob, ensure_ascii=False), datetime.now().isoformat()))
        con.commit(); con.close()
        return True
    except Exception as e:
        logger.warning(f"sync set 실패: {e}"); return False

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
_LOCAL_EXEMPT = ("/auth/login", "/auth/callback", "/auth/logout",
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


app.add_middleware(AuthMiddleware)


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
        idt = r.json().get("id_token", "")
        payload = _json.loads(_b64.urlsafe_b64decode(idt.split(".")[1] + "==").decode())
        email = (payload.get("email") or "").lower()
        if not email or payload.get("email_verified") is False:
            return _HTML(_page("오류", "<p>이메일을 확인할 수 없습니다.</p>"), status_code=400)
    except Exception as e:
        logger.warning(f"OAuth 콜백 오류: {e!r}")
        return _HTML(_page("오류", "<p>로그인 처리 중 오류가 발생했습니다.</p>"), status_code=500)
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
            "approved": _is_approved(email), "admin": email in _ADMIN_EMAILS}


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
    return {k: (clean(v) if isinstance(v, str) else v) for k, v in data.items()}


@app.get("/api/sync")
async def api_sync_get(request: Request):
    """구글 계정별로 완전히 격리된 설정만 돌려준다.
    ⚠️ 예전에는 계정 데이터가 없으면 공용(__shared__) 값을 물려줬는데, 거기에 코레일
    회원번호·비밀번호가 들어 있어 새로 로그인한 다른 사람에게 그대로 넘어갔다
    (2026-09-09 실측 확인). 상속을 없앤다 — 새 계정은 빈 상태로 시작해 직접 등록한다."""
    key = _sync_key_for(request)
    return {"ok": True, "data": _sync_get(key), "key": key}


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
    ok = _sync_set(key, data)
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
_MODE_LABEL = {"cand": "후보 등록", "fixed": "직접 선택"}


def _open_at_for(date_yyyymmdd: str) -> Optional[datetime]:
    """코레일 일반 예매 개시 시각 = 출발일 1개월 전 07:00.
    공지 예외(10:00 개시 등)는 개시를 '늦추기만' 하므로 07:00 기준으로 기다리면 놓치지 않는다."""
    try:
        d = datetime.strptime(date_yyyymmdd, "%Y%m%d")
    except Exception:
        return None
    y, m = (d.year, d.month - 1) if d.month > 1 else (d.year - 1, 12)
    last = [31, 29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1]
    return datetime(y, m, min(d.day, last), 7, 0)


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
    """'긴급(일반실 우선 / 창측 우선 / 순방향 우선)' — 결과 탭 카드 한 줄 요약."""
    mode = _MODE_LABEL.get(req.seat_mode, "긴급")
    parts = [_CLS_LABEL.get(req.seat_class, "일반실 우선")]
    if req.seat_mode not in ("cand", "fixed"):   # 자동 선택일 때만 창측·방향이 의미가 있다
        parts += [_SIDE_LABEL.get(req.seat_side, "창측 우선"), _DIR_LABEL.get(req.seat_dir, "순방향 우선")]
    if req.allow_waiting:  parts.append("예약대기 허용")
    if req.allow_standing: parts.append("입석 허용")
    return f"{mode}({' / '.join(parts)})"


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
    p["search_start_hhmmss"] = _shift_hhmmss(times[0], -15)   # 여유 15분
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
    return {"ok": True, "start_at": start_at}


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
        })
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
@app.get("/api/trains")
async def api_trains(dep: str = "", arr: str = "", date: str = ""):
    dep, arr, date = dep.strip(), arr.strip(), date.strip()
    if not dep or not arr or not (len(date) == 8 and date.isdigit()):
        return {"ok": False, "trains": [], "error": "dep/arr/date(yyyymmdd) 필요"}
    ce = _cooling_error()
    if ce: return {**ce, "trains": []}
    loop = asyncio.get_event_loop()
    try:
        res = await loop.run_in_executor(_executor, list_trains_day, dep, arr, date)
    except Exception as e:
        res = {"ok": False, "trains": [], "error": str(e)}
    # 아직 예매가 안 열린(또는 명절로 막힌) 날짜는 시각표로 목록을 미리 만들어 준다 — 오픈런에서 대상 열차를 골라두기 위해
    if not res.get("ok") or not res.get("trains"):
        pkey = f"{dep}|{arr}|{date}"
        phit = _PREVIEW_CACHE.get(pkey)
        if phit and time.time() - phit[0] < 600:
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
    """내 티켓 탭 — 미결제 예약 + 발권 승차권 + N카드. 20초 캐시(force=true면 무시)."""
    body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
    member_no, password = _acct_from(body)
    if not member_no or not password:
        return {"ok": False, "error": "계정 정보가 없습니다(설정에서 계정 등록)"}
    if _is_demo_acct(member_no, password):
        return {"ok": False, "error": "데모 계정입니다(코레일 조회 안 함)"}
    hit = _MYTICKETS_CACHE.get(member_no)
    if hit and not body.get("force") and time.time() - hit[0] < 20:
        return hit[1]
    loop = asyncio.get_event_loop()
    def work():
        return _ui_call(member_no, password, lambda k: {"ok": True, **my_tickets(k)})
    try:
        res = await loop.run_in_executor(_executor, work)
    except Exception as e:
        return {"ok": False, "error": f"조회 실패: {e}"}
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
        return {"ok": False, "error": f"조회 실패: {e}"}
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
        return {"ok": False, "error": f"조회 실패: {e}"}
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


def _ui_call(member_no: str, password: str, fn):
    """세션이 만료됐으면(P058 등) 한 번만 다시 로그인해서 재시도."""
    try:
        return fn(_ui_korail(member_no, password))
    except Exception as e:
        if "P058" not in str(e) and "로그인" not in str(e):
            raise
        return fn(_ui_korail(member_no, password, fresh=True))


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
        return {"ok": False, "error": f"취소 실패: {e}"}
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
        return {"ok": False, "error": f"조회 실패: {e}"}
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
def _find_train(dep: str, arr: str, date: str, train_no: str, dep_time: str, allow_ref: bool = True):
    """열차 객체를 찾는다. 예매가 열리지 않은 날짜면 **같은 열차번호가 다니는 가까운 예매 가능일**로 대신 찾는다.
    좌석 배치는 날짜가 아니라 편성(car_tp_cd)으로 정해지므로(2026-09-07 실측: 같은 열차·다른 날짜 배치 동일),
    오픈런에서도 좌석을 미리 고를 수 있다. 두 번째 반환값이 True면 대체 날짜로 본 '배치 미리보기'다."""
    from korail2.korail2 import Korail as _K
    k = _K("", "", auto_login=False)
    t = re.sub(r"\D", "", dep_time)[:6].ljust(6, "0")
    def _try(d):
        trains, _ = search_pages(k, dep, arr, d, t, hhmmss_to_min(t) + 1, max_pages=1, page_sleep=0.1)
        return next((x for x in trains if x.train_no == train_no), None)
    try:
        tr = _try(date)
    except Exception:
        tr = None
    if tr is not None:
        return k, tr, False
    if allow_ref:
        target = datetime.strptime(date, "%Y%m%d").date()
        today = datetime.now().date()
        for back in range(1, 7):                    # 같은 요일로 7일씩 당겨 예매 가능한 날 찾기
            cand = target - timedelta(days=7 * back)
            if cand <= today:
                break
            try:
                tr = _try(cand.strftime("%Y%m%d"))
            except Exception:
                tr = None
            if tr is not None:
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
        return hit[1]
    loop = asyncio.get_event_loop()
    def work():
        k, tr, ref = _find_train(dep.strip(), arr.strip(), date.strip(), train_no.strip(), dep_time.strip())
        # 기본은 코레일 웹과 같이 등급당 1콜(잔여석 있는 호차만). 매진 호차까지 필요한 지정 좌석 감시만 full=1(호차당 1콜).
        # full=1(지정 좌석 감시)은 호차당 1콜이라, 고른 등급만 훑어 절반으로 줄인다
        classes = (("2",) if seat_class == "2" else ("1",)) if full else ("1", "2")
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
async def api_layout(request: Request, train_type: str = "", car_no: str = "", seat_class: str = "1", updown: str = "down"):
    """열차 종류별 좌석 배치(호차 목록 / 호차 좌석). 후보 좌석을 실열차 없이 고르기 위한 캐시."""
    train_type = train_type.strip()
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
                _executor, lambda: layout_seats(_mk, train_type, car_no.zfill(4), seat_class, date, updown))
            if isinstance(res, list):   # 구버전 캐시 호환
                res = {"seats": res, "updown": "down"}
            return {"ok": True, **res}
        cars = await loop.run_in_executor(
            _executor, lambda: layout_cars(_mk, train_type, date, updown))
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
    threading.Thread(target=_restart_self, daemon=True).start()
    return {"ok": True, "restarting": True}


def _restart_self():
    """새 프로세스를 띄우고 이 프로세스를 끝낸다. 콘솔 창이 곧 앱이라 창은 하나로 유지된다."""
    time.sleep(1.0)
    try:
        os.execv(sys.executable, [sys.executable] + sys.argv[1:])
    except Exception:
        os._exit(0)


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
