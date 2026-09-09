import re
import json
import random
import asyncio
import logging
import logging.handlers
import os
import sys
from datetime import datetime, timedelta
from dataclasses import dataclass, field
from typing import Optional, Dict, Any, List, Tuple
from concurrent.futures import ThreadPoolExecutor

import threading
import time as _time
from collections import deque

from paths import data_path, resource_path, LOCAL_MODE

import korail2.korail2 as _k2
from korail2.korail2 import (
    Korail, AdultPassenger, TrainType, ReserveOption,
    SoldOutError, NoResultsError, KorailError, NeedToLoginError,
)

try:   # 텔레그램 봇은 옛 CLI 전용 — 웹앱은 쓰지 않는다. 배포본에는 싣지 않는다.
    from telegram import Update
    from telegram.ext import (
        Application, CommandHandler, MessageHandler, ConversationHandler,
        ContextTypes, filters
    )
except ModuleNotFoundError:
    class _TgAbsent:
        END = -1
        DEFAULT_TYPE = object
        def __getattr__(self, name):
            raise RuntimeError("텔레그램 봇 기능은 이 빌드에 포함되지 않았습니다")
    Update = Application = CommandHandler = MessageHandler = object
    ConversationHandler = ContextTypes = filters = _TgAbsent()

# ── 로깅 ──────────────────────────────────────────────────────
_log_handlers = []
if sys.stderr is not None:   # --windowed 빌드에서는 stderr가 없다 — StreamHandler가 매 로그마다 터진다
    _console = logging.StreamHandler()
    # 윈도우 단독 실행에서는 콘솔 창이 곧 앱 화면이다. INFO를 다 흘리면 읽을 수 없으니
    # 경고 이상만 띄운다. 자세한 기록은 korail.log 에 그대로 남는다(2026-09-09 지시).
    if LOCAL_MODE:
        _console.setLevel(logging.WARNING)
    _log_handlers.append(_console)
try:
    # 로테이션 필수: 2026-06-21 nodriver 리스너 에러가 24분간 2,300만 번 반복되며
    # 무제한 FileHandler로 26.6GB를 쌓은 사고가 있었음. 20MB×3 = 최대 80MB 상한.
    _log_handlers.append(logging.handlers.RotatingFileHandler(
        data_path("korail.log"), maxBytes=20 * 1024 * 1024, backupCount=3,
        encoding="utf-8", delay=True))
except Exception:
    pass   # 쓰기 불가 경로여도 앱은 떠야 한다
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=_log_handlers,
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("telegram").setLevel(logging.WARNING)

# ── 설정 ───────────────────────────────────────────────────────
BOT_TOKEN       = os.environ.get("KORAIL_BOT_TOKEN", "")
ALLOWED_USER_ID = int(os.environ.get("ALLOWED_USER_ID") or "0")
KORAIL_ID       = os.environ.get("KORAIL_ID", "")
KORAIL_PW       = os.environ.get("KORAIL_PW", "")
DEFAULT_JITTER  = 3
RELOGIN_EVERY   = 30

# ── 대화 상태 ──────────────────────────────────────────────────
S_DATE, S_DEP, S_ARR, S_START, S_END, S_ADULTS, S_INTERVAL = range(7)
DATE6_RE = re.compile(r"^\d{6}$")
TIME4_RE = re.compile(r"^\d{4}$")

_executor = ThreadPoolExecutor(max_workers=2)


# ── 상태 관리 ──────────────────────────────────────────────────
@dataclass
class RunnerState:
    running: bool = False
    task: Optional[asyncio.Task] = None
    params: Dict[str, Any] = field(default_factory=dict)
    started_at: Optional[datetime] = None
    last_check_at: Optional[datetime] = None
    next_delay_sec: Optional[int] = None
    attempts: int = 0
    last_candidates: int = 0
    last_error: Optional[str] = None
    last_success: Optional[str] = None

STATE = RunnerState()

def reset_state():
    STATE.running = False
    STATE.task = None
    STATE.params = {}
    STATE.started_at = None
    STATE.last_check_at = None
    STATE.next_delay_sec = None
    STATE.attempts = 0
    STATE.last_candidates = 0
    STATE.last_error = None
    STATE.last_success = None


# ── 유틸 ──────────────────────────────────────────────────────
def authorized(update: Update) -> bool:
    u = update.effective_user
    return bool(u and u.id == ALLOWED_USER_ID)

async def deny(update: Update):
    u = update.effective_user
    await update.message.reply_text(f"권한 없음. user_id={u.id if u else None}")

def yyMMdd_to_yyyymmdd(s: str) -> str:
    return "20" + s

def is_valid_yyMMdd(s: str) -> bool:
    if not DATE6_RE.match(s):
        return False
    mm, dd = int(s[2:4]), int(s[4:6])
    if not 1 <= mm <= 12:
        return False
    mdays = [31, 29, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
    return 1 <= dd <= mdays[mm - 1]

def hhmm_to_hhmmss(s: str) -> str:
    return s + "00"

def hhmmss_to_min(s: str) -> int:
    return int(s[:2]) * 60 + int(s[2:4])

def _valid_hhmm(s: str) -> bool:
    if not TIME4_RE.match(s):
        return False
    return 0 <= int(s[:2]) <= 23 and 0 <= int(s[2:]) <= 59

# ── 코레일 호출 관문(레이트 리밋 · 동시 실행 상한 · 차단 감지 쿨다운) ─────────────
# 예약 감시 러너(탭마다 1개) + UI 프로브(호차 40콜, 날짜상태 45콜, 예매전 목록 80콜)가
# 겹치면 순간 요청량이 급증한다. 오작동·연타로 차단당하지 않도록 나가는 모든 요청을 여기로 모은다.
# 코레일이 /com/macro.do로 공개하는 자체 매크로 제한: site 'ticket' = 100회/3, 'ticket_multi' = 200회/2
# (단위 표기가 없어 분/초가 불분명하다). 분이라면 33회/분이므로, 기존 300회/분은 과했다 → 120회/분으로 낮춘다.
KORAIL_MAX_PER_SEC = float(os.getenv("KORAIL_MAX_PER_SEC", "8"))
KORAIL_MAX_PER_MIN = int(os.getenv("KORAIL_MAX_PER_MIN", "120"))
KORAIL_MAX_CONCURRENT = int(os.getenv("KORAIL_MAX_CONCURRENT", "6"))
KORAIL_COOLDOWN_SEC = int(os.getenv("KORAIL_COOLDOWN_SEC", "300"))
# 코레일이 차단·매크로 판정을 내릴 때 쓰는 표현들
_BLOCK_SIGNS = ("MACRO ERROR", "매크로", "미허가 도구", "비정상적인 접근", "접근이 차단", "이용이 제한")


class _CallGate:
    def __init__(self):
        self._lock = threading.Lock()
        self._sem = threading.BoundedSemaphore(KORAIL_MAX_CONCURRENT)
        self._recent = deque()        # 최근 60초 요청 시각
        self._last = 0.0
        self._gap = 1.0 / max(0.5, KORAIL_MAX_PER_SEC)
        self._until = 0.0             # 쿨다운 종료 시각
        self.reason = ""
        self.total = 0
        self.blocked_count = 0

    def _slot(self):
        while True:
            with self._lock:
                now = _time.time()
                if now >= self._until:
                    while self._recent and now - self._recent[0] > 60:
                        self._recent.popleft()
                    wait = self._gap - (now - self._last)
                    if len(self._recent) >= KORAIL_MAX_PER_MIN:
                        wait = max(wait, 60 - (now - self._recent[0]))
                    if wait <= 0:
                        self._last = now
                        self._recent.append(now)
                        self.total += 1
                        return
                else:
                    wait = self._until - now
            _time.sleep(min(max(wait, 0.01), 1.0))

    def __enter__(self):
        self._sem.acquire()
        try:
            self._slot()
        except BaseException:
            self._sem.release(); raise
        return self

    def __exit__(self, *a):
        self._sem.release()
        return False

    def note(self, resp):
        """차단 신호가 보이면 전역 쿨다운을 건다(모든 호출이 그동안 대기)."""
        try:
            if resp is None:
                return
            if getattr(resp, "status_code", 200) in (403, 429):
                self.trip(f"HTTP {resp.status_code}")
                return
            head = (resp.text or "")[:400]
        except Exception:
            return
        if any(sign in head for sign in _BLOCK_SIGNS):
            self.trip("코레일 차단/매크로 감지 응답")

    def trip(self, reason: str, seconds: int = None):
        secs = KORAIL_COOLDOWN_SEC if seconds is None else seconds
        with self._lock:
            first = _time.time() >= self._until
            self._until = max(self._until, _time.time() + secs)
            self.reason = reason
            if first:
                self.blocked_count += 1
        if first:
            logger.error(f"호출 차단 · {reason} — {secs}초간 중단 후 자동 재개")
            try:
                log_event("block", "코레일 호출 차단 감지", f"{reason} — {secs}초간 전면 중단 후 자동 재개합니다. 반복되면 감시 주기를 늘리세요.", "warn", dedup_hours=1)
            except Exception:
                pass

    def status(self) -> dict:
        with self._lock:
            now = _time.time()
            while self._recent and now - self._recent[0] > 60:
                self._recent.popleft()
            return {"per_min": len(self._recent), "total": self.total, "limit_per_min": KORAIL_MAX_PER_MIN,
                    "cooling": max(0, round(self._until - now)), "reason": self.reason, "trips": self.blocked_count}


GATE = _CallGate()


# ── 코레일 API 호출 로그(관리자 실시간 보기용). 스레드 여러 개서 쓰므로 락으로 보호. ──
import threading as _thr
from collections import deque as _deque
_CALL_LOG = _deque(maxlen=200)
_CALL_LOCK = _thr.Lock()
_CALL_SEQ = [0]
# URL 경로 조각 → 사람이 읽는 목적
_CALL_PURPOSE = [
    ("login.Login", "로그인"), ("common.code.do", "비밀번호 암호화 키"),
    ("seatMovie.ScheduleView", "열차 조회"), ("certification.TicketReservation", "예약"),
    ("reservation.ReservationView", "예약 재조회"), ("certification.ReservationList", "예약 목록"),
    ("payment.ReservationPayment", "결제"), ("pay.stlKeyQry", "간편결제 키 조회"),
    ("research.TrainResearch", "호차 목록"), ("research.TResidualSeatsResearch", "좌석맵(웹)"),
    ("research.ResidualSeatsResearch", "좌석맵(모바일)"), ("myTicket.MyTicketList", "승차권 목록"),
    ("refunds.SelTicketInfo", "승차권 좌석"), ("reservationCancel", "예약 취소"),
    ("refunds.RefundsRequest", "환불"), ("trainsInfo.TrainSchedule", "운행 시각표"),
    ("common.stationdata", "역 목록"), ("nchand", "N카드"),
]
_CALL_SECRET = ("txtpwd", "txtmemberno", "pwd", "hidstlcrcrdno", "hidvanpwd", "hidathnval",
                "cardno", "txtcardno", "cardpw", "key", "sid")
def _call_purpose(url: str) -> str:
    for frag, name in _CALL_PURPOSE:
        if frag in url:
            return name
    return url.rsplit(".", 1)[-1][:24] or "API"
def _fmt_params(params) -> str:
    if not isinstance(params, dict):
        return ""
    parts = []
    for k, v in params.items():
        lk = str(k).lower()
        if any(sec in lk for sec in _CALL_SECRET):
            v = "***"
        vs = str(v)
        if vs == "" or vs == "0":
            continue                      # 빈 값·0은 잡음이라 생략(한 줄 유지)
        parts.append(f"{k}={vs[:40]}")
    return " ".join(parts)
def _log_call(method: str, url: str, params) -> None:
    try:
        _CALL_SEQ[0] += 1
        host = "web" if "www.korail.com" in url else "mobile"
        short = url.split("com.korail.mobile.", 1)[-1].split("/")[-1]
        item = {"n": _CALL_SEQ[0], "t": datetime.now().strftime("%H:%M:%S"),
                "purpose": _call_purpose(url), "method": method, "host": host,
                "ep": short, "url": url, "params": _fmt_params(params)}
        with _CALL_LOCK:
            _CALL_LOG.append(item)
    except Exception:
        pass
def get_call_log(after: int = 0):
    with _CALL_LOCK:
        return [x for x in _CALL_LOG if x["n"] > after]


def _guard_session(sess):
    """requests.Session의 모든 요청을 관문에 통과시킨다(get/post 모두 request()를 거친다)."""
    if sess is None or getattr(sess, "_gated", False):
        return sess
    orig = sess.request
    def request(method, url, **kw):
        _log_call(method, url, kw.get("params") if kw.get("params") is not None else kw.get("data"))
        with GATE:
            r = orig(method, url, **kw)
        GATE.note(r)
        return r
    sess.request = request
    sess._gated = True
    return sess


def compute_delay(base: int, jitter: int = 0) -> int:
    # 고정 주기로 보이면 매크로 티가 나므로 주기에 무작위 오차(지터)를 둔다.
    # 주기의 ±25%(최소 ±3초)와 호출부 지정값 중 큰 쪽을 폭으로 사용.
    #   10초 → 7~13초, 30초 → 22~38초, 60초 → 45~75초
    j = max(jitter, 3, round(base * 0.25))
    return max(5, base + random.randint(-j, j))


# ── 기기 프로파일(앱 지문) ────────────────────────────────────
# korail2 기본값은 UA가 "Android 13 / SM-S928N / UP1A.231005.007"인데, UP1A는 Android 14
# 빌드ID(구글 규칙: 13→T, 14→U, 15→A, 16→B)이고 SM-S928N(갤럭시 S24 Ultra)은 Android 14로
# 출시된 기종이라 13이 나올 수 없다. Dynapath 토큰의 os=13도 같은 값을 광고한다.
# → 셋(UA·빌드ID·토큰)을 실제 존재하는 조합으로 맞춘다. (2026-09-08)
#
# 2026-09-09: Android 14 → 16. S24 Ultra(2024-01 출시)를 2026년까지 출시 OS 그대로
# 광고하는 게 오히려 부자연스럽다 — 실기기는 One UI 8까지 올라가 있다. 기종을 바꾸면
# .device.json의 고정 기기 ID와 앞뒤가 안 맞으므로(같은 폰인데 기종이 바뀜), 같은 폰의
# OS만 올린다. 빌드ID 표는 pykorail 기기 카탈로그와 교차 확인:
#   13→TP1A.220624.014 · 14→UP1A.231005.007 · 15→AP3A.240905.015.A2 · 16→BP2A.250605.031
# SM-S928N의 유효 버전은 14·15·16. 보수적으로 가려면 env로 15를 주면 된다.
DEVICE_MODEL = os.environ.get("KORAIL_DEVICE_MODEL", "SM-S928N")
DEVICE_OS = os.environ.get("KORAIL_DEVICE_OS", "16")
DEVICE_BUILD = os.environ.get("KORAIL_DEVICE_BUILD", "BP2A.250605.031")
DEVICE_UA = f"Dalvik/2.1.0 (Linux; U; Android {DEVICE_OS}; {DEVICE_MODEL} Build/{DEVICE_BUILD})"
# 앱 버전: 코레일이 버전을 게이트로 쓸 수 있다(토큰 없이 요청하면 "앱을 최신 버전으로
# 업데이트하라"는 응답). 현재 값으로 계속 통과하므로 유지하되, 막히면 여기만 바꾸면 된다.
APP_VERSION = os.environ.get("KORAIL_APP_VERSION", "250601002")
_DEVICE_FILE = data_path(".device.json")


def _device_id() -> str:
    """이 설치본 고유의 기기 식별자(고정). 포크 기본값은 사용자 전원이 공유하므로 한 번만 새로 만든다.
    실행마다 바뀌면 '한 IP에서 매번 다른 폰'이 되어 오히려 부자연스럽다 — 파일에 고정 저장."""
    try:
        with open(_DEVICE_FILE, encoding="utf-8") as f:
            v = (json.load(f) or {}).get("device_id")
        if v:
            return v
    except Exception:
        pass
    v = os.environ.get("KORAIL_DEVICE_ID") or random.getrandbits(64).to_bytes(8, "big").hex()
    try:
        with open(_DEVICE_FILE, "w", encoding="utf-8") as f:
            json.dump({"device_id": v, "created": datetime.now().isoformat(timespec="seconds")}, f)
    except Exception as e:
        logger.debug(f"[기기] 저장 실패: {e}")
    return v


def _apply_device_profile() -> None:
    """korail2가 광고하는 기기 정보를 한 벌로 맞춘다(UA · Dynapath 토큰 · device_id)."""
    dev_id = _device_id()
    _k2.DEFAULT_USER_AGENT = DEVICE_UA
    _k2.Korail._device_id = dev_id
    _k2.Korail._version = APP_VERSION
    eng = _k2.DynaPathMasterEngine
    eng.DEVICE_MODEL = DEVICE_MODEL
    if getattr(eng, "_profile_patched", False):
        return
    orig = eng.generate_token

    def generate_token(self, device_id, ts, rand):
        # 원본과 동일하되 os= 를 프로파일에서 가져온다(원본은 13 하드코딩)
        plaintext = (f"ai={self.APP_ID}&di={device_id}&as={self.AS_VALUE}&"
                     f"su=false&dbg=false&emu=false&hk=false&it={self.app_start_ts}&"
                     f"ts={ts}&rt=0&os={DEVICE_OS}&dm={self.DEVICE_MODEL}&st={self.OS_TYPE}&sv={self.SDK_VERSION}")
        dyn_key = f"v1+{rand}+{ts}"
        key_enc = self.encode_normal_be(dyn_key, self.TABLE, self.I8, self.I9, self.I10)
        big_key = self.make_key(dyn_key)
        custom_table = self.make_encode_table(big_key, self.I9, self.TABLE)
        body_enc = self.encode_normal_be(plaintext, custom_table, self.I8, self.I9, self.I10)
        return f"bEeEP{self.TABLE[len(key_enc)]}{key_enc}{body_enc}"

    eng.generate_token = generate_token
    eng._profile_patched = True
    eng._orig_generate_token = orig
    logger.info(f"기기 프로파일 · Android {DEVICE_OS} / {DEVICE_MODEL} / {dev_id[:6]}…")


_apply_device_profile()


# ── korail2 API 래퍼 ──────────────────────────────────────────
# 로그인 실패가 이어지면 더 시도하지 않는다 — 코레일은 비밀번호 5회 연속 오류 시 계정을 잠근다.
# 계정 → (연속 실패수, 마지막 실패 epoch, 쉬어야 할 초). 자격증명 오류(S034)는 길게,
# 일시적 오류는 짧게 — 감시가 몇 분씩 멈추는 대신 잠금만 피한다.
_LOGIN_FAILS: Dict[str, tuple] = {}
_LOGIN_BLOCK_AFTER = 2
_LOGIN_BLOCK_BAD = 600                  # 코레일이 자격증명을 거부한 응답: 10분(반복 로그인 시 일시 거부도 여기 해당)
_LOGIN_BLOCK_SOFT = 60                  # 일시적 실패(응답 이상 등): 1분


def login_block_left(member_no: Optional[str]) -> int:
    rec = _LOGIN_FAILS.get(str(member_no or ""))
    if not rec:
        return 0
    n, ts, cool = rec
    if n < _LOGIN_BLOCK_AFTER:
        return 0
    return max(0, int(cool - (_time.time() - ts)))


def set_default_account(member_no: str, password: str) -> None:
    """화면 설정에 저장된 계정을 기본 계정으로 등록 — 스크립트·예약 작업이 .env에 의존하지 않게."""
    global KORAIL_ID, KORAIL_PW
    if member_no and password:
        KORAIL_ID, KORAIL_PW = str(member_no).strip(), str(password)


def _make_korail(member_no=None, password=None) -> Korail:
    # 코레일 로그인이 간헐적으로 degraded 응답(strMbCrdNo 없음)을 주면 login()이
    # False를 반환하고 _key를 안 채운다. 이 상태로 예약하면 P058(로그인 필요)이 난다.
    # → 성공 + _key 확보를 검증하고, 실패 시 재시도.
    # member_no/password 주면 그 계정으로(웹폼 입력), 없으면 .env 값으로.
    # korail2.login()은 실패 사유(응답 코드/문구)를 버리므로 로그인 응답을 가로채 로그에 남긴다
    # (2026-06 degraded 4건이 레이트리밋인지 점검인지 로그로 구분 불가했던 문제).
    # 재시도 간격은 백오프(10s, 30s): 레이트리밋 상황에서 1.5s 재시도는 시도 횟수만 늘림.
    import time
    acct = str(member_no or KORAIL_ID or "")
    left = login_block_left(acct)
    if left:
        raise KorailError(
            f"로그인 실패가 반복돼 {left}초간 시도를 멈췄습니다(계정 잠금 방지). "
            "설정에서 회원번호·비밀번호를 확인해 주세요.", "LOGIN_COOLDOWN")
    korail = Korail(member_no or KORAIL_ID, password or KORAIL_PW, auto_login=False)
    # korail2의 _session은 클래스 속성(모든 인스턴스 공유)이라, 로그인마다 post를 감싸면
    # 래퍼가 겹겹이 쌓여 RecursionError가 난다(2026-09-08 실측). 인스턴스 전용 세션을 준다.
    korail._session = _rq.Session()
    korail._session.headers.update({"User-Agent": DEVICE_UA})   # 토큰이 광고하는 기기와 일치시킨다
    _guard_session(korail._session)
    last, bad_cred = "", False
    for attempt in range(1, 4):
        captured = {}
        orig_post = korail._session.post
        def _spy(url, **kw):
            resp = orig_post(url, **kw)
            if "login.Login" in url:
                try:
                    captured["j"] = json.loads(resp.text)
                except Exception:
                    pass
            return resp
        korail._session.post = _spy
        try:
            ok = korail.login()
        except RecursionError:
            ok, last = False, "세션 래퍼 중첩(RecursionError) — 새 세션으로 재시도"
        except Exception as e:
            ok, last = False, repr(e)
        finally:
            korail._session.post = orig_post   # 반드시 원본으로 복구(중첩 방지)
        if ok and getattr(korail, "_key", None):
            logger.info(f"로그인 성공 · {korail.name}")
            _LOGIN_FAILS.pop(acct, None)
            return korail
        j = captured.get("j") or {}
        last = last or (f"degraded(strResult={j.get('strResult')}, code={j.get('h_msg_cd')}, msg={j.get('h_msg_txt')})"
                        if j else f"degraded(name={korail.name}, 응답 파싱 실패)")
        # 비밀번호가 틀렸다는 확정 응답(S034)이면 재시도가 계정 잠금만 앞당긴다 — 즉시 중단
        bad_cred = "S034" in last
        logger.warning(f"로그인 {'중단' if bad_cred else '재시도'} {attempt}/3 · {last}")
        if bad_cred:
            break
        if attempt < 3:
            time.sleep((10, 30)[attempt - 1])
    n = _LOGIN_FAILS.get(acct, (0, 0.0, 0))[0]
    _LOGIN_FAILS[acct] = (n + 1, time.time(), _LOGIN_BLOCK_BAD if bad_cred else _LOGIN_BLOCK_SOFT)
    log_event("login_fail", "코레일 로그인 실패", str(last)[:200], level="warn", dedup_hours=1)
    raise KorailError(f"로그인 실패: {last}", "LOGIN_FAIL")

# 코레일 조회 API는 1회 호출당 출발시각 이후 10편 고정(실측: 서울→부산 06:00 → 06:03~07:15).
# 넓은 시간 범위를 감시하려면 마지막 열차 출발시각+1분으로 다시 조회하는 시간 슬라이딩 페이징 필요.
SEARCH_MAX_PAGES  = 8
SEARCH_PAGE_SLEEP = 0.3

# 명절 특별수송기간: 잔여석 판매 개시 전에는 코레일이 대상일 조회 자체를 막는다(안내문 + 이 코드).
HOLIDAY_BLOCK_CODE = "ERR299929"
# 잔여석 상시판매 개시 예정 시각(코레일 공지 기준, UI 카운트다운용). 명절마다 갱신.
HOLIDAY_OPEN_AT = "2026-09-11T15:00:00"

def is_not_open_yet(e) -> bool:
    """아직 예매가 시작되지 않은 날짜(WRD000058 '승차권 예약발매 준비 중')인지.
    코레일은 예매 개시 '시각'을 API로 알려주지 않지만(달력에도 시각 필드 없음), 개시되는 순간
    이 오류가 사라진다 — 그래서 시각을 몰라도 조회를 계속 던져 열리는 순간을 잡을 수 있다."""
    return str(getattr(e, "code", "") or "") == "WRD000058"


def is_holiday_block(e: BaseException) -> bool:
    """코레일이 명절 특별수송기간 대상일 조회를 아직 막고 있어서 난 에러인지."""
    if not isinstance(e, KorailError):
        return False
    if getattr(e, "code", None) == HOLIDAY_BLOCK_CODE:
        return True
    return "특별수송기간" in str(getattr(e, "msg", "") or "")

def _has_standing(train) -> bool:
    """좌석 예약('11')은 불가하지만 입석/자유석으로 구매 가능한 열차인지 추정.
    ⚠️ 코레일 응답 필드 기반 추정값 — 로그인 degraded로 실데이터 미검증.
    첫 조회 진단 로그([입석?진단])로 실제 코드 확인 후 보정 필요."""
    if train.has_seat():
        return False
    psb = (getattr(train, "reserve_possible", "") or "").strip()
    nm = getattr(train, "reserve_possible_name", "") or ""
    return psb == "Y" and ("입석" in nm or "자유" in nm)


def search_pages(korail: Korail, dep: str, arr: str, date: str, start_hhmmss: str, end_min: int,
                 passengers=None, train_type=TrainType.ALL, max_pages: int = SEARCH_MAX_PAGES,
                 page_sleep: float = SEARCH_PAGE_SLEEP):
    """시간 슬라이딩 페이징: 페이지 마지막 열차 출발시각+1분으로 다음 페이지 조회.
    좌석 필터는 호출부가 로컬로 하므로 페이지의 전체 열차(무좌석·예약대기 포함)를 받아야
    마지막 출발시각으로 다음 페이지를 정확히 잡을 수 있다. (trains, pages) 반환."""
    import time
    trains, seen, pages = [], set(), 0
    t = start_hhmmss
    # 조회 응답 원본을 가로채 Train 객체에 _raw로 붙인다(korail2가 버리는 h_dpt_stn_run_ordr 등이 좌석 API에 필요)
    raw_box = {}
    orig_post = korail._session.post
    def _spy(url, **kw):
        # 인접역 시간표 미포함(코레일 조회 화면의 그 옵션). 켜져 있으면 요청 구간이 아닌
        # 열차(서울→부전, 청량리→부전 등)가 10편 정원을 잡아먹어 페이지 수만 늘어난다.
        if "ScheduleView" in url and isinstance(kw.get("params"), dict):
            kw["params"]["adjStnScdlOfrFlg"] = "N"
        resp = orig_post(url, **kw)
        if "ScheduleView" in url:
            try:
                raw_box["infos"] = json.loads(resp.text)["trn_infos"]["trn_info"]
            except Exception:
                raw_box["infos"] = []
        return resp
    while pages < max_pages:
        if pages:
            time.sleep(page_sleep)
        korail._session.post = _spy
        try:
            page = korail.search_train(
                dep=dep, arr=arr, date=date, time=t, train_type=train_type,
                passengers=passengers or [AdultPassenger(1)],
                include_no_seats=True, include_waiting_list=True,
            )
        except NoResultsError:
            break
        finally:
            korail._session.post = orig_post
        infos = raw_box.get("infos") or []
        for tr in page:
            tr._raw = next((i for i in infos if i.get("h_trn_no") == tr.train_no and i.get("h_dpt_tm") == tr.dep_time), None)
        pages += 1
        new_trains = [tr for tr in page if tr.train_no not in seen]
        if not new_trains:
            break   # 같은 열차만 반복되면(동일 분 출발 등) 더 진행할 의미 없음
        seen.update(tr.train_no for tr in new_trains)
        trains.extend(new_trains)
        last_min = hhmmss_to_min(page[-1].dep_time)
        if last_min >= end_min or last_min + 1 >= 1440:   # 범위 끝 도달 / 자정(다음 날 조회 방지)
            break
        t = f"{(last_min + 1) // 60:02d}{(last_min + 1) % 60:02d}00"
    return trains, pages


def _dur_min(dep_time: str, arr_time: str) -> int:
    """소요시간(분). 자정을 넘기면 +24시간으로 본다."""
    try:
        d = int(dep_time[:2]) * 60 + int(dep_time[2:4])
        a = int(arr_time[:2]) * 60 + int(arr_time[2:4])
        return a - d if a >= d else a + 1440 - d
    except Exception:
        return 0


def list_trains_day(dep: str, arr: str, date_yyyymmdd: str, max_pages: int = 8) -> dict:
    """하루 전체 열차 목록(좌석 유무 무관) — 날짜 선택 시 1회 조회해 24시간 캐시. 3구간(00/10/16시) 병렬, 페이지 간 0.1초.
    로그인 없는 조회 전용 호출이라 예약 계정과 무관하고, 한 번에 최대 3동시×~4페이지 ≈ 12요청/2초."""
    k = Korail("", "", auto_login=False); _guard_session(k._session)
    slices = [("000000", 599), ("100000", 959), ("160000", 1439)]
    def one(sl):
        return search_pages(k, dep, arr, date_yyyymmdd, sl[0], sl[1], max_pages=max_pages, page_sleep=0.1)
    try:
        with ThreadPoolExecutor(max_workers=3) as ex:
            parts = list(ex.map(one, slices))
    except KorailError as e:
        return {"ok": False, "trains": [], "pages": 0, "holiday_block": is_holiday_block(e),
                "error": ("명절 특별수송기간 — 잔여석 판매 개시 전에는 조회할 수 없습니다" if is_holiday_block(e) else str(e))}
    seen, trains, pages = set(), [], 0
    for trs, pg in parts:
        pages += pg
        for tr in trs:
            if tr.train_no in seen:
                continue
            seen.add(tr.train_no); trains.append(tr)
    trains.sort(key=lambda t: t.dep_time)
    out, bogus = [], []
    for tr in trains:
        if (tr.dep_name or "").strip() != dep.strip() or (tr.arr_name or "").strip() != arr.strip():
            continue   # 코레일은 다른 구간 열차도 섞어 준다
        d = _dur_min(tr.dep_time, tr.arr_time)
        if d < 8 or d > 720:   # 소요시간이 말이 안 되면 시각이 어긋난 응답이다
            bogus.append(f"{tr.train_no} {tr.dep_time[:4]}→{tr.arr_time[:4]}")
            continue
        clsf = str(getattr(tr, "train_type", "") or "")   # = h_trn_clsf_cd
        out.append({"no": tr.train_no, "type": tr.train_type_name, "dep_time": tr.dep_time, "arr_time": tr.arr_time,
                    "gp_cd": getattr(tr, "train_group", "109"),
                    # 좌석 배치 그룹 — 목록에 이미 실려 있어 좌석맵을 열 필요가 없다
                    "clsf": clsf, "group": group_id_by_clsf(clsf, train_updown(tr.train_no)),
                    "seat": "ok" if tr.has_seat() else ("wait" if tr.has_waiting_list() else "none")})
    if bogus:
        logger.warning(f"이상 응답 {len(bogus)}건 제외 · {', '.join(bogus[:5])}")
    return {"ok": True, "trains": out, "pages": pages, "holiday_block": False, "error": None}


def list_trains(dep: str, arr: str, date_yyyymmdd: str, start_hhmm: str = "0000", end_hhmm: str = "2359",
                max_pages: int = 6) -> dict:
    """로그인 없이 지정 시간 범위의 열차 목록(좌석 유무 무관) — UI '조회 대상 열차' 미리보기용.
    페이지는 '출발시각 이후 10편' 단위(개수 기준)이고, 마지막 열차가 범위 끝을 넘으면 멈추므로
    필요한 페이지 수 = 범위 안 열차 수 ÷ 10 근처(예: 서울→부산 08~12시 ≈ 3페이지). 최대 6페이지.
    출발/도착역 정확 일치만(봇의 후보 필터와 동일). 명절 차단이면 holiday_block=True."""
    k = Korail("", "", auto_login=False); _guard_session(k._session)
    s_min, e_min = hhmmss_to_min(start_hhmm.ljust(6, "0")), hhmmss_to_min(end_hhmm.ljust(6, "0"))
    try:
        trains, pages = search_pages(k, dep, arr, date_yyyymmdd, start_hhmm.ljust(6, "0"), e_min, max_pages=max_pages, page_sleep=0.1)
    except KorailError as e:
        return {"ok": False, "trains": [], "pages": 0, "holiday_block": is_holiday_block(e),
                "error": ("명절 특별수송기간 — 잔여석 판매 개시 전에는 조회할 수 없습니다" if is_holiday_block(e) else str(e))}
    out = []
    for tr in trains:
        if not (s_min <= hhmmss_to_min(tr.dep_time) <= e_min):
            continue
        if (tr.dep_name or "").strip() != dep.strip() or (tr.arr_name or "").strip() != arr.strip():
            continue
        clsf = str(getattr(tr, "train_type", "") or "")   # = h_trn_clsf_cd
        out.append({
            "no": tr.train_no, "type": tr.train_type_name, "dep_time": tr.dep_time, "arr_time": tr.arr_time,
            "seat": "ok" if tr.has_seat() else ("wait" if tr.has_waiting_list() else "none"),
            # 좌석 배치 그룹 — 목록에 이미 들어 있는 값이라 좌석맵을 열지 않아도 된다
            "clsf": clsf, "group": group_id_by_clsf(clsf, train_updown(tr.train_no)),
        })
    return {"ok": True, "trains": out, "pages": pages, "holiday_block": False, "error": None}


def seat_class_for(train, option) -> str:
    """이 예약 요청에 실제로 실릴 객실등급('1' 일반실 / '2' 특실).
    korail2.reserve()가 option과 잔여석으로 txtPsrmClCd1을 정하는 규칙과 **반드시 같아야** 한다.
    좌석맵·호차를 다른 등급으로 고르면 코레일이 ERS700120('요청 호차와 객실등급이 불일치')로
    지정 좌석 예약을 거절한다(2026-09-09 실측: 특실 우선 설정인데 일반실 1호차를 골라 실패)."""
    if option == ReserveOption.GENERAL_ONLY:
        return "1"
    if option == ReserveOption.SPECIAL_ONLY:
        return "2"
    if option == ReserveOption.SPECIAL_FIRST:
        return "2" if train.has_special_seat() else "1"
    return "1" if train.has_general_seat() else "2"   # GENERAL_FIRST(기본)


def _build_candidates(korail: Korail, p: Dict[str, Any]) -> List[Tuple]:
    start_min = hhmmss_to_min(p["start_hhmmss"])
    end_min   = hhmmss_to_min(p["end_hhmmss"])
    passengers = [AdultPassenger(p["adults"])]

    allow_waiting = bool(p.get("allow_waiting"))
    allow_standing = bool(p.get("allow_standing"))
    max_dur = int(p.get("max_duration_min") or 0)   # 최대 소요시간(분). 0=제한 없음
    first = p.get("_attempt", 1) <= 1

    # 열차를 직접 골랐으면 조회 범위가 그 구간으로 좁혀져 있다(_narrow_search_window). 필터는 원래 범위 그대로.
    s_hhmmss = p.get("search_start_hhmmss") or p["start_hhmmss"]
    s_end_min = p.get("search_end_min") or end_min
    trains, pages = search_pages(korail, p["dep"], p["arr"], p["date_yyyymmdd"], s_hhmmss, s_end_min,
                                 passengers=passengers, train_type=p.get("train_type", TrainType.ALL))
    if not trains:
        return []
    f, l = trains[0].dep_time, trains[-1].dep_time
    p["_coverage"] = f"{f[:2]}:{f[2:4]}~{l[:2]}:{l[2:4]} ({pages}페이지)"
    if first:
        note = ""
        if pages >= SEARCH_MAX_PAGES and hhmmss_to_min(l) < s_end_min:
            note = f" — 페이지 상한({SEARCH_MAX_PAGES})으로 {p['end_hhmmss'][:2]}:{p['end_hhmmss'][2:4]}까지 못 미침"
        logger.info(f"{len(trains)}편 조회 ({f[:2]}:{f[2:4]}~{l[:2]}:{l[2:4]}){note}")

    want_dep = (p["dep"] or "").strip()
    want_arr = (p["arr"] or "").strip()
    only_nos = set(str(x) for x in (p.get("train_nos") or []))   # 사용자가 고른 열차(비어 있으면 시간대 전체)
    watching = []   # 결과 탭 '조회중인 열차 목록'용 — 이번 사이클에 실제로 본 열차
    candidates = []
    excluded_station = []
    for train in trains:
        # 출발/도착역이 정확히 일치하는 열차만 (예: 서울행만, 용산행 제외)
        if (train.dep_name or "").strip() != want_dep or (train.arr_name or "").strip() != want_arr:
            excluded_station.append(f"{train.dep_name}→{train.arr_name}")
            continue
        if only_nos and str(train.train_no) not in only_nos:
            continue                       # 고르지 않은 열차는 아예 보지 않는다
        dep_min = hhmmss_to_min(train.dep_time)
        if not (start_min <= dep_min <= end_min):
            continue
        if len(watching) < 20:
            watching.append({"no": train.train_no, "type": train.train_type_name,
                             "dep_time": train.dep_time, "arr_time": train.arr_time})
        if max_dur > 0:
            dur = hhmmss_to_min(train.arr_time) - dep_min
            if dur < 0:
                dur += 1440   # 자정 넘김
            if dur > max_dur:
                continue
        has_seat = train.has_seat()
        is_waiting = (not has_seat) and train.has_waiting_list()
        is_standing = (not has_seat) and (not is_waiting) and allow_standing and _has_standing(train)
        if not has_seat and not is_waiting and not is_standing:
            # 입석 허용인데 무좌석으로 걸러진 열차는 첫 조회 때 원시 코드 진단(입석 감지 검증용)
            if allow_standing and (not has_seat) and first:
                logger.info(f"[입석?진단] {train.train_no} gen={train.general_seat} spe={train.special_seat} "
                            f"wait={train.wait_reserve_flag} psb={train.reserve_possible} 이름='{(train.reserve_possible_name or '').strip()}'")
            continue
        # 입석은 등급 개념이 없다. 나머지는 예약 요청과 같은 규칙으로 등급을 정한다
        seat = "1" if is_standing else seat_class_for(train, p.get("option"))
        candidates.append((train, seat, is_waiting, is_standing))
        tag = '·예약대기' if is_waiting else ('·입석' if is_standing else '')
        key = f"{train.train_no}{tag}"
        seen_key = p.setdefault("_cand_seen", set())
        if key not in seen_key:            # 처음 보이거나 상태가 바뀐 후보만 기록
            seen_key.add(key)
            # 가격·적립율은 빼고 시각·열차만 — 실시간 로그가 길어지지 않게
            dt, at = train.dep_time, train.arr_time
            logger.info(f"후보{tag}|{dt[:2]}:{dt[2:4]}~{at[:2]}:{at[2:4]}|"
                        f"{train.train_type_name} {train.train_no}")

    # 역 불일치 제외는 첫 조회 때 한 번만 요약(매 사이클 스팸 방지). 시간 범위 밖은 조용히 제외.
    if excluded_station and first:
        uniq = list(dict.fromkeys(excluded_station))
        logger.info(f"다른 역 {len(excluded_station)}건 제외 · {', '.join(uniq[:3])}")

    # 고른 열차의 출발시각을 알게 되면 다음 사이클부터 그 구간만 조회한다.
    # (프런트가 시각을 함께 보내면 첫 사이클부터 좁혀져 있고, 옛 작업·자동 재개는 여기서 좁혀진다)
    if only_nos and not p.get("search_start_hhmmss"):
        found = {str(t.train_no): t.dep_time for t in trains if str(t.train_no) in only_nos}
        if len(found) == len(only_nos):
            ts = sorted(found.values())
            lo = max(0, hhmmss_to_min(ts[0]) - 15)   # 여유 15분 — 시각이 조금 달라져도 놓치지 않는다
            hi = hhmmss_to_min(ts[-1]) + 15
            p["search_start_hhmmss"] = f"{lo // 60:02d}{lo % 60:02d}00"
            p["search_end_min"] = hi
            logger.debug(f"조회 구간 {lo // 60:02d}:{lo % 60:02d}~{hi // 60:02d}:{hi % 60:02d}")

    if watching:
        p["_watch_trains"] = watching

    # 우선순위: 좌석(0) > 예약대기(1) > 입석(2)
    candidates.sort(key=lambda c: (2 if c[3] else (1 if c[2] else 0)))
    return candidates

def align_cand_cars(korail: Korail, train, seat: str, want: List[dict], p: dict) -> List[dict]:
    """후보 좌석의 호차 번호를 **그날 이 열차가 실제로 쓰는 번호대**(1~8 / 11~18)로 옮긴다.

    같은 열차번호라도 운행일에 따라 1~8로도 11~18로도 편성된다(2026-09 실측: 409·411·457편이
    9/19와 10/7에 서로 달랐다). 그래서 seat_groups의 trains_by_band 같은 고정 표로는 맞출 수
    없고, 예매 시점의 호차 목록으로 판단해야 한다. 후보 템플릿 쪽 번호대도 표본 열차에 따라
    둘 중 하나라(캐시 cars|… 참조) 양방향으로 맞춘다. 열차·운행일당 1회만 조회해 p에 캐시."""
    if not want:
        return want
    key = f"{train.train_no}|{train.run_date}"
    cache = p.setdefault("_car_band", {})
    if key not in cache:
        hi = None
        try:
            nos = [int(c["car_no"]) for c in list_cars(korail, train, seat, 1)
                   if str(c.get("car_no") or "").strip().isdigit()]
            if nos:
                hi = min(nos) >= 11
        except Exception as e:
            logger.debug(f"[호차] {train.train_no}편 번호대 확인 실패({e}) — 저장된 번호 그대로")
        cache[key] = hi
    hi = cache[key]
    if hi is None:
        return want
    out, moved = [], 0
    for x in want:
        raw = str(x.get("car_no") or "").strip()
        if not raw.isdigit():
            out.append(x); continue
        n = int(raw)
        if hi and n <= 8:
            n += 10; moved += 1
        elif not hi and n >= 11:
            n -= 10; moved += 1
        out.append({**x, "car_no": str(n).zfill(len(raw)) if len(raw) >= 4 else str(n)})
    if moved:
        logger.info(f"{train.train_no}편은 오늘 {'11~18' if hi else '1~8'}호차 편성 — 후보 좌석 호차 {moved}건 맞춤")
    return out


def _reserve(korail: Korail, train, adults: int, option: str, seat: str, try_waiting: bool = False, seats: Optional[List[dict]] = None, ncard: Optional[str] = None):
    passengers = [AdultPassenger(adults)]
    # korail2.reserve()는 예약(TicketReservation) 응답을 버리고 ReservationView로 재조회한 객체를 반환한다.
    # 토스 즉시결제에 필요한 h_wct_no/h_tmp_job_sqno1·2/h_tot_prc는 예약 응답에만 있으므로 가로채 둔다.
    # seats=[{car_no,seat_no}]면 좌석 지정 파라미터(txtSrcarCnt/txtSrcarNo{i}/txtSeatNo{i})를 요청에 끼워 넣는다(letskorail 방식).
    # ncard = 내부 할인카드번호(list_ncards().card_no; 앱의 16자리 승차권번호 아님). 웹 N카드 예매 필드(txtMenuId=A2, txtDiscKndCd1=153, txtCardNo_1)
    # 를 붙이면 할인가로 예약된다(2026-09-06 실측: 용산→광주송정 41,700→35,400원).
    captured = {}
    orig_get = korail._session.get
    def _spy(url, **kw):
        if ncard and "TicketReservation" in url and isinstance(kw.get("params"), dict):
            kw["params"].update({"txtMenuId": "A2", "txtDiscKndCd1": "153", "txtCardNo_1": ncard, "txtPsgTpCd1": "1", "txtCompaCnt1": "1"})
        if seats and "TicketReservation" in url and isinstance(kw.get("params"), dict):
            kw["params"]["txtSrcarCnt"] = str(len(seats))
            if seat:
                # 좌석을 고를 때 쓴 등급으로 강제 일치. korail2는 option만 보고 등급을 정하므로
                # 여기서 맞춰주지 않으면 호차/등급이 어긋나 ERS700120으로 통째로 실패한다.
                kw["params"]["txtPsrmClCd1"] = seat
            for i, x in enumerate(seats, 1):
                kw["params"][f"txtSrcarNo{i}"] = x["car_no"]
                kw["params"][f"txtSeatNo{i}"] = x["seat_no"]
        resp = orig_get(url, **kw)
        if "TicketReservation" in url:
            try:
                captured["raw"] = json.loads(resp.text)
            except Exception:
                pass
        return resp
    korail._session.get = _spy
    try:
        rsv = korail.reserve(train, passengers=passengers, option=option, try_waiting=try_waiting)
    finally:
        korail._session.get = orig_get
    if rsv is not None:
        try:
            rsv._reserve_raw = captured.get("raw")
        except Exception:
            pass
    return rsv


# ── 좌석 지정 (letskorail이 쓰는 코레일 모바일 research API, 로그인·Dynapath 토큰 불필요 — 2026-09-05 실측) ──
SEAT_URL_CARS = _k2.KORAIL_MOBILE + ".research.TrainResearch"
SEAT_URL_MAP  = _k2.KORAIL_MOBILE + ".research.ResidualSeatsResearch.do"

def _seat_payload(korail: Korail, train, seat_class: str, count: int) -> dict:
    raw = getattr(train, "_raw", None) or {}
    # 로그인된 세션은 Key가 없으면 '[3]인증정보에 문제가 있습니다'(2026-09-06 실측). 로그인 없는 세션은 Key 없이도 됨.
    d = {"Device": korail._device, "Version": korail._version}
    if getattr(korail, "_key", None):
        d["Key"] = korail._key
    return {
        **d,
        "txtArvRsStnCd": train.arr_code, "txtArvStnRunOrdr": raw.get("h_arv_stn_run_ordr", ""),
        "txtDptDt": train.dep_date, "txtDptRsStnCd": train.dep_code, "txtDptStnRunOrdr": raw.get("h_dpt_stn_run_ordr", ""),
        "txtGdNo": "", "txtMenuId": "11", "txtPsrmClCd": seat_class, "txtRunDt": train.run_date,
        "txtSeatAttCd": "015", "txtTotPsgCnt": str(count),
        "txtTrnClsfCd": train.train_type, "txtTrnGpCd": train.train_group, "txtTrnNo": train.train_no,
    }

# ── 웹(www.korail.com) 경로: 코레일 웹 프런트(bundle.js)가 쓰는 같은 research API. 세션·선행 호출·Dynapath 없이
#    새 연결로 바로 응답한다(2026-09-06 실측, 모바일 좌석맵과 판매가능 수 일치). 응답 스키마가 다름:
#    seatList[{seat_no, seat_spec('14A'), sale_psb_flg, dir_seat_att_cd 009순/010역, rq_seat_att_cd 015일반/052대피도우미, intg_msg}],
#    seat_ary_cd('4'=2+2, '3'=2+1), windowList, seat_remain_count. 창측/내측 플래그는 없어 열 배치로 추정(2+2: A·D 창측, 2+1: A·C 창측).
WEB_BASE = "https://www.korail.com/classes/com.korail.mobile"
# 웹 호스트로 나가는 요청은 코레일 웹 클라이언트와 같은 신원을 쓴다.
# 코레일 SPA 코드표: TICKET/TOUR/GLOBAL=BH, BIZ=BE, HOLIDAY=BT, ANDROID=AD, IOS=IOS.
# 브라우저 UA·Referer를 쓰면서 Device=AD(앱)를 보내면 앞뒤가 맞지 않는다. (2026-09-08)
WEB_DEVICE = "BH"
WEB_VERSION = "999999999"
_WEB_HEADERS = {"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
                "Referer": "https://www.korail.com/ticket/search/general", "Origin": "https://www.korail.com"}
import requests as _rq

_WEB_SESSION = _rq.Session()

def _web_post(path: str, data: dict) -> dict:
    _log_call("POST", WEB_BASE + path, data)
    with GATE:
        r = _WEB_SESSION.post(WEB_BASE + path, data=data, headers=_WEB_HEADERS, timeout=15)
    GATE.note(r)
    j = json.loads(r.text)
    if j.get("strResult") != "SUCC":
        raise KorailError(j.get("h_msg_txt") or "웹 좌석 API 실패", j.get("h_msg_cd") or "WEB_FAIL")
    return j

def _web_common(korail: Korail, train, seat_class: str, count: int) -> dict:
    raw = getattr(train, "_raw", None) or {}
    return {"Device": WEB_DEVICE, "Version": WEB_VERSION, "runDt": train.run_date, "trnNo": train.train_no,
            "trnClsfCd": train.train_type, "trnGpCd": train.train_group, "dptRsStnCd": train.dep_code, "arvRsStnCd": train.arr_code,
            "psrmClCd": seat_class, "seatAttCd": "015", "dptStnRunOrdr": raw.get("h_dpt_stn_run_ordr", ""),
            "arvStnRunOrdr": raw.get("h_arv_stn_run_ordr", ""), "totPsgCnt": str(count), "gdNo": ""}

def list_cars_web(korail: Korail, train, seat_class: str = "1", count: int = 1) -> List[dict]:
    raw = getattr(train, "_raw", None) or {}
    d = {"Device": WEB_DEVICE, "Version": WEB_VERSION, "txtMenuId": "11", "txtRunDt": train.run_date, "txtDptDt": train.dep_date,
         "txtTrnNo": train.train_no, "txtDptTm": train.dep_time, "txtTrnClsfCd": train.train_type, "txtTrnGpCd": train.train_group,
         "txtDptRsStnCd": train.dep_code, "txtArvRsStnCd": train.arr_code, "txtPsrmClCd": seat_class, "txtSeatAttCd": "015", "txtCustSrtCd": "",
         "txtDptStnRunOrdr": raw.get("h_dpt_stn_run_ordr", ""), "txtArvStnRunOrdr": raw.get("h_arv_stn_run_ordr", ""), "txtTotPsgCnt": str(count), "txtGdNo": ""}
    j = _web_post(".research.TrainResearch", d)
    cars = (j.get("srcar_infos") or {}).get("srcar_info") or []
    return [{"car_no": c.get("h_srcar_no"), "rest": int(c.get("h_rest_seat_cnt") or 0), "total": int(c.get("h_seat_cnt") or 0),
             "recommended": c.get("h_srcar_no") == j.get("h_rcmd_srcar_no")} for c in cars]

# 좌석 속성 코드표 — 코레일 웹 번들(cdn.korail.com/bundle)에서 확인한 공식 값.
# 추측이 아니라 코레일이 화면에 쓰는 그 표다.
SEAT_ATT_NAMES = {
    "003": "자유석",       "015": "일반석",         "018": "2층석",
    "019": "유아동반석",   "020": "노인석",         "021": "휠체어석",
    "024": "할당석",       "027": "가족석",         "028": "전동휠체어석",
    "032": "자전거거치석", "033": "입석",           "039": "온돌마루실석",
    "040": "커플별실석",   "041": "패밀리별실석",   "051": "정기권좌석지정석",
    "052": "대피도우미석", "055": "장애인보호자석",
    # 코레일 표에는 이름이 없지만, 웹 번들의 판매 가능 예외 목록에 들어 있는 범용석.
    #   !["056","057","080".."089"].includes(rq_seat_att_cd) → 요청 속성과 달라도 팔린다
    "056": "일반석", "057": "일반석",
}
# 열차·시점마다 바뀌는 판매 상태 — 공유 배치 데이터에 넣으면 안 된다(2026-09-09 실측:
# 같은 그룹의 443과 471이 024로 6석 차이). 실시간 조회에서만 반영한다.
SEAT_ATT_VOLATILE = {"024", "051"}
# 자동 예매·후보 좌석에서 기본 제외 — 자격이 있는 사람 전용이거나 좌석 개념이 아닌 자리.
# 휠체어석·보호자석을 정당 이용자가 아닌 사람이 쓰면 부가운임 징수 대상이다(코레일 안내).
# 대피도우미석(052)은 자격 제한이 아니라 위치 표시라 일반석으로 취급한다(2026-09-09 지시).
# 할당석(024)·정기권석(051)은 판매 상태라 실시간 조회에서만 판단한다.
SEAT_ATT_BLOCKED = {"020", "021", "028", "032", "033", "055"}


def seat_att_name(code) -> str:
    return SEAT_ATT_NAMES.get(str(code or "").zfill(3), "")


def seat_map_web(korail: Korail, train, car_no: str, seat_class: str = "1", count: int = 1) -> dict:
    """호차 좌석맵. 응답의 car_tp_cd(차량 타입 코드)가 코레일이 좌석배치를 고르는 진짜 기준이다
    — 앞 4자리가 차량 형식, 뒤 4자리가 편성 번호대(2026-09-09 확인: 50454101 산천 382석,
    50534101/50534102 산천 413석). 관측한 좌석속성으로 편성을 역추론하면 안 된다."""
    j = _web_post(".research.TResidualSeatsResearch.do", dict(_web_common(korail, train, seat_class, count), srcarNo=car_no))
    ary = str(j.get("seat_ary_cd") or "4")
    win_cols = {"A", "C"} if ary == "3" else {"A", "D"}
    seats = []
    for x in j.get("seatList") or []:
        lab = (x.get("seat_spec") or "").strip()
        row, col = int(re.sub(r"\D", "", lab) or 0), re.sub(r"\d", "", lab)
        seats.append({"no": str(x.get("seat_no")), "label": lab, "row": row, "col": col,
                      "avail": x.get("sale_psb_flg") == "Y", "window": col in win_cols, "aisle": col not in win_cols,
                      "forward": x.get("dir_seat_att_cd") == "009",
                      "dir": {"009": "fwd", "010": "rev"}.get(str(x.get("dir_seat_att_cd") or ""), ""),
                      "door": False, "attr": x.get("rq_seat_att_cd"),
                      "att_name": seat_att_name(x.get("rq_seat_att_cd")),
                      "blocked": str(x.get("rq_seat_att_cd") or "") in SEAT_ATT_BLOCKED,
                      # rq=000·etc=001·dir=000 = 격자에는 있지만 좌석이 없는 자리(정원에서 빠진다)
                      "noseat": (str(x.get("rq_seat_att_cd") or "") == "000"
                                 and str(x.get("etc_seat_att_cd") or "") == "001"),
                      "note": (x.get("intg_msg") or "").strip()})
    max_row = max((s_["row"] for s_ in seats), default=0)
    for i, s_ in enumerate(seats):
        s_["door"] = s_["row"] in (1, max_row)   # 출입문 인접: 첫 열·끝 열로 추정(웹 응답엔 플래그 없음)
        s_["seq"] = i                            # 응답 순서 = 실제 배치 순서(왼쪽이 뒤, 오른쪽이 앞)
    return {"car_no": str(j.get("scar_no") or car_no),
            "capacity": int(j.get("seat_total_count") or 0),   # 코레일이 말하는 정원(빈 격자 제외)
            "max": int(j.get("seat_total_count") or len(seats)),
            "psb": int(j.get("seat_remain_count") or 0), "seats": seats, "source": "web",
            "car_updown": j.get("up_dn_dv_cd"), "layout_type": j.get("layout_type"),
            "car_tp_cd": str(j.get("car_tp_cd") or "")}   # 코레일이 좌석배치를 고르는 기준


def _train_times_on(korail: Korail, train_no: str, trn_gp_cd: str, run_dt: str, dep: str, arr: str):
    """그 날짜에 이 열차가 실제로 운행하는지 + 출발/도착 시각. 예매 전 날짜에도 응답한다(시각표 API)."""
    url = _k2.KORAIL_MOBILE + ".trainsInfo.TrainSchedule"
    data = {"Device": korail._device, "Version": korail._version, "txtRunDt": run_dt, "txtTrnNo": train_no, "txtTrnGpCd": trn_gp_cd}
    headers, sid = korail._get_auth_headers_and_sid(url)
    if sid:
        data["Sid"] = sid
    try:
        j = json.loads(korail._session.post(url, data=data, headers=headers).text)
        stops = (j.get("time_infos") or {}).get("time_info") or []
    except Exception:
        return None
    idx = {}
    for i, st in enumerate(stops):
        idx.setdefault((st.get("h_stop_rs_stn_nm") or "").strip(), i)
    if dep not in idx or arr not in idx or idx[dep] >= idx[arr]:
        return None   # 그 날은 운행하지 않거나 이 구간을 지나지 않음
    a, b = stops[idx[dep]], stops[idx[arr]]
    dt, at = str(a.get("h_dpt_tm") or ""), str(b.get("h_arv_tm") or "")
    if not dt.isdigit() or not at.isdigit() or at.startswith("9999"):
        return None
    return dt, at


# ── 코레일 공식 KTX 시간표 엑셀(열차운임/시간표 게시판) ─────────────────────────
# 예매가 열리지 않은 날짜의 열차 목록을 만들 때, 하루치를 조회(약 17콜)하는 대신
# 코레일이 게시판에 올려둔 시각표 엑셀을 1번 받아 쓴다. 열차번호·편성·역별 시각·**운행 요일**이 다 들어 있다.
_TT_BOARD = "https://www.korail.com/com/userBoard.do"
_TT_FILE_BASE = "https://www.korail.com/file/cubedata/COMMON/"
_TT_CACHE: Dict[str, Any] = {"ts": 0.0, "trains": None, "title": ""}
TT_TTL = 7 * 86400          # 시각표 개정은 드물다 — 7일 캐시
_WD_CHARS = "월화수목금토일"


def _tt_find_file() -> Tuple[str, str]:
    """게시판에서 최신 'KTX 시간표' 글의 첨부 엑셀 경로를 찾는다(1콜)."""
    with GATE:
        r = _WEB_SESSION.get(_TT_BOARD, params={"schBcid": "ticketTable", "mode": "list", "page": "1",
                                                "Device": "BH", "Version": "999999999"},
                             headers=_WEB_HEADERS, timeout=20)
    GATE.note(r)
    for post in (r.json().get("boardList") or []):
        title = str(post.get("bdTitle") or "")
        if "KTX" in title and "시간표" in title:
            m = re.search(r"([\w/]+\.xlsx)", str(post.get("fileId") or ""))
            if m:
                return _TT_FILE_BASE + m.group(1), title
    raise KorailError("게시판에서 KTX 시간표 파일을 찾지 못했습니다", "TT_NOFILE")


def _xlsx_rows(zf, sheet_path: str, shared: List[str]) -> Dict[str, Dict[str, str]]:
    xml = zf.read(sheet_path).decode("utf-8", "ignore")
    out = {}
    for rno, rx in re.findall(r'<row[^>]*r="(\d+)"[^>]*>(.*?)</row>', xml, re.S):
        cells = {}
        for m in re.finditer(r'<c r="([A-Z]+)\d+"(?:[^>]*t="(\w+)")?[^>]*>(?:<v>([^<]*)</v>)?', rx):
            col, t, v = m.groups()
            if v is None:
                continue
            cells[col] = shared[int(v)] if (t == "s" and v.isdigit() and int(v) < len(shared)) else v
        if cells:
            out[rno] = cells
    return out


def _col_i(col: str) -> int:
    n = 0
    for ch in col:
        n = n * 26 + (ord(ch) - 64)
    return n


def _tt_days(note: str) -> set:
    """비고의 운행일('매일', '월화수목', '금토일')을 요일 집합(월=0)으로."""
    note = (note or "").strip()
    if not note or "매일" in note:
        return set(range(7))
    d = {i for i, ch in enumerate(_WD_CHARS) if ch in note}
    return d or set(range(7))


def _tt_parse(blob: bytes) -> List[dict]:
    """엑셀 → [{no, type, days:set, stops:{역명: 분}}]. 시트마다 하행/상행 블록이 좌우로 나뉘어 있다."""
    import io, zipfile
    zf = zipfile.ZipFile(io.BytesIO(blob))
    shared = [re.sub(r"<[^>]+>", "", m) for m in
              re.findall(r"<si>(.*?)</si>", zf.read("xl/sharedStrings.xml").decode("utf-8", "ignore"), re.S)]
    wb = zf.read("xl/workbook.xml").decode("utf-8", "ignore")
    names = [n for n in re.findall(r'<sheet name="([^"]+)"', wb) if not n.startswith("_xlnm")]
    trains = []
    for idx, _name in enumerate(names, 1):
        path = f"xl/worksheets/sheet{idx}.xml"
        if path not in zf.namelist():
            continue
        rows = _xlsx_rows(zf, path, shared)
        hrow = next((r for r in sorted(rows, key=int) if "열차번호" in rows[r].values()), None)
        if not hrow:
            continue
        hdr = rows[hrow]
        starts = sorted((c for c, v in hdr.items() if v == "열차번호"), key=_col_i)
        bounds = [(starts[i], starts[i + 1] if i + 1 < len(starts) else "ZZZ") for i in range(len(starts))]
        for lo, hi in bounds:
            cols = {c: v for c, v in hdr.items() if _col_i(lo) <= _col_i(c) < _col_i(hi)}
            c_no = lo
            c_tp = next((c for c, v in cols.items() if v == "편성"), None)
            c_note = next((c for c, v in cols.items() if str(v).startswith("비고")), None)
            stn_cols = {c: v for c, v in cols.items() if v not in ("열차번호", "편성") and not str(v).startswith("비고")}
            for r in sorted(rows, key=int):
                if int(r) <= int(hrow):
                    continue
                cell = rows[r]
                no = str(cell.get(c_no, "")).strip()
                if not no.isdigit():
                    continue
                seq = []
                for c in sorted(stn_cols, key=_col_i):
                    try:
                        f = float(cell.get(c, ""))
                    except (TypeError, ValueError):
                        continue
                    if 0 < f < 1:
                        seq.append((str(stn_cols[c]).strip(), round(f * 24 * 60)))
                if len(seq) < 2:
                    continue
                trains.append({"no": no.zfill(3), "type": str(cell.get(c_tp, "KTX")).strip() or "KTX",
                               "days": _tt_days(str(cell.get(c_note, ""))), "seq": seq})
    return trains


def timetable_trains(force: bool = False) -> List[dict]:
    """KTX 시각표(캐시 7일). 파일 1개만 받으면 전 노선 편성을 다 안다."""
    now = _time.time()
    if not force and _TT_CACHE["trains"] and now - _TT_CACHE["ts"] < TT_TTL:
        return _TT_CACHE["trains"]
    url, title = _tt_find_file()
    with GATE:
        r = _WEB_SESSION.get(url, headers=_WEB_HEADERS, timeout=60)
    GATE.note(r)
    trains = _tt_parse(r.content)
    prev = _TT_CACHE.get("title")
    _TT_CACHE.update(ts=now, trains=trains, title=title)
    logger.info(f"[시각표] '{title}' 엑셀 파싱 완료 — 열차 {len(trains)}편(2콜, 7일 캐시)")
    if prev and prev != title:
        log_event("timetable", f"KTX 시각표 개정: {title}",
                  f"이전 '{prev}' → 새 '{title}'. 예매 전 날짜 목록이 새 시각표로 바뀝니다. 파싱이 어긋나면 열차 수가 비정상일 수 있으니 확인하세요(현재 {len(trains)}편).", "info")
    elif not trains:
        log_event("timetable", "KTX 시각표 파싱 결과 없음",
                  f"'{title}' 파일에서 열차를 하나도 못 읽었습니다. 엑셀 형식이 바뀌었을 수 있어 코드 수정이 필요합니다.", "warn", dedup_hours=24)
    return trains


def trains_from_timetable(dep: str, arr: str, date_yyyymmdd: str) -> dict:
    """공식 시각표로 만든 그 날짜의 열차 목록(예매 전 날짜용). 운행 요일이 맞는 편만 남긴다."""
    trains = timetable_trains()
    wd = datetime.strptime(date_yyyymmdd, "%Y%m%d").weekday()
    out = []
    for t in trains:
        if wd not in t["days"]:
            continue
        names = [n for n, _ in t["seq"]]
        if dep not in names or arr not in names:
            continue
        i, j = names.index(dep), names.index(arr)
        if i >= j:                      # 열 순서가 진행 방향 — 역순이면 반대 방향 열차
            continue
        a, b = t["seq"][i][1], t["seq"][j][1]
        out.append({"no": t["no"], "type": t["type"], "seat": "pre",
                    "group": train_group_id(t["type"], t["no"], train_updown(t["no"])),   # by_train 추정(예매 전엔 clsf 없음)
                    "dep_time": f"{a // 60 % 24:02d}{a % 60:02d}00", "arr_time": f"{b // 60 % 24:02d}{b % 60:02d}00"})
    out.sort(key=lambda x: x["dep_time"])
    return {"ok": bool(out), "trains": out, "preview": True, "verified": False,
            "source": "timetable", "title": _TT_CACHE.get("title", "")}


def list_trains_preview(dep: str, arr: str, date_yyyymmdd: str, workers: int = 10, verify: bool = False) -> dict:
    """**예매 시작 전 날짜의 열차 목록**(오픈런용). 코레일은 오픈 전 날짜의 예매 조회(ScheduleView)를 막지만
    열차 시각표(trainsInfo.TrainSchedule)는 미래 날짜도 응답한다(2026-09-06 실측).
    같은 요일의 '예매 가능한' 최근 날짜로 후보 편성을 뽑고, 각 열차의 목표일 시각표로 실제 운행·시각을 확인한다.
    좌석 상태는 아직 판매 전이라 알 수 없으므로 seat='pre'.
    verify=False(기본)면 기준일 편성을 그대로 쓴다(**추가 호출 0**). verify=True면 열차마다 시각표를 1콜씩 확인해
    그날 운행하지 않는 편을 걸러내고 실제 시각으로 바꾼다(정확하지만 편당 1콜)."""
    k = Korail("", "", auto_login=False); _guard_session(k._session)
    target = datetime.strptime(date_yyyymmdd, "%Y%m%d").date()
    today = datetime.now().date()
    ref = None
    for back in range(1, 9):                       # 같은 요일로 7일씩 당겨 예매 가능한 기준일 찾기
        cand = target - timedelta(days=7 * back)
        if cand < today:
            break
        ds = cand.strftime("%Y%m%d")
        try:
            base = list_trains_day(dep, arr, ds)
        except Exception:
            continue
        if base.get("ok") and base.get("trains"):
            ref = (ds, base["trains"]); break
    if not ref:
        return {"ok": False, "trains": [], "error": "기준이 될 같은 요일의 예매 가능한 날짜를 찾지 못했습니다"}
    ref_ds, cands = ref
    if not verify:
        out = [{"no": t["no"], "type": t["type"], "dep_time": t["dep_time"], "arr_time": t["arr_time"], "seat": "pre"}
               for t in cands]
        out.sort(key=lambda x: x["dep_time"])
        return {"ok": True, "trains": out, "preview": True, "verified": False, "ref_date": ref_ds}
    def one(t):
        r = _train_times_on(k, t["no"], t.get("gp_cd") or "109", date_yyyymmdd, dep, arr)
        if not r:
            return None
        return {"no": t["no"], "type": t["type"], "dep_time": r[0], "arr_time": r[1], "seat": "pre"}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = [x for x in ex.map(one, cands) if x]
    out.sort(key=lambda x: x["dep_time"])
    return {"ok": True, "trains": out, "preview": True, "verified": True, "ref_date": ref_ds}


_HOLIDAY_RANGE_RE = re.compile(r"(\d{1,2})\s*\.\s*(\d{1,2})\s*\.?\s*\([^)]*\)\s*~\s*(?:\d{2}\s*\.\s*)?(\d{1,2})\s*\.\s*(\d{1,2})")


def _holiday_range_from_msg(msg: str, probed: str) -> List[str]:
    """ERR299929 안내문의 '대상열차 : ’26.9.23.(수) ~ 9.27.(일)'에서 기간 전체를 뽑는다.
    한 번만 걸려도 명절 기간을 통째로 알 수 있어 날짜마다 물어볼 필요가 없다."""
    m = _HOLIDAY_RANGE_RE.search(msg or "")
    if not m:
        return [probed]
    y = int(probed[:4])
    try:
        a = datetime(y, int(m.group(1)), int(m.group(2))).date()
        b = datetime(y, int(m.group(3)), int(m.group(4))).date()
    except ValueError:
        return [probed]
    if b < a or (b - a).days > 20:
        return [probed]
    return [(a + timedelta(days=i)).strftime("%Y%m%d") for i in range((b - a).days + 1)]


# ── 예매 시작 시각 예외(공지 자동 반영) ─────────────────────────────
# 코레일은 명절 등 혼잡기에 '출발 1개월 전 07:00' 규칙을 바꾸고 공지로만 알린다(예: 2026-08-31 공지 →
# 10/7~10/11 출발은 10:00 개시). 달력 API(runDt)에는 **오픈 시각 필드가 없어서**(saleDdDvCd는 판매 개시
# 경계 표시일 뿐 시각과 무관) 공지를 읽는 수밖에 없다. 본문은 이미지지만 alt 텍스트에 전문이 들어 있다.
_OPENTIME_CACHE: Dict[str, Any] = {"ts": 0.0, "data": None}
_OPENTIME_TTL = 6 * 3600
_NOTICE_BOARD = "https://www.korail.com/com/userBoard.do"


# ── 시스템 기록(알림 탭) ────────────────────────────────────────────
# 사람이 알아야 할 변화만 남긴다: 예매 시각 변경, 시각표 개정, 명절 기간, 호출 차단 등.
# 코레일이 규칙을 바꾸면 여기에 뜨므로 코드 수정이 필요한지 바로 알 수 있다.
_EVENTS_FILE = data_path(".events.json")
_EVENTS_MAX = 300
_EVENTS_LOCK = threading.Lock()


def _events_load() -> List[dict]:
    try:
        with open(_EVENTS_FILE, encoding="utf-8") as f:
            return json.load(f) or []
    except Exception:
        return []


def log_event(kind: str, title: str, detail: str = "", level: str = "info", dedup_hours: int = 168) -> None:
    """운영 이벤트 기록. 같은 (kind, title)이 dedup_hours 안에 있으면 시각만 갱신한다."""
    now = _time.time()
    with _EVENTS_LOCK:
        evs = _events_load()
        for e in evs:
            if e.get("kind") == kind and e.get("title") == title and now - float(e.get("ts") or 0) < dedup_hours * 3600:
                e["ts"] = now; e["seen"] = False
                break
        else:
            evs.append({"ts": now, "kind": kind, "title": title, "detail": detail, "level": level, "seen": False})
        evs = sorted(evs, key=lambda e: e.get("ts") or 0)[-_EVENTS_MAX:]
        try:
            with open(_EVENTS_FILE, "w", encoding="utf-8") as f:
                json.dump(evs, f, ensure_ascii=False)
        except Exception as ex:
            logger.warning(f"[기록] 저장 실패: {ex}")
    logger.info(f"[기록/{level}] {title}")


def list_events(limit: int = 100) -> List[dict]:
    return sorted(_events_load(), key=lambda e: e.get("ts") or 0, reverse=True)[:limit]


def mark_events_seen() -> int:
    with _EVENTS_LOCK:
        evs = _events_load()
        n = sum(1 for e in evs if not e.get("seen"))
        for e in evs:
            e["seen"] = True
        try:
            with open(_EVENTS_FILE, "w", encoding="utf-8") as f:
                json.dump(evs, f, ensure_ascii=False)
        except Exception:
            pass
    return n


def _parse_open_time_notice(title: str, content: str, regdt: str):
    """공지 한 건에서 {from,to,hour,minute}를 뽑는다. 본문이 이미지라 alt 텍스트를 쓴다."""
    import html as _html
    alt = _html.unescape(re.sub(r"\s+", " ", " ".join(re.findall(r'alt="([^"]+)"', content or ""))))
    body = _html.unescape(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", content or "")))
    text = f"{alt} {body}"
    # 기간: '2026년 10월 7일(수) ~ 10월 11일(일)' / '10월 7일 ~ 11일'
    m = re.search(r"(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일[^~]{0,12}~\s*(?:(\d{4})년\s*)?(?:(\d{1,2})월\s*)?(\d{1,2})일", text)
    # 시각: 공지는 '(기존) … 07:00 → (변경) … 10:00' 형식이라 **반드시 '(변경)' 뒤에서** 찾아야 한다
    # (넓게 잡으면 기존 시각을 집는다 — 2026-09-07 실측 회귀).
    scope = re.split(r"\(\s*변\s*경\s*\)", text)[-1] if re.search(r"\(\s*변\s*경\s*\)", text) else text
    t = (re.search(r"(?:오전|오후)\s*(\d{1,2})\s*[:시]\s*(\d{2})?", scope)
         or re.search(r"(\d{1,2}):(\d{2})\s*부터", scope)
         or re.search(r"(\d{1,2})\s*시\s*(?:(\d{2})\s*분)?\s*부터", scope))
    if not m or not t:
        return None
    y = int(m.group(1) or m.group(4) or datetime.now().year)
    m1, d1 = int(m.group(2)), int(m.group(3))
    m2, d2 = int(m.group(5) or m1), int(m.group(6))
    y2 = int(m.group(4) or y)
    try:
        a = datetime(y, m1, d1).date(); b = datetime(y2, m2, d2).date()
    except ValueError:
        return None
    if b < a or (b - a).days > 40:
        return None
    hour = int(t.group(1)); minute = int(t.group(2) or 0)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return {"from": a.strftime("%Y-%m-%d"), "to": b.strftime("%Y-%m-%d"), "hour": hour, "minute": minute,
            "note": title, "regdt": regdt}


_OPEN_TITLE_RE = re.compile(r"예매.{0,8}(?:시작|개시).{0,8}(?:시간|시각)")


def open_time_exceptions() -> List[dict]:
    """'예매 시작 시간 변경' 공지를 읽어 {from,to,hour,minute} 규칙을 자동 추출(6시간 캐시).
    ① 메인 API(userMain.do)가 공지 본문까지 주므로 보통 1콜이면 끝난다.
    ② 공지가 메인 목록(6건)에서 밀려났으면 공지 게시판 목록으로 폴백한다(제목 매칭 후 상세 1콜)."""
    now = _time.time()
    if _OPENTIME_CACHE["data"] is not None and now - _OPENTIME_CACHE["ts"] < _OPENTIME_TTL:
        return _OPENTIME_CACHE["data"]
    out = []
    try:
        with GATE:
            r = _WEB_SESSION.get("https://www.korail.com/com/userMain.do",
                                 params={"siteCode": "ticket", "Device": "BH", "Version": "999999999"},
                                 headers=_WEB_HEADERS, timeout=20)
        GATE.note(r)
        for post in (r.json().get("NoticeList") or []):
            if not _OPEN_TITLE_RE.search(str(post.get("bdTitle") or "")):
                continue
            got = _parse_open_time_notice(str(post.get("bdTitle") or ""), str(post.get("bdContent") or ""),
                                          str(post.get("regdt") or ""))
            if got:
                out.append(got)
        if not out:   # 메인 목록에서 밀려났을 때만 게시판을 뒤진다
            with GATE:
                lr = _WEB_SESSION.get(_NOTICE_BOARD, params={"schBcid": "ticketNotice", "mode": "list", "page": "1",
                                                             "Device": "BH", "Version": "999999999"},
                                      headers=_WEB_HEADERS, timeout=20)
            GATE.note(lr)
            for post in [x for x in (lr.json().get("boardList") or [])
                         if _OPEN_TITLE_RE.search(str(x.get("bdTitle") or ""))][:3]:
                with GATE:
                    v = _WEB_SESSION.get(_NOTICE_BOARD, params={"schBcid": "ticketNotice", "mode": "view",
                                                                "bdIdx": post.get("bdIdx"), "Device": "BH", "Version": "999999999"},
                                         headers=_WEB_HEADERS, timeout=20)
                GATE.note(v)
                bv = (v.json().get("boardView") or {})
                got = _parse_open_time_notice(str(post.get("bdTitle") or ""), str(bv.get("bdContent") or ""),
                                              str(post.get("regdt") or ""))
                if got:
                    out.append(got)
    except Exception as e:
        logger.warning(f"[예매시각] 공지 파싱 실패({e}) — 기본 07:00 규칙 사용")
    # 지난 규칙은 버리고, 같은 기간이 중복되면 최신 공지만 남긴다
    today = datetime.now().date().strftime("%Y-%m-%d")
    dedup = {}
    for x in sorted(out, key=lambda z: z.get("regdt") or ""):
        if x["to"] >= today:
            dedup[(x["from"], x["to"])] = x
    out = list(dedup.values())
    _OPENTIME_CACHE.update(ts=now, data=out)
    if out:
        logger.info("[예매시각] 공지에서 예외 %d건 반영: %s" % (len(out),
                    ", ".join(f"{x['from']}~{x['to']} {x['hour']:02d}:{x['minute']:02d}" for x in out)))
        for x in out:
            log_event("opentime", f"예매 시작 시각 변경: {x['from']}~{x['to']} {x['hour']:02d}:{x['minute']:02d}",
                      f"코레일 공지 '{x['note']}'({x['regdt']})를 자동 반영했습니다. 이 기간 출발 열차는 평소(07:00)와 다른 시각에 예매가 열립니다.", "info")
    return out


def running_calendar() -> List[dict]:
    """코레일 예매 달력 — **파라미터 없이 1콜**로 예매 가능한 날짜 전체를 준다(웹 날짜 선택창이 쓰는 그 API).
    반환 항목: runDt(날짜), xTrnOpFlg 등 열차종별 운행 플래그, saleDdDvCd, hldyDvCd.
    ⚠️명절 특별수송기간 날짜는 **목록에서 통째로 빠져서** 온다(2026 추석 9/23~27 → 9/22 다음이 9/28).
    목록의 마지막 날짜가 예매 오픈 한계이고, 플래그가 전부 빈 문자열인 날은 아직 판매 준비 중이다."""
    k = Korail("", "", auto_login=False); _guard_session(k._session)
    url = _k2.KORAIL_MOBILE + ".schedule.runDt"
    data = {"Device": k._device, "Version": k._version}
    headers, sid = k._get_auth_headers_and_sid(url)
    if sid:
        data["Sid"] = sid
    j = json.loads(k._session.post(url, data=data, headers=headers).text)
    return [x for x in (j.get("runningCalendar") or []) if x.get("runDt")]


def day_calendar(days: int = 45, with_notice: bool = True) -> dict:
    """달력에 쓸 날짜 상태 — 예매 달력 1콜(+명절이 있으면 안내문용 1콜)로 끝낸다.
    이전에는 날짜마다 조회를 던져 45콜(표본으로 줄여도 11콜)이었지만, 코레일이 달력을 통째로 주므로 그럴 필요가 없다."""
    cal = running_calendar()
    if not cal:
        return {"days": {}, "calls": 1, "error": "예매 달력을 불러오지 못했습니다"}
    listed = {x["runDt"]: x for x in cal}
    last = max(listed)
    base = datetime.now().date()
    out, calls = {}, 1
    for i in range(days):
        ds = (base + timedelta(days=i)).strftime("%Y%m%d")
        x = listed.get(ds)
        if x is None:
            # 달력 한계 안인데 빠져 있으면 명절 특별수송기간, 한계 밖이면 아직 예매 전
            out[ds] = {"status": "holiday" if ds < last else "closed"}
        elif not any((x.get(f) or "") for f in ("xTrnOpFlg", "sTrnOpFlg", "gTrnOpFlg", "vTrnOpFlg", "aTrnOpFlg")):
            out[ds] = {"status": "closed"}   # 목록엔 있지만 아직 판매 준비 중(플래그 비어 있음)
    # 명절 안내문(예매·결제 일정)은 그 날짜를 한 번만 물어보면 전문이 온다
    if with_notice:
        hol = sorted(d for d, v in out.items() if v["status"] == "holiday")
        if hol:
            calls += 1
            msg = (day_status([hol[0]]).get(hol[0]) or {}).get("msg", "")
            if msg:
                for d in hol:
                    out[d]["msg"] = msg
    hol = sorted(d for d, v in out.items() if v["status"] == "holiday")
    if hol:
        log_event("holiday", f"명절 특별수송기간: {hol[0][4:6]}/{hol[0][6:]}~{hol[-1][4:6]}/{hol[-1][6:]}",
                  (out[hol[0]].get("msg") or "").strip()[:400] or "이 기간은 일반 예매가 막히고 코레일 별도 일정으로만 예매됩니다.", "info")
    return {"days": out, "calls": calls, "last_open": last}


def holiday_dates(days: int = 45, step: int = 4, dep: str = "서울", arr: str = "부산") -> dict:
    """명절 특별수송기간 탐지 — **전 날짜를 묻지 않는다.**
    특별수송기간은 연속 구간(보통 5~11일)이라 `step`일 간격 표본만 던져도 반드시 하나는 걸리고,
    걸린 응답의 안내문에 기간 전체가 적혀 있어 나머지 날짜는 계산으로 채운다(45콜 → 약 8~11콜).
    예매 오픈 여부(WRD000058)는 '출발 1개월 전' 규칙으로 계산 가능하므로 여기서 묻지 않는다."""
    base = datetime.now().date()
    probe_days = [(base + timedelta(days=i)).strftime("%Y%m%d") for i in range(1, days, step)]
    res = day_status(probe_days, dep, arr)
    out = {}
    for ds, v in res.items():
        if v.get("status") == "holiday":
            for d in _holiday_range_from_msg(v.get("msg", ""), ds):
                out[d] = {"status": "holiday", "msg": v.get("msg", "")}
    return {"days": out, "probed": len(probe_days)}


def day_status(dates: List[str], dep: str = "서울", arr: str = "부산", workers: int = 12) -> dict:
    """날짜별 예매 가능 여부를 코레일에 직접 물어본다(로그인 불필요 ScheduleView 프로브).
    - ERR299929 → 명절 특별수송기간(예: 2026 추석 9.23~9.27). 안내문에 예매·결제 일정이 그대로 들어있다.
    - WRD000058 → 아직 예약발매 준비 중(출발 1개월 전 오픈 규칙; 명절 조정도 코레일이 알아서 반영)
    명절 날짜를 하드코딩하지 않아도 다음 명절에 자동으로 맞는다(2026-09-06 실측)."""
    def probe(ds: str) -> tuple:
        try:
            k = _k2.Korail("", "", auto_login=False); _guard_session(k._session)
            k.search_train(dep=dep, arr=arr, date=ds, time="060000", include_no_seats=True)
            return ds, {"status": "ok"}
        except Exception as e:
            code = str(getattr(e, "code", "") or "")
            msg = str(e).strip()
            if code == "ERR299929" or is_holiday_block(e):
                return ds, {"status": "holiday", "msg": msg}
            if code == "WRD000058":
                return ds, {"status": "closed", "msg": msg}
            return ds, {"status": "ok"}   # 그 밖의 오류(경로 없음 등)로는 날짜를 막지 않는다
    with ThreadPoolExecutor(max_workers=workers) as ex:
        return dict(ex.map(probe, dates))


def list_cars_probe(korail: Korail, train, seat_class: str = "1", max_car: int = 20, workers: int = 10) -> List[dict]:
    """편성 전체 호차(잔여 0 포함). 호차 목록 API(TrainResearch)는 **잔여석 있는 호차만** 주고 하나도 없으면
    ERI411321('잔여석이 없습니다')로 통째로 실패한다 — 매진·예약대기 열차나 특실 미편성 열차에서 호차가 안 뜨던 원인.
    좌석맵 API는 매진 호차도 정상 반환하고 등급이 안 맞으면 ERI411092를 내므로, 호차별 병렬 프로브로 편성을 알아낸다.
    (2026-09-06 실측: 1~20호차 병렬 0.5초, 요청한 등급의 호차만 걸러짐)"""
    def probe(n: int):
        try:
            m = seat_map_web(korail, train, str(n), seat_class, 1)
        except Exception:
            return None
        return {"n": n, "car_no": m["car_no"], "total": len(m["seats"]),
                "rest": sum(1 for x in m["seats"] if x["avail"])}
    with ThreadPoolExecutor(max_workers=workers) as ex:
        out = [r for r in ex.map(probe, range(1, max_car + 1)) if r]
    out.sort(key=lambda x: x["n"])
    for x in out:
        x.pop("n", None)
    return out


def list_cars(korail: Korail, train, seat_class: str = "1", count: int = 1, full: bool = False) -> List[dict]:
    """열차의 호차 목록 + 잔여석. seat_class '1'=일반실 '2'=특실.
    full=False(기본) = 코레일 웹과 같은 방식으로 **1콜**(TrainResearch) — 잔여석 있는 호차만.
    full=True = 잔여 0인 호차까지 편성 전체를 알아내야 할 때만(지정 좌석 감시). 호차당 1콜씩 드니 남용하지 말 것."""
    if full:
        try:
            cars = list_cars_probe(korail, train, seat_class)
            if cars:
                return cars
        except Exception as e:
            logger.warning(f"호차 프로브 실패({e}) — 잔여 호차만 조회")
    try:
        return list_cars_web(korail, train, seat_class, count)
    except KorailError as e:
        if str(getattr(e, "code", "")).startswith(("ERI", "WRG")):
            raise   # 업무 오류(예: 그 등급 객실 없음)는 폴백해도 같음
        logger.warning(f"웹 호차 조회 실패({e}) — 모바일 폴백")
    except Exception as e:
        logger.warning(f"웹 호차 조회 실패({e}) — 모바일 폴백")
    j = json.loads(korail._session.post(SEAT_URL_CARS, data=_seat_payload(korail, train, seat_class, count)).text)
    korail._result_check(j)
    cars = (j.get("srcar_infos") or {}).get("srcar_info") or []
    return [{"car_no": c.get("h_srcar_no"), "rest": int(c.get("h_rest_seat_cnt") or 0), "total": int(c.get("h_seat_cnt") or 0)} for c in cars]

def seat_map(korail: Korail, train, car_no: str, seat_class: str = "1", count: int = 1, after_cars: bool = False) -> dict:
    """호차 좌석맵. seats[].label='7A', no='25'(예약 파라미터용), avail(판매가능), window/aisle, forward(순방향), door(출입문 인접).
    ⚠️ 코레일은 같은 세션에서 호차 목록(TrainResearch)을 먼저 조회하지 않으면 '[3]인증정보에 문제가 있습니다'로 거부한다
    (2026-09-06 실측). after_cars=False면 여기서 호차 조회를 선행한다."""
    try:
        return seat_map_web(korail, train, car_no, seat_class, count)
    except KorailError as e:
        if str(getattr(e, "code", "")).startswith(("ERI", "WRG")):
            raise   # 업무 오류(예: 요구 객실등급 미편성)는 폴백해도 같음
        logger.warning(f"웹 좌석맵 실패({e}) — 모바일 폴백")
    except Exception as e:
        logger.warning(f"웹 좌석맵 실패({e}) — 모바일 폴백")
    if not after_cars:
        list_cars(korail, train, seat_class, count)
    j = json.loads(korail._session.post(SEAT_URL_MAP, data=dict(_seat_payload(korail, train, seat_class, count), txtSrcarNo=car_no)).text)
    korail._result_check(j)
    seats = []
    for x in (j.get("seat_infos") or {}).get("seat_info") or []:
        lab = (x.get("h_con_seat_no") or "").strip()
        seats.append({
            "no": x.get("h_seat_no"), "label": lab,
            "row": int(re.sub(r"\D", "", lab) or 0), "col": re.sub(r"\d", "", lab),
            "avail": x.get("h_sale_psb_flg") == "Y",
            "window": x.get("h_sigl_win_in_dv") == "012", "aisle": x.get("h_sigl_win_in_dv") == "013",
            "forward": x.get("h_for_rev_dir_dv") == "009",
            "dir": {"009": "fwd", "010": "rev"}.get(str(x.get("h_for_rev_dir_dv") or ""), ""),
            "door": x.get("h_door_nbor_flg") == "Y",
            "attr": x.get("h_dmd_seat_att"),
        })
    for i, s_ in enumerate(seats):
        s_["seq"] = i   # 모바일 응답도 배치 순서를 그대로 쓴다(정렬하면 호차별 앞뒤가 뒤집힌다)
    return {"car_no": car_no, "max": int(j.get("h_max_seat_no") or 0), "psb": int(j.get("h_psb_seat_cnt") or 0), "seats": seats}

def prefs_are_strict(prefs: dict) -> bool:
    """'창측만/역방향만'처럼 조건을 강제하는 설정인지 — 조건에 맞는 자리가 없으면 예약하지 않아야 한다."""
    return str(prefs.get("side") or "").endswith("_only") or str(prefs.get("dir") or "").endswith("_only")


def pick_seats(korail: Korail, train, seat_class: str, count: int, prefs: dict, stats: Optional[dict] = None) -> List[dict]:
    """선호 조건에 맞는 좌석을 자동 선택. side: window|aisle|window_only|aisle_only, dir: fwd|rev|fwd_only|rev_only.
    '…_only'는 그 조건을 만족하는 좌석만 대상으로 삼고(조건 밖 좌석은 아예 후보에서 뺀다), '…우선'은 점수만 높인다.
    같은 호차 안에서 count석 — 2명이면 같은 열 옆자리(A·B / C·D) 우선. 못 찾으면 []."""
    side_raw = str(prefs.get("side") or "window")
    dir_raw = str(prefs.get("dir") or ("fwd" if prefs.get("forward") else ""))
    side_only, side = side_raw.endswith("_only"), side_raw.replace("_only", "")
    dir_only, want_dir = dir_raw.endswith("_only"), dir_raw.replace("_only", "")
    def allowed(x):
        if side_only and not (x["window"] if side == "window" else x["aisle"]):
            return False
        if dir_only and want_dir and x.get("dir") != want_dir:
            return False
        return True
    def score(x):
        sc = 0
        if side == "window" and x["window"]: sc += 4
        if side == "aisle" and x["aisle"]: sc += 4
        if want_dir and x.get("dir") == want_dir: sc += 2
        return sc
    max_sc = (4 + (2 if want_dir else 0)) * count
    cars = [c for c in list_cars(korail, train, seat_class, count) if c["rest"] >= count]
    best, best_sc = [], -1
    seen_free = 0   # 조건과 무관하게 실제로 비어 있던 자리 수(로그용)
    for c in cars[:6]:   # 호차마다 조회 1회 — 과다 호출 방지
        free = [x for x in seat_map(korail, train, c["car_no"], seat_class, count, after_cars=True)["seats"]
                if x["avail"] and x["attr"] in (None, "", "015")]
        seen_free += len(free)
        avail = [x for x in free if allowed(x)]
        if len(avail) < count:
            continue
        if count == 1:
            x = max(avail, key=score); cand, sc = [x], score(x)
        else:
            cand, sc = [], -1
            by_row = {}
            for x in avail: by_row.setdefault(x["row"], []).append(x)
            for row, xs in by_row.items():   # 같은 열 옆자리 조합 우선(A·B, C·D)
                xs.sort(key=lambda x: x["col"])
                for grp in (("A", "B"), ("C", "D")):
                    g = [x for x in xs if x["col"] in grp]
                    if count == 2 and len(g) == 2 and sum(map(score, g)) > sc: cand, sc = g, sum(map(score, g))
                if len(xs) >= count and count > 2 and sum(map(score, xs[:count])) > sc: cand, sc = xs[:count], sum(map(score, xs[:count]))
            if not cand:   # 같은 열이 없으면 점수순 상위 N (같은 호차)
                cand = sorted(avail, key=score, reverse=True)[:count]; sc = sum(map(score, cand)) - 1
        if sc > best_sc:
            best, best_sc = [{"car_no": c["car_no"], "seat_no": x["no"], "label": x["label"]} for x in cand], sc
        if best_sc >= max_sc:
            break   # 만점이면 더 볼 필요 없음
    if stats is not None:
        stats["free"] = seen_free
    return best

# ── 열차 종류별 좌석 배치 캐시 ────────────────────────────────
# 후보 좌석을 미리 고르려면 실제 열차 없이도 좌석맵이 필요하다. 배치는 열차 종류·호차별로
# 거의 고정이라, 살아 있는 열차 1편에서 한 번 떠서 디스크에 저장해두고 재사용한다.
_LAYOUT_FILE = data_path(".layouts.json")
_LAYOUT_TTL = 30 * 86400
# 종류별로 열차를 찾기 쉬운 대표 구간(호출 낭비를 막기 위해 최대 2개만 훑는다)
# ── 공용 캐시(읽기 전용) ──────────────────────────────────────
# 좌석 배치는 사람마다 다르지 않다. 각자 코레일을 두들겨 다시 만들 이유가 없어,
# 맥미니가 모아 공개 피드에 올린 것을 모두가 내려받아 씨앗으로 쓴다(2026-09-09 A안).
# 읽기만 한다 — 올리는 쪽은 운영 서버뿐이라 공개 쓰기 문을 열지 않는다.
SHARED_FEED = os.environ.get("TRAINER_SHARED_FEED") or \
    "https://raw.githubusercontent.com/jdent0228/trainer-update/main/shared"
_SHARED_TTL = 6 * 3600
_shared_at = 0.0


_SEEDED = False


def _seed_from_survey() -> int:
    """실측 파일(seat_layouts.json)을 배치 캐시에 한 번 심는다.
    이게 없으면 호차를 처음 누를 때마다 그룹 표본 열차를 찾느라 노선 5개를 하루치씩
    훑어(최대 30회 검색) 20~30초가 걸린다(2026-09-09 실측). 심어 두면 검색이 사라진다."""
    global _SEEDED
    if _SEEDED:
        return 0
    _SEEDED = True
    try:
        with open(resource_path("seat_layouts.json"), encoding="utf-8") as f:
            d = json.load(f)
    except Exception:
        return 0
    cur = _layout_all()
    n = 0
    for gid, g in (d.get("groups") or {}).items():
        cars = []
        for cno, info in sorted(g.get("cars", {}).items(), key=lambda x: int(x[0])):
            cars.append({"car_no": f"{int(cno):04d}", "cls": info.get("cls") or "1",
                         "total": len([s for s in info["seats"] if not s.get("noseat")])})
            key = f"seats|{gid}|{info.get('cls') or '1'}|{int(cno):04d}"
            if key in cur:
                continue
            seats = []
            for i, x in enumerate(info["seats"]):
                lab = x.get("label") or ""
                row = "".join(ch for ch in lab if ch.isdigit())
                col = "".join(ch for ch in lab if ch.isalpha())
                seats.append({"label": lab, "row": int(row) if row else 0, "col": col, "seq": i,
                              "window": col in ("A", "D"), "aisle": col in ("B", "C"),
                              "dir": x.get("dir") or "", "attr": x.get("attr") or "",
                              "att_name": seat_att_name(x.get("attr")),
                              "blocked": str(x.get("attr") or "") in SEAT_ATT_BLOCKED,
                              "noseat": bool(x.get("noseat"))})
            cur[key] = {"ts": _time.time(),
                        "value": {"seats": seats, "updown": gid.split("|")[2],
                                  "train_no": g.get("train_no", ""), "seat_class": info.get("cls") or "1"}}
            n += 1
        ckey = f"cars|{gid}"
        if cars and ckey not in cur:
            cur[ckey] = {"ts": _time.time(), "value": cars}
            n += 1
    # v7 그룹 별칭: 런타임은 'KTX-산천|down|A' 같은 v7 id로 조회하는데 씨앗은 소스 id
    # ('KTX-산천|1-8|down|B')로 저장돼 있어 미스→라이브 샘플(로그인·검색 폭주)이 났다.
    # 각 v7 그룹의 source_ids 레이아웃을 합쳐 v7 키로도 심는다(2026-09-09).
    try:
        for g in (seat_groups().get("groups") or []):
            gid7 = g.get("id"); srcs = g.get("source_ids") or []
            if not gid7 or not srcs:
                continue
            cars7 = []
            for src in srcs:
                for kk, vv in list(cur.items()):
                    if kk == f"cars|{src}":
                        cars7.extend(vv.get("value") or [])
                    elif kk.startswith(f"seats|{src}|"):
                        rest = kk[len(f"seats|{src}|"):]          # {cls}|{car}
                        dst = f"seats|{gid7}|{rest}"
                        if dst not in cur:
                            cur[dst] = vv; n += 1
            ck7 = f"cars|{gid7}"
            if cars7 and ck7 not in cur:
                cur[ck7] = {"ts": _time.time(),
                            "value": sorted({c["car_no"]: c for c in cars7}.values(), key=lambda c: int(c["car_no"]))}
                n += 1
    except Exception as e:
        logger.debug(f"[배치 씨앗] v7 별칭 실패: {e}")
    if n:
        try:
            tmp = _LAYOUT_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False)
            os.replace(tmp, _LAYOUT_FILE)
        except Exception as e:
            logger.warning(f"[배치 씨앗] 저장 실패: {e}")
            return 0
        logger.info(f"실측 좌석 배치 {n}건 적재")
    return n


def _shared_pull() -> int:
    """공용 배치 캐시를 받아 **비어 있는 키만** 채운다. 내가 직접 본 값은 건드리지 않는다."""
    global _shared_at
    if _time.time() - _shared_at < _SHARED_TTL:
        return 0
    _shared_at = _time.time()
    try:
        import urllib.request
        req = urllib.request.Request(f"{SHARED_FEED}/layouts.json",
                                     headers={"User-Agent": "Trainer", "Cache-Control": "no-cache"})
        with urllib.request.urlopen(req, timeout=20) as r:
            shared = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        logger.debug(f"[공용캐시] 받기 실패: {e}")
        return 0
    cur = _layout_all()
    n = 0
    for k, v in (shared.get("entries") or {}).items():
        if k not in cur:
            cur[k] = {"ts": _time.time(), "value": v}
            n += 1
    if n:
        try:
            tmp = _LAYOUT_FILE + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(cur, f, ensure_ascii=False)
            os.replace(tmp, _LAYOUT_FILE)
        except Exception as e:
            logger.warning(f"[공용캐시] 저장 실패: {e}")
            return 0
        logger.info(f"공용 좌석배치 {n}건 받음")
    return n


def shared_export() -> dict:
    """공유해도 되는 항목만 추린다 — 좌석 배치·호차 구성뿐, 개인 정보는 없다."""
    out = {}
    for k, rec in _layout_all().items():
        if k.startswith(("cars|", "seats|")) and isinstance(rec, dict) and rec.get("value") is not None:
            out[k] = rec["value"]
    return {"version": 1, "entries": out}


_LAYOUT_ROUTES = [("서울", "부산"), ("용산", "광주송정"), ("수서", "광주송정"), ("청량리", "부전"), ("서울", "강릉")]


def _layout_all() -> dict:
    try:
        with open(_LAYOUT_FILE, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _layout_put(key: str, value: dict) -> None:
    data = _layout_all()
    data[key] = {"ts": _time.time(), "value": value}
    try:
        tmp = _LAYOUT_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, _LAYOUT_FILE)
    except Exception as e:
        logger.debug(f"[배치] 저장 실패: {e}")


def _layout_get(key: str):
    rec = _layout_all().get(key)
    if rec and _time.time() - rec.get("ts", 0) < _LAYOUT_TTL:
        return rec.get("value")
    return None


def _sample_train(korail: Korail, train_type: str, date: str, updown: str = "down", skip_no: str = "",
                  group_id: str = ""):
    """배치 샘플용 열차 1편. group_id 를 주면 **그 그룹에 속한 열차번호만** 고른다.

    ⚠️ 그룹을 안 주면 같은 종류·방향이라도 편성이 다른 열차가 잡힌다. KTX-산천 하행에는
    A형(413석)·B형(382석)·C형(413석)이 섞여 있어, 그렇게 모은 배치를 한 칸에 담으면
    실제로 없는 좌석이 후보로 등록된다(2026-09-09 확인). 그래서 그룹을 넘겨받는다."""
    want = set()
    if group_id:
        for g in seat_groups().get("groups", []):
            if g["id"] == group_id:
                want = set(g.get("trains", []))
                break
    fallback = None
    routes = _LAYOUT_ROUTES if updown == "down" else [(b, a) for a, b in _LAYOUT_ROUTES]
    # 특정 그룹을 찾을 때는 후보가 몇 편 안 되므로 하루 전체를 넉넉히 훑는다
    win = ("000000", 1439, 6) if group_id else ("060000", 1200, 2)
    for dep, arr in routes:
        try:
            trains, _ = search_pages(korail, dep, arr, date, win[0], win[1], max_pages=win[2])
        except Exception:
            continue
        for t in trains:
            no = str(getattr(t, "train_no", "")).strip()
            if str(getattr(t, "train_type_name", "")).strip() != train_type:
                continue
            if want:
                if no not in want:
                    continue
            elif train_updown(no) != updown:
                continue
            if skip_no and no == str(skip_no):
                continue   # 다른 편을 보고 싶을 때
            fallback = fallback or t
            try:
                if t.has_general_seat() or t.has_special_seat():
                    return t
            except Exception:
                return t
    return fallback


def layout_cars(korail_fn, train_type: str, date: str, updown: str = "down",
                group_id: str = "") -> List[dict]:
    """좌석 배치 그룹의 호차 구성(캐시). [{car_no, cls, total}]
    korail_fn: 로그인된 Korail을 만드는 함수 — 캐시 적중 시에는 호출하지 않는다."""
    key = f"cars|{group_id}" if group_id else f"cars|{train_type}|{updown}"
    hit = _layout_get(key)
    if hit is None and (_seed_from_survey() or _shared_pull()):
        hit = _layout_get(key)          # 공용 캐시에 있으면 코레일을 안 두들긴다
    if hit is not None:
        return hit
    korail = korail_fn() if callable(korail_fn) else korail_fn
    t = _sample_train(korail, train_type, date, updown, group_id=group_id)
    if t is None:
        raise KorailError(f"'{train_type}' 열차를 찾지 못했습니다(배치 샘플)", "NO_SAMPLE")
    cars, errs = [], []
    for cls in ("1", "2"):
        try:
            for c in list_cars(korail, t, cls, 1, full=True):
                cars.append({"car_no": c["car_no"], "cls": c.get("cls") or cls, "total": c.get("total")})
        except Exception as e:
            errs.append(f"{cls}:{e}")
    if not cars:   # 빈 결과는 캐시하지 않는다(다음에 다시 시도)
        raise KorailError(f"'{train_type}' 호차 구성을 얻지 못했습니다 ({'; '.join(errs)[:120]})", "NO_CARS")
    # 같은 그룹이면 편성이 같으므로 관측을 합쳐도 안전하다(잔여석 때문에 호차가 일부만 보임).
    # 그룹이 없을 때만 예전처럼 합치되, 이 경로는 조사되지 않은 열차용 폴백이다.
    prev = _layout_get(key) or []
    merged = {str(c["car_no"]): c for c in prev}
    merged.update({str(c["car_no"]): c for c in cars})
    cars = sorted(merged.values(), key=lambda c: int(c["car_no"]))
    _layout_put(key, cars)
    _layout_put(f"sample|{key}", {"train_no": getattr(t, "train_no", ""), "date": date})
    return cars


def train_updown(train_no) -> str:
    """열차번호 홀수=하행, 짝수=상행 (2026-09-08 실측: 121 하행 fwd, 104 상행 rev)."""
    try:
        return "down" if int(str(train_no).strip()) % 2 else "up"
    except Exception:
        return "down"


def layout_seats(korail_fn, train_type: str, car_no: str, seat_class: str, date: str, updown: str = "down",
                 group_id: str = "") -> dict:
    """열차 종류·호차의 좌석 배치(캐시). 판매 여부는 빼고 자리 모양만 담는다.
    좌석 방향(순/역)은 상행·하행에서 정반대이므로 샘플이 어느 방향이었는지 함께 남긴다."""
    cars = _layout_get(f"cars|{group_id}" if group_id else f"cars|{train_type}|{updown}")
    if cars:   # 호차 등급을 알면 그대로 쓴다 — 특실을 일반실로 조회하면 ERI411092
        m0 = next((c for c in cars if str(c["car_no"]).lstrip("0") == str(car_no).lstrip("0")), None)
        if m0:
            seat_class = m0.get("cls") or seat_class
    _base = f"seats|{group_id}" if group_id else f"seats|{train_type}|{updown}"
    key = f"{_base}|{seat_class}|{car_no}"
    hit = _layout_get(key)
    if hit is None and (_seed_from_survey() or _shared_pull()):
        hit = _layout_get(key)
    if hit is not None:
        return hit
    korail = korail_fn() if callable(korail_fn) else korail_fn
    t = _sample_train(korail, train_type, date, updown, group_id=group_id)
    if t is None:
        raise KorailError(f"'{train_type}' 열차를 찾지 못했습니다(배치 샘플)", "NO_SAMPLE")
    try:
        m = seat_map(korail, t, car_no, seat_class, 1, after_cars=False)
    except Exception as e:
        # 이 편에는 없는 호차일 수 있다(중련 11~18 vs 단독 1~8) — 다른 편성으로 한 번 더
        logger.info(f"{train_type} {car_no}호차 샘플({getattr(t, 'train_no', '?')}편) 실패 — 다른 편성 재시도")
        t2 = _sample_train(korail, train_type, date, updown, skip_no=getattr(t, "train_no", ""), group_id=group_id)
        if t2 is None:
            raise
        alt = "2" if seat_class == "1" else "1"
        try:
            m = seat_map(korail, t2, car_no, seat_class, 1, after_cars=False)
        except Exception:
            # 등급이 다른 호차였다 — 특실 배치를 일반실 키에 넣으면 안 되므로 키도 함께 바꾼다
            m = seat_map(korail, t2, car_no, alt, 1, after_cars=False)
            seat_class = alt
            key = f"{_base}|{seat_class}|{car_no}"
        t = t2
    seats = [{"label": x.get("label"), "row": x.get("row"), "col": x.get("col"), "seq": x.get("seq"),
              "window": x.get("window"), "aisle": x.get("aisle"), "dir": x.get("dir"),
              # 좌석 속성 코드(019 유아동반·021 휠체어 등)도 함께 — 후보 등록 화면이 캐시로 그린다
              "attr": x.get("attr"), "att_name": x.get("att_name"),
              "blocked": x.get("blocked"), "noseat": x.get("noseat")}
             for x in m.get("seats", []) if x.get("label")]
    if not seats:
        raise KorailError(f"'{train_type}' {car_no}호차 좌석 배치를 얻지 못했습니다", "NO_SEATS")
    out = {"seats": seats, "updown": updown, "train_no": getattr(t, "train_no", ""), "seat_class": seat_class}
    _layout_put(key, out)
    return out


def my_info(korail: Korail) -> dict:
    """내 회원정보·포인트 — 모바일 xPoint.MyXPointView(로그인 Key만, nodriver·웹쿠키 불필요, 2026-09-06 실측)."""
    url = _k2.KORAIL_MOBILE + ".xPoint.MyXPointView"
    data = {"Device": korail._device, "Version": korail._version, "Key": korail._key}
    headers, sid = korail._get_auth_headers_and_sid(url)
    if sid:
        data["Sid"] = sid
    j = json.loads(korail._session.post(url, data=data, headers=headers).text)
    korail._result_check(j)
    def _pt(k):
        try: return int(str(j.get(k) or "0").lstrip("0") or "0")
        except Exception: return 0
    def _mask_mid(v):   # 010-1234-5678 → 010-****-5678
        v = str(v or "")
        return re.sub(r"(\d{2,3}-?)\d{3,4}(-?\d{4})", r"\g<1>****\g<2>", v)
    return {
        "name": korail.name or j.get("h_cust_nm"),
        "member_no": getattr(korail, "membership_number", "") or "",
        "phone": _mask_mid(j.get("h_cntc_chn_cont1")),
        "email": j.get("h_cntc_chn_cont2") or (korail.email or ""),
        "sex": {"M": "남", "F": "여"}.get(j.get("h_sex_dv_cd"), ""),
        "rail_point": _pt("h_korail_point"),
        "coupon_cnt": _pt("h_disc_coup_cnt"),
        "delay_cnt": _pt("h_delay_cnt"),
    }


def list_ncards(korail: Korail) -> List[dict]:
    """보유 N카드 목록 — 코레일 웹 '이용가능 티켓'과 같은 경로(2026-09-06 korail.com 청크 분석·실측).
    MyTicketList 행 중 h_tk_knd_cd=='81'(N카드할인)이 카드이고, SelTicketInfo의 dcnt_crd_info.h_dcnt_crd_no(내부 할인카드번호)가
    예약 시 txtCardNo에 넣는 값이다. 앱에 보이는 16자리(wct_no+판매일MMDD+판매순번+비밀번호)는 승차권번호라 예약에 쓰면 EVZ000003."""
    url = _k2.KORAIL_MYTICKETLIST
    data = {"Device": korail._device, "Version": korail._version, "Key": korail._key,
            "txtIndex": "1", "h_page_no": "1", "txtDeviceId": "", "h_abrd_dt_from": "", "h_abrd_dt_to": ""}
    headers, sid = korail._get_auth_headers_and_sid(url)
    if sid:
        data["Sid"] = sid
    j = json.loads(korail._session.post(url, data=data, headers=headers).text)
    if j.get("strResult") != "SUCC":
        return []
    out = []
    for row in j.get("reservation_list") or []:
        try:
            ti = row["ticket_list"][0]["train_info"][0]
        except Exception:
            continue
        if str(ti.get("h_tk_knd_cd")) != "81":
            continue
        ident = {"h_orgtk_wct_no": ti.get("h_orgtk_wct_no", ""), "h_orgtk_ret_sale_dt": ti.get("h_orgtk_ret_sale_dt", ""),
                 "h_orgtk_sale_sqno": ti.get("h_orgtk_sale_sqno", ""), "h_orgtk_ret_pwd": ti.get("h_orgtk_ret_pwd", "")}
        det_url = _k2.KORAIL_MOBILE + ".refunds.SelTicketInfo"
        d2 = {"Device": korail._device, "Version": korail._version, "Key": korail._key, "h_rsv_chg_no": "00000", **ident}
        h2, sid2 = korail._get_auth_headers_and_sid(det_url)
        if sid2:
            d2["Sid"] = sid2
        try:
            det = json.loads(korail._session.post(det_url, data=d2, headers=h2).text)
        except Exception:
            det = {}
        ci = det.get("dcnt_crd_info") or {}
        def _n(v):
            try: return int(str(v or "0").lstrip("0") or "0")
            except Exception: return 0
        segs = [{"dep": s.get("dptRsStnNm"), "arr": s.get("arvRsStnNm"), "dep_cd": s.get("dptRsStnCd"), "arr_cd": s.get("arvRsStnCd"), "trn_gp_cd": s.get("trnGpCd")}
                for s in (ci.get("appSegList") or [])]
        if not segs:
            segs = [{"dep": ti.get("h_dpt_rs_stn_nm"), "arr": ti.get("h_arv_rs_stn_nm"), "dep_cd": ti.get("h_dpt_rs_stn_cd"), "arr_cd": ti.get("h_arv_rs_stn_cd"), "trn_gp_cd": ""}]
        out.append({
            "card_no": ci.get("h_dcnt_crd_no") or "",                      # 예약 txtCardNo 값(내부 번호)
            "ticket_no": ident["h_orgtk_wct_no"] + ident["h_orgtk_ret_sale_dt"] + ident["h_orgtk_sale_sqno"] + ident["h_orgtk_ret_pwd"],
            "kind": ci.get("h_dcnt_crd_knd_cd") or "", "name": ti.get("h_tk_knd_nm") or "N카드",
            "segments": segs, "valid_from": ti.get("h_dpt_dt") or "", "valid_to": ti.get("h_arv_dt") or "",
            "used": _n(ci.get("h_use_tno")), "remaining": _n(ci.get("h_noty_use_tno")), "price": _n(ti.get("h_rcvd_amt")),
            "ok": bool(ci.get("h_dcnt_crd_no")),
        })
    return out


def _reservation_pay_window(korail: Korail) -> Dict[str, dict]:
    """예약별 결제 가능 기간 — korail2 Reservation 이 안 들고 있는 필드라 원본 응답에서 직접 읽는다.
      h_ntisu_psb_dt  결제 시작일(있는 예약만 — 명절 등 사전 예약)
      h_ntisu_lmt_dt/tm  결제 기한
      h_payment_flg   'Y'면 지금 결제 가능
    {pnr: {"buy_start", "pay_open", "payment_msg"}} (2026-09-09 실측)"""
    out: Dict[str, dict] = {}
    try:
        url = _k2.KORAIL_MYRESERVATIONLIST
        data = {"Device": korail._device, "Version": korail._version, "Key": korail._key}
        headers, sid = korail._get_auth_headers_and_sid(url)
        if sid:
            data["Sid"] = sid
        j = json.loads(korail._session.post(url, data=data, headers=headers).text)
        for jr in ((j.get("jrny_infos") or {}).get("jrny_info") or []):
            for ti in ((jr.get("train_infos") or {}).get("train_info") or []):
                pnr = (ti.get("h_pnr_no") or "").strip()
                if not pnr:
                    continue
                start = (ti.get("h_ntisu_psb_dt") or "").strip()
                out[pnr] = {
                    "buy_start": start if len(start) == 8 else "",
                    "pay_open": (ti.get("h_payment_flg") or "").upper() != "N",
                    "payment_msg": (ti.get("h_payment_msg") or "").strip(),
                }
    except Exception as e:
        logger.debug(f"[예약] 결제기간 조회 실패: {e}")
    return out


def ticket_detail(korail: Korail, wct_no: str, ret_sale_dt: str, sale_sqno: str, ret_pwd: str) -> dict:
    """승차권 1장의 좌석 상세(SelTicketInfo) — 내 티켓 카드를 탭할 때 지연 조회. 호차·좌석·할인명."""
    def _n(v):
        try: return int(str(v or "0").lstrip("0") or "0")
        except Exception: return 0
    du = _k2.KORAIL_MOBILE + ".refunds.SelTicketInfo"
    dd = {"Device": korail._device, "Version": korail._version, "Key": korail._key, "h_rsv_chg_no": "00000",
          "h_orgtk_wct_no": wct_no, "h_orgtk_ret_sale_dt": ret_sale_dt,
          "h_orgtk_sale_sqno": sale_sqno, "h_orgtk_ret_pwd": ret_pwd}
    dh, dsid = korail._get_auth_headers_and_sid(du)
    if dsid:
        dd["Sid"] = dsid
    det = json.loads(korail._session.post(du, data=dd, headers=dh).text)
    tinfo = ((det.get("ticket_infos") or {}).get("ticket_info") or [{}])[0]
    picks, disc = [], []
    for si in tinfo.get("tk_seat_info") or []:
        c, sn = _n(si.get("h_srcar_no")), str(si.get("h_seat_no") or "").strip()
        if c and sn:
            picks.append((c, sn))
        if si.get("h_dcnt_knd_nm"):
            disc.append(si["h_dcnt_knd_nm"])
    seat_pick = ""
    if picks:
        cars = {c for c, _ in picks}
        seat_pick = (f"{list(cars)[0]}호차 " if len(cars) == 1 else "") + "·".join((f"{c}호차 " if len(cars) > 1 else "") + sn for c, sn in picks)
    return {"seat_pick": seat_pick, "discount": ", ".join(dict.fromkeys(disc)), "std_price": _n(tinfo.get("h_std_seat_prc"))}


def my_tickets(korail: Korail) -> dict:
    """내 티켓 탭 — 미결제 예약(ReservationList)과 발권된 승차권(MyTicketList, 이용가능 티켓)을 한 번에.
    승차권 행은 h_tk_knd_cd '81'=N카드(카드 자체)라 별도 분리. 좌석은 예약 상세(reservation_seats)로 보강."""
    out = {"reservations": [], "tickets": [], "ncards": []}
    pay_win = _reservation_pay_window(korail)
    try:
        for r in korail.reservations():
            d = reservation_to_dict(r, "waiting" if getattr(r, "is_waiting", False) else "seat")
            d.update({"journey_no": r.journey_no, "journey_cnt": r.journey_cnt, "rsv_chg_no": r.rsv_chg_no})
            d.update(pay_win.get(str(r.rsv_id), {"buy_start": "", "pay_open": True, "payment_msg": ""}))
            try:
                got = reservation_seats(korail, r.rsv_id)
                d["seat_pick"] = seats_label(got["seats"]) if got.get("seats") else ""
            except Exception:
                d["seat_pick"] = ""
            out["reservations"].append(d)
    except Exception as e:
        out["reservations_error"] = str(e)[:80]
    try:
        url = _k2.KORAIL_MYTICKETLIST
        data = {"Device": korail._device, "Version": korail._version, "Key": korail._key,
                "txtIndex": "1", "h_page_no": "1", "txtDeviceId": "", "h_abrd_dt_from": "", "h_abrd_dt_to": ""}
        headers, sid = korail._get_auth_headers_and_sid(url)
        if sid:
            data["Sid"] = sid
        j = json.loads(korail._session.post(url, data=data, headers=headers).text)
        def _n(v):
            try: return int(str(v or "0").lstrip("0") or "0")
            except Exception: return 0
        for row in (j.get("reservation_list") or []) if j.get("strResult") == "SUCC" else []:
            try:
                ti = row["ticket_list"][0]["train_info"][0]
            except Exception:
                continue
            if str(ti.get("h_tk_knd_cd")) == "81":
                continue   # N카드 자체 → list_ncards
            # 좌석 상세(SelTicketInfo)는 초기 로드에서 생략하고, 카드를 탭하면 그때 조회한다(지연 로딩, 2026-09-09 지시).
            # 상세 조회에 필요한 키만 실어 보낸다.
            out["tickets"].append({
                "d_wct": ti.get("h_orgtk_wct_no", ""), "d_dt": ti.get("h_orgtk_ret_sale_dt", ""),
                "d_sqno": ti.get("h_orgtk_sale_sqno", ""), "d_pwd": ti.get("h_orgtk_ret_pwd", ""),
                "date": ti.get("h_dpt_dt") or row.get("h_dpt_dt"), "train_type": ti.get("h_trn_clsf_nm"), "train_no": (ti.get("h_trn_no") or "").lstrip("0"),
                "dep": ti.get("h_dpt_rs_stn_nm"), "arr": ti.get("h_arv_rs_stn_nm"), "dep_time": ti.get("h_dpt_tm"), "arr_time": ti.get("h_arv_tm"),
                "seat_pick": "", "seats": _n(ti.get("h_seat_cnt")) or 1,
                "seat_class": {"1": "일반실", "2": "특실"}.get(str(ti.get("h_psrm_cl_cd")), ""),
                "price": _n(ti.get("h_rcvd_amt")), "kind_name": ti.get("h_tk_knd_nm") or "", "status": ti.get("h_tk_stt_nm") or "",
                "pnr": ti.get("h_pnr_no") or "", "buyer": ti.get("h_buy_ps_nm") or "",
                "ticket_no": (ti.get("h_orgtk_wct_no") or "") + (ti.get("h_orgtk_ret_sale_dt") or "") + (ti.get("h_orgtk_sale_sqno") or "") + (ti.get("h_orgtk_ret_pwd") or ""),
            })
    except Exception as e:
        out["tickets_error"] = str(e)[:80]
    try:
        out["ncards"] = list_ncards(korail)
    except Exception as e:
        out["ncards_error"] = str(e)[:80]
    return out


def ncard_route_ok(card: dict, dep: str, arr: str) -> bool:
    """카드 적용 구간과 (출발,도착)이 양방향 중 하나로 일치하는지(실측: 광주송정→용산 편도 표도 할인가)."""
    d, a = (dep or "").strip(), (arr or "").strip()
    return any((s.get("dep") == d and s.get("arr") == a) or (s.get("dep") == a and s.get("arr") == d) for s in card.get("segments") or [])


def cancel_reservation(korail: Korail, rsv) -> bool:
    """예약 취소. korail2.cancel()은 GET에 data=(본문)를 써서 코레일이 400을 돌려주는 버그가 있어 params로 보낸다(2026-09-06 실측 SUCC)."""
    data = {"Device": korail._device, "Version": korail._version, "Key": korail._key,
            "txtPnrNo": rsv.rsv_id, "txtJrnySqno": rsv.journey_no, "txtJrnyCnt": rsv.journey_cnt, "hidRsvChgNo": rsv.rsv_chg_no}
    headers, sid = korail._get_auth_headers_and_sid(_k2.KORAIL_CANCEL)
    if sid:
        data["Sid"] = sid
    j = json.loads(korail._session.get(_k2.KORAIL_CANCEL, params=data, headers=headers).text)
    return bool(korail._result_check(j))


# ── 좌석 배치 그룹 ────────────────────────────────────────────
# 같은 열차종류라도 편성이 다르면 좌석 배치가 다르다. 2026-09-19 4개 노선 101편을 실측해
# (편성타입 × 호차번호대 × 방향)으로 묶으니 13그룹이 나왔다(seat_groups.json).
# 편성타입은 호차별 정원 벡터로 가른다 — 호차 목록은 잔여석에 따라 달라지지만 정원은 안 변한다.
_SEAT_GROUPS: Optional[dict] = None


def seat_groups() -> dict:
    """{groups: [...], by_train: {"KTX-산천|401": "그룹id"}}"""
    global _SEAT_GROUPS
    if _SEAT_GROUPS is None:
        try:
            with open(resource_path("seat_groups.json"), encoding="utf-8") as f:
                d = json.load(f)
        except Exception as e:
            logger.warning(f"seat_groups.json 로드 실패: {e}")
            d = {"groups": []}
        by, by_no, dup = {}, {}, set()
        for g in d.get("groups", []):
            for no in g.get("trains", []):
                by[f"{g['train_type']}|{no}"] = g["id"]
                if no in by_no and by_no[no] != g["id"]:
                    dup.add(no)
                by_no[no] = g["id"]
        # 열차번호가 두 그룹에 걸치면 번호만으로는 못 정한다 — 그런 번호는 뺀다
        for no in dup:
            by_no.pop(no, None)
        d["by_train"] = by
        d["by_no"] = by_no
        _SEAT_GROUPS = d
    return _SEAT_GROUPS


def group_id_by_clsf(clsf_cd: str, updown: str) -> str:
    """열차 조회 응답의 h_trn_clsf_cd 로 좌석 배치 그룹을 정한다.

    이게 코레일이 좌석배치도를 고르는 기준이고(웹 번들의 switch(h_trn_clsf_cd)),
    열차 목록에 이미 실려 오므로 좌석맵을 따로 열 필요가 없다. 열차번호 표와 달리
    시각표 개정에도 낡지 않는다(2026-09-09 확인: 07=382석, 0A/10=413석 두 종류)."""
    return seat_groups().get("by_clsf", {}).get(f"{(clsf_cd or '').strip()}|{updown}", "")


def train_group_id(train_type: str, train_no: str, updown: str = "") -> str:
    """열차종류 + 열차번호로 좌석 배치 그룹을 찾는다. 조사되지 않은 열차는 빈 문자열.

    ⚠️ 번호만으로 찾으면 안 된다. 시각표 개정으로 같은 번호에 다른 열차가 배정된다
    (2026-09-09 실측: 420이 7월엔 KTX 18량 14:21, 9월엔 KTX-산천 8량 13:40.
     과거 승차권에 16호차 기록이 남아 있는데 지금 420에는 16호차가 없다)."""
    return seat_groups()["by_train"].get(
        f"{(train_type or '').strip()}|{(train_no or '').strip()}", "")


def group_label(g: dict) -> str:
    return f"{g['train_type']} ({'상행' if g['updown'] == 'up' else '하행'})"


def ride_history(korail: Korail, days: int = 90) -> List[dict]:
    """과거 이용내역 — MyTicketList의 txtIndex='2'. (txtIndex='1'은 앞으로 탈 승차권이라 날짜 범위가 무시된다.)
    코레일이 약 3개월치만 보관한다(180·365일을 넣어도 90일과 같은 건수 — 2026-09-09 실측).
    반환 건과 시각·열차번호가 비어 있는 잔여 레코드는 버리고, 같은 날·구간·시각 중복(여행변경 짝)은 한 건으로 접는다."""
    today = datetime.now()
    url = _k2.KORAIL_MYTICKETLIST
    data = {"Device": korail._device, "Version": korail._version, "Key": korail._key,
            "txtIndex": "2", "h_page_no": "1", "txtDeviceId": "",
            "h_abrd_dt_from": (today - timedelta(days=days)).strftime("%Y%m%d"),
            "h_abrd_dt_to": today.strftime("%Y%m%d")}
    headers, sid = korail._get_auth_headers_and_sid(url)
    if sid:
        data["Sid"] = sid
    j = json.loads(korail._session.post(url, data=data, headers=headers).text)
    if j.get("strResult") != "SUCC":
        raise KorailError(j.get("h_msg_txt") or "이용내역 조회 실패", j.get("h_msg_cd") or "HIST_FAIL")
    seen, out = set(), []
    for row in (j.get("reservation_list") or []):
        for tk in (row.get("ticket_list") or []):
            for ti in (tk.get("train_info") or []):
                if ti.get("h_tk_stt_nm") == "반환":
                    continue
                tm = (ti.get("h_dpt_tm") or "")[:4]
                trn_no = (ti.get("h_trn_no") or "").strip()
                run_dt = (ti.get("h_dpt_dt") or "").strip()
                if len(tm) != 4 or not tm.isdigit() or not trn_no or len(run_dt) != 8:
                    continue
                dep, arr = ti.get("h_dpt_rs_stn_nm") or "", ti.get("h_arv_rs_stn_nm") or ""
                if not dep or not arr:
                    continue
                key = (run_dt, tm, dep, arr)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    wd = datetime.strptime(run_dt, "%Y%m%d").weekday()   # 0=월
                except Exception:
                    continue
                out.append({"date": run_dt, "weekday": wd, "time": tm[:2] + ":" + tm[2:],
                            "minutes": int(tm[:2]) * 60 + int(tm[2:]), "dep": dep, "arr": arr,
                            "train_no": trn_no, "train_type": ti.get("h_trn_clsf_nm") or ""})
    out.sort(key=lambda r: (r["date"], r["minutes"]))
    return out


# 프리셋 자동 등록: 출발시각이 1시간 이내로 붙어 있으면 같은 일정으로 본다.
HIST_CLUSTER_GAP_MIN = 60
HIST_MIN_RIDES = 3
HIST_PAD_MIN = 30       # 프리셋 조회 창을 클러스터 앞뒤로 넓히는 폭


def history_clusters(rides: List[dict], gap_min: int = HIST_CLUSTER_GAP_MIN,
                     min_rides: int = HIST_MIN_RIDES) -> List[dict]:
    """(출발역, 도착역, 요일)로 묶고 그 안에서 출발시각을 gap_min 이내 간격끼리 이어붙인다.
    min_rides회 이상 탄 클러스터만 남긴다 — 나머지는 보여주지도 않는다(2026-09-09 지시)."""
    groups: Dict[Tuple[str, str, int], List[dict]] = {}
    for r in rides:
        groups.setdefault((r["dep"], r["arr"], r["weekday"]), []).append(r)

    out: List[dict] = []
    for (dep, arr, wd), items in groups.items():
        items = sorted(items, key=lambda r: r["minutes"])
        run: List[dict] = []
        for r in items + [None]:
            if run and (r is None or r["minutes"] - run[-1]["minutes"] > gap_min):
                if len(run) >= min_rides:
                    out.append(_cluster_to_preset(dep, arr, wd, run))
                run = []
            if r is not None:
                run.append(r)
    # 많이 탄 순 → 요일 → 시각
    out.sort(key=lambda c: (-c["rides"], c["weekday"], c["start_min"]))
    return out


def _cluster_to_preset(dep: str, arr: str, wd: int, run: List[dict]) -> dict:
    lo, hi = run[0]["minutes"], run[-1]["minutes"]
    def _hhmm(m):
        m = max(0, min(1439, m))
        return f"{m // 60:02d}:{m % 60:02d}"
    trains, seen = [], set()
    for r in sorted(run, key=lambda x: x["minutes"]):
        sig = (r["train_type"], r["train_no"])
        if sig not in seen:
            seen.add(sig)
            trains.append({"train_type": r["train_type"], "train_no": r["train_no"], "time": r["time"]})
    return {
        "dep": dep, "arr": arr,
        "weekday": (wd + 1) % 7,                 # 0=월(파이썬) → 0=일(JS Date.getDay)
        "rides": len(run),
        "start_min": lo, "end_min": hi,
        "actual_start": _hhmm(lo), "actual_end": _hhmm(hi),
        "startTime": _hhmm(lo - HIST_PAD_MIN), "endTime": _hhmm(hi + HIST_PAD_MIN),
        "trains": trains,
        "last_date": max(r["date"] for r in run),
    }


def history_presets(korail: Korail, days: int = 90) -> dict:
    """온보딩·'자동 등록'이 쓰는 진입점. {rides:[...], clusters:[...]}"""
    rides = ride_history(korail, days=days)
    return {"name": korail.name or "", "rides": rides, "clusters": history_clusters(rides)}


def reservation_seats(korail: Korail, pnr: str) -> dict:
    """예약 상세(certification.ReservationList, hidPnrNo)로 실제 배정된 호차·좌석 조회 — ReservationView엔 좌석번호가 없음.
    letskorail이 쓰는 엔드포인트(2026-09-06 실측). {seats:[{car_no, seat_no, label, raw}], raw_keys} 반환."""
    url = _k2.KORAIL_MOBILE + ".certification.ReservationList"
    data = {"Device": korail._device, "Version": korail._version, "Key": korail._key, "hidPnrNo": pnr}
    headers, sid = korail._get_auth_headers_and_sid(url)
    if sid:
        data["Sid"] = sid
    j = json.loads(korail._session.post(url, data=data, headers=headers).text)
    korail._result_check(j)
    out, keys = [], []
    for jr in (j.get("jrny_infos") or {}).get("jrny_info") or []:
        for si in (jr.get("seat_infos") or {}).get("seat_info") or []:
            keys = keys or sorted(si.keys())
            car = str(si.get("h_srcar_no") or "").strip()
            seat = str(si.get("h_seat_no") or "").strip()
            out.append({"car_no": car, "seat_no": seat, "label": f"{int(car) if car.isdigit() else car}호차 {seat}", "raw": si})
    return {"seats": out, "raw_keys": keys}


def seats_label(seats: List[dict]) -> str:
    """'16호차 4C·5A' 형태. label 에 이미 호차가 붙어 있으면 떼고 쓴다
    (reservation_seats 는 '16호차 4C'를, 좌석맵은 '4C'를 준다 — 그대로 이으면 호차가 두 번 나온다)."""
    if not seats:
        return ""
    car = int(seats[0]["car_no"]) if str(seats[0].get("car_no", "")).strip().isdigit() else seats[0].get("car_no")
    names = []
    for x in seats:
        lab = str(x.get("label") or x.get("seat_no") or "")
        names.append(re.sub(r"^\s*\d+\s*호차\s*", "", lab))
    return f"{car}호차 " + "·".join(names)


def reservation_to_dict(rsv, kind: str, seat_label: str = "") -> dict:
    """예약 결과를 UI 카드용 구조로. kind: seat|waiting|standing. rsv가 None이면 raw만."""
    g = lambda k, d=None: getattr(rsv, k, d) if rsv is not None else d
    return {
        "kind": kind, "raw": str(rsv) if rsv is not None else "",
        "rsv_id": g("rsv_id"), "train_type": g("train_type_name"), "train_no": g("train_no"),
        "date": g("dep_date"), "dep": g("dep_name"), "arr": g("arr_name"),
        "dep_time": g("dep_time"), "arr_time": g("arr_time"),
        "seat_label": seat_label, "seats": g("seat_no_count"), "price": g("price"),
        "buy_limit": (g("buy_limit_date") or "") + (g("buy_limit_time") or ""),   # yyyymmddHHMMSS
    }


def _reserve_standing(korail: Korail, train, adults: int):
    """입석(standing) 예약 — korail2가 미지원하여 예약 페이로드를 직접 구성한다.
    좌석 예약과 동일하되 txtStndFlg='Y'(입석)로 호출. txtJobId는 좌석과 같은 '1101' 추정.
    ⚠️ 미검증(로그인 degraded). 첫 실사용은 반드시 watched. 실패 시 KorailError 발생."""
    psgs = _k2.Passenger.reduce([AdultPassenger(adults)])
    cnt = sum(p.count for p in psgs)
    url = _k2.KORAIL_TICKETRESERVATION
    headers, sid = korail._get_auth_headers_and_sid(url)
    data = {
        "Device": korail._device, "Version": korail._version, "Key": korail._key,
        "txtGdNo": "", "txtJobId": "1101", "txtTotPsgCnt": cnt,
        "txtSeatAttCd1": "000", "txtSeatAttCd2": "000", "txtSeatAttCd3": "000",
        "txtSeatAttCd4": "015", "txtSeatAttCd5": "000",
        "hidFreeFlg": "N", "txtStndFlg": "Y",   # ← 입석 플래그 (좌석 예약은 'N')
        "txtMenuId": "11", "txtSrcarCnt": "0", "txtJrnyCnt": "1",
        "txtJrnySqno1": "001", "txtJrnyTpCd1": "11",
        "txtDptDt1": train.dep_date, "txtDptRsStnCd1": train.dep_code,
        "txtDptTm1": train.dep_time, "txtArvRsStnCd1": train.arr_code,
        "txtTrnNo1": train.train_no, "txtRunDt1": train.run_date,
        "txtTrnClsfCd1": train.train_type, "txtPsrmClCd1": "1",
        "txtTrnGpCd1": train.train_group, "txtChgFlg1": "",
        "txtJrnySqno2": "", "txtJrnyTpCd2": "", "txtDptDt2": "", "txtDptRsStnCd2": "",
        "txtDptTm2": "", "txtArvRsStnCd2": "", "txtTrnNo2": "", "txtRunDt2": "",
        "txtTrnClsfCd2": "", "txtPsrmClCd2": "", "txtChgFlg2": "",
    }
    idx = 1
    for psg in psgs:
        data.update(psg.get_dict(idx)); idx += 1
    logger.info(f"입석 시도 · {train.train_no} {train.dep_name}→{train.arr_name}")
    r = korail._session.get(url, params=data, headers=headers)
    j = json.loads(r.text)
    if korail._result_check(j):
        rsv_id = j.get("h_pnr_no")
        for rv in korail.reservations():
            if getattr(rv, "rsv_id", None) == rsv_id:
                return rv
        return None
    raise KorailError("입석 예약 응답 확인 실패", "STANDING_FAIL")


# ── 토스페이 즉시 자동결제 (korail2 세션 그대로 사용, nodriver/재로그인 불필요) ──
# ⚠️ 실제 결제. 친구 리버스엔지니어링 스펙 기반 — 첫 실사용은 watched 권장.
def _korail_common(korail: Korail) -> dict:
    return {"Device": korail._device, "Version": korail._version, "Key": korail._key}


# 코레일 계정에 등록된 간편결제 키. spayDvCd 13=토스페이 (2026-09-09 실측)
SPAY_TOSS = "13"


def simple_pay_keys(korail: Korail) -> dict:
    """등록된 간편결제 수단 조회 — 토스페이는 이 키를 그대로 결제에 쓴다(앱에서 따로 입력할 값이 없다)."""
    url = _k2.KORAIL_MOBILE + ".pay.stlKeyQry.do"
    data = _korail_common(korail); data["spayDvCd"] = "12"
    j = json.loads(korail._session.post(url, data=data).text)
    out = {"ok": j.get("strResult") == "SUCC", "toss": False, "kinds": []}
    for sp in (j.get("spayList") or []):
        dv = str(sp.get("spayDvCd") or "")
        out["kinds"].append(dv)
        if dv == SPAY_TOSS and sp.get("spayStlKeyVal"):
            out["toss"] = True
    return out


def _pay_amount(reserve_raw: dict) -> str:
    """결제에 실을 금액. 예약 응답의 **h_tot_rcvd_amt(실수령액)** 를 쓴다.
    h_tot_prc는 운임(할인 전)이라 일반실·무할인일 때만 우연히 총액과 같다 — 특실요금(h_tot_fare)이나
    N카드 할인(h_tot_dcnt_amt)이 붙으면 어긋나 WRTV08009 '금액오류'가 난다(2026-09-09 실측:
    prc 42,000 + fare 18,900 − dcnt 6,600 = rcvd 54,300)."""
    for k in ("h_tot_rcvd_amt", "h_tot_prc", "h_rsv_amt"):
        v = re.sub(r"\D", "", str(reserve_raw.get(k) or ""))
        if v and int(v) > 0:
            return str(int(v))
    return ""


def _amount_fields(d: dict) -> str:
    """예약 응답에서 금액으로 보이는 필드를 전부 뽑아 한 줄로. 결제 '금액오류' 추적용."""
    if not isinstance(d, dict):
        return "(raw 없음)"
    out = []
    for k in sorted(d):
        if any(w in k for w in ("prc", "amt", "fare", "disc")):
            v = str(d[k]).strip()
            if v and v.strip("0"):        # 전부 0인 필드는 잡음이라 뺀다
                out.append(f"{k}={v}")
    return " · ".join(out) or "(금액 필드 없음)"


def _pay_submit(korail: Korail, reserve_raw: dict, stl: dict, label: str) -> dict:
    """결제 수단별 파라미터(stl)를 얹어 ReservationPayment 호출. 성공 판정·로깅은 공통."""
    sess = korail._session
    purl = _k2.KORAIL_MOBILE + ".payment.ReservationPayment"
    amt = _pay_amount(reserve_raw)
    logger.info(f"[{label}] 보낼 금액 hidMnsStlAmt1={amt!r} · 예약응답 {_amount_fields(reserve_raw)}")
    pdata = _korail_common(korail)
    pdata.update({
        "hidPnrNo": reserve_raw.get("h_pnr_no", ""),
        "hidWctNo": reserve_raw.get("h_wct_no", ""),
        "hidTmpJobSqno1": reserve_raw.get("h_tmp_job_sqno1", ""),
        "hidTmpJobSqno2": reserve_raw.get("h_tmp_job_sqno2", ""),
        "hidInrecmnsGridcnt": "1",
        "hidStlMnsSqno1": "1",
        "hidMnsStlAmt1": amt,
        "hiduserYn": "Y",
    })
    pdata.update(stl)
    txt = sess.post(purl, data=pdata).text
    try:
        pj = json.loads(txt)
    except Exception:
        pj = {}
    ok = ("정상발매" in txt) or ("정상발권" in txt) or (pj.get("strResult") == "SUCC")
    if not ok:
        msg = pj.get("h_msg_txt") or pj.get("strResult") or txt[:120]
        # 실패 응답 전문을 남긴다 — h_msg_txt만으로는 어느 값이 틀렸는지 알 수 없다
        logger.warning(f"[{label}] 실패 응답: {txt[:600]}")
        raise KorailError(f"{label} 실패: {msg}", "PAY_FAIL")
    logger.info(f"결제 완료 · PNR {reserve_raw.get('h_pnr_no')} · {amt}원")
    return {"ok": True, "amount": amt, "pnr": reserve_raw.get("h_pnr_no")}


# 신용카드 직접결제 파라미터는 코레일 웹 번들의 카드결제 핸들러에서 확인(2026-09-09).
#   hidStlMnsCd1='02'(카드) · hidCrdInpWayCd1='@'(직접입력)
#   hidCrdVlidTrm1=YYMM · hidVanPwd1=카드비밀번호 앞2자리
#   hidAthnDvCd1='J'(개인, 생년월일 6자리) | 'S'(법인, 사업자번호 10자리) · hidAthnVal1=그 값
#   hidIsmtMnthNum1=할부개월(0=일시불, 5만원 미만은 할부 불가)
CARD_MIN_INSTALLMENT_AMOUNT = 50000


def card_check(card: dict, amount: int = 0) -> Optional[str]:
    """카드 입력값 검증 — 코레일 웹과 같은 규칙. 통과하면 None, 아니면 사유 문자열."""
    no = re.sub(r"\D", "", str(card.get("no") or ""))
    if len(no) < 12:
        return "카드번호를 12자리 이상 입력하세요"
    exp = re.sub(r"\D", "", str(card.get("exp") or ""))
    if len(exp) != 4:
        return "유효기간을 YYMM 4자리로 입력하세요"
    if not (1 <= int(exp[2:]) <= 12):
        return "유효기간의 월은 01~12만 가능합니다"
    if len(re.sub(r"\D", "", str(card.get("pw") or ""))) != 2:
        return "카드 비밀번호는 앞 2자리입니다"
    kind = str(card.get("auth_kind") or "J")
    val = re.sub(r"\D", "", str(card.get("auth_val") or ""))
    if kind == "S":
        if len(val) != 10:
            return "법인카드는 사업자번호 10자리를 입력하세요"
    elif len(val) != 6:
        return "개인카드는 생년월일 6자리를 입력하세요"
    ismt = int(card.get("installment") or 0)
    if ismt > 0 and amount and amount < CARD_MIN_INSTALLMENT_AMOUNT:
        return f"할부는 {CARD_MIN_INSTALLMENT_AMOUNT:,}원 이상부터 가능합니다"
    return None


def pay_with_card(korail: Korail, reserve_raw: dict, card: dict) -> dict:
    """신용카드 직접 결제(1콜). card = {no, exp(YYMM), pw(앞2자리), auth_kind('J'|'S'), auth_val, installment}"""
    if not reserve_raw or not reserve_raw.get("h_pnr_no"):
        raise KorailError("예약 raw 응답 없음 — 카드결제 불가", "CARD_NO_RAW")
    try:
        amount = int(re.sub(r"\D", "", _pay_amount(reserve_raw)) or 0)
    except Exception:
        amount = 0
    bad = card_check(card, amount)
    if bad:
        raise KorailError(f"카드 정보 오류: {bad}", "CARD_BAD_INPUT")
    exp = re.sub(r"\D", "", str(card["exp"]))
    return _pay_submit(korail, reserve_raw, {
        "hidStlMnsCd1": "02",
        "hidCrdInpWayCd1": "@",
        "hidStlCrCrdNo1": re.sub(r"\D", "", str(card["no"])),
        "hidCrdVlidTrm1": exp,
        "hidVanPwd1": re.sub(r"\D", "", str(card["pw"])),
        "hidAthnDvCd1": str(card.get("auth_kind") or "J"),
        "hidAthnVal1": re.sub(r"\D", "", str(card.get("auth_val") or "")),
        "hidIsmtMnthNum1": str(int(card.get("installment") or 0)),
    }, "카드결제")


def pay_with_toss(korail: Korail, reserve_raw: dict) -> dict:
    """예약 직후 raw 응답 + 코레일 세션으로 토스페이 결제(2콜). 전제: 코레일 계정에 토스페이 미리 연결."""
    if not reserve_raw or not reserve_raw.get("h_pnr_no"):
        raise KorailError("예약 raw 응답 없음 — 토스결제 불가", "TOSS_NO_RAW")
    # ① 결제키 조회 (등록된 토스 결제키 가져오기)
    qurl = _k2.KORAIL_MOBILE + ".pay.stlKeyQry.do"
    qdata = _korail_common(korail); qdata["spayDvCd"] = "12"
    qj = json.loads(korail._session.post(qurl, data=qdata).text)
    toss_key = None
    for sp in (qj.get("spayList") or []):
        if str(sp.get("spayDvCd")) == SPAY_TOSS:
            toss_key = sp.get("spayStlKeyVal"); break
    if not toss_key:
        raise KorailError("토스 결제키 없음 — 코레일 계정에 토스페이 연결 필요", "TOSS_NO_KEY")
    # ② 결제
    return _pay_submit(korail, reserve_raw, {
        "hidStlMnsCd1": "14",
        "hidCrdInpWayCd1": "D",
        "stSpayGridcnt_1": "1",
        "spayDvCd_1_1": "13",
        "spayCphdDatVal_1_1": "Y" + toss_key,
    }, "토스결제")


# ── 핵심 루프 ─────────────────────────────────────────────────
async def runner_loop(app: Application, chat_id: int, p: Dict[str, Any]):
    STATE.running    = True
    STATE.started_at = datetime.now()
    STATE.attempts   = 0
    loop = asyncio.get_event_loop()

    try:
        korail = await loop.run_in_executor(_executor, _make_korail)
    except Exception as e:
        STATE.running = False
        STATE.task = None
        await app.bot.send_message(chat_id, f"❌ 코레일 로그인 실패: {e}")
        return

    await app.bot.send_message(
        chat_id=chat_id,
        text=(
            "✅ 감시 시작! (korail2 API)\n"
            f"📅 날짜: {p['date_yyyymmdd']}\n"
            f"🚆 구간: {p['dep']} → {p['arr']}\n"
            f"⏰ 시간: {p['start_hhmm']} ~ {p['end_hhmm']}\n"
            f"👥 인원: 성인 {p['adults']}명\n"
            f"🔄 주기: {p['interval_sec']}초 (±{p['jitter_sec']}초)"
        )
    )

    try:
        while True:
            STATE.last_check_at = datetime.now()
            STATE.attempts += 1

            if STATE.attempts % RELOGIN_EVERY == 0:
                try:
                    korail = await loop.run_in_executor(_executor, _make_korail)
                    logger.info(f"[세션 갱신] attempt={STATE.attempts}")
                except Exception as e:
                    logger.warning(f"[세션 갱신 실패] {e}")

            try:
                candidates = await loop.run_in_executor(_executor, _build_candidates, korail, p)
                STATE.last_candidates = len(candidates)
                logger.info(f"[조회 #{STATE.attempts}] 후보 {len(candidates)}개")

                for (train, seat) in candidates:
                    try:
                        rsv = await loop.run_in_executor(
                            _executor, _reserve, korail, train, p["adults"], p["option"], seat
                        )
                        STATE.last_success = str(rsv)
                        logger.info(f"[예약 성공] {rsv}")
                        dep_hhmm = train.dep_time[:4]
                        await app.bot.send_message(
                            chat_id=chat_id,
                            text=(
                                f"🎉 예약 성공! (출발 {dep_hhmm[:2]}:{dep_hhmm[2:]})\n\n"
                                f"{rsv}\n\n"
                                "※ 코레일 앱/웹에서 구입기한 내 결제하세요.\n"
                                "/start 로 다음 예약 가능"
                            )
                        )
                        reset_state()
                        return
                    except SoldOutError:
                        continue
                    except Exception as e:
                        STATE.last_error = repr(e)
                        logger.warning(f"[예약 실패] {e}")
                        continue

            except Exception as e:
                STATE.last_error = repr(e)
                logger.warning(f"[조회 실패] {e}")

            delay = compute_delay(p["interval_sec"], p["jitter_sec"])
            STATE.next_delay_sec = delay
            await asyncio.sleep(delay)

    except asyncio.CancelledError:
        logger.info("[runner] 취소됨")
    finally:
        STATE.running = False
        STATE.task = None


# ── 자연어 파싱 ──────────────────────────────────────────────
def parse_korean_booking(text: str) -> Optional[Dict[str, Any]]:
    date_m = re.search(r'(?:(\d{4})년\s*)?(\d{1,2})월\s*(\d{1,2})일', text)
    if not date_m:
        return None
    yyyy = date_m.group(1) or str(datetime.now().year)
    mm   = date_m.group(2).zfill(2)
    dd   = date_m.group(3).zfill(2)
    date_yyyymmdd = f"{yyyy}{mm}{dd}"
    date_yyMMdd   = date_yyyymmdd[2:]

    time_m = re.search(
        r'(\d{1,2})시(?:\s*(\d{1,2})분)?\s*(?:부터|에서|~|-)\s*(\d{1,2})시(?:\s*(\d{1,2})분)?',
        text
    )
    if not time_m:
        return None
    sh = time_m.group(1).zfill(2) + (time_m.group(2) or "00").zfill(2)
    eh = time_m.group(3).zfill(2) + (time_m.group(4) or "00").zfill(2)

    dep_m = re.search(r'(\S+?)역?출발', text)
    if not dep_m:
        return None

    arr_m = re.search(r'(\S+?)역?도착', text)
    if not arr_m:
        return None

    adults_m = re.search(r'(\d+)\s*(?:개|명|장|매)', text)
    adults = min(max(int(adults_m.group(1)), 1), 6) if adults_m else 1

    interval_m = re.search(r'(\d+)\s*초', text)
    interval = int(interval_m.group(1)) if interval_m and 3 <= int(interval_m.group(1)) <= 600 else 10

    return {
        "date_yyMMdd":   date_yyMMdd,
        "date_yyyymmdd": date_yyyymmdd,
        "dep":           dep_m.group(1),
        "arr":           arr_m.group(1),
        "start_hhmm":    sh,
        "end_hhmm":      eh,
        "start_hhmmss":  hhmm_to_hhmmss(sh),
        "end_hhmmss":    hhmm_to_hhmmss(eh),
        "adults":        adults,
        "interval_sec":  interval,
        "jitter_sec":    DEFAULT_JITTER,
        "option":        ReserveOption.GENERAL_FIRST,
        "train_type":    TrainType.ALL,
    }


# ── 텔레그램 핸들러 ───────────────────────────────────────────
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await deny(update); return ConversationHandler.END
    if STATE.running:
        await update.message.reply_text("감시 중입니다. /stats 확인 또는 /stop 중단")
        return ConversationHandler.END
    await update.message.reply_text("코레일 자동예약 설정\n1) 날짜(YYMMDD) 입력. 예: 260301")
    return S_DATE

async def on_date(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not is_valid_yyMMdd(text):
        await update.message.reply_text("형식 오류. YYMMDD 예: 260301")
        return S_DATE
    context.user_data.update(date_yyMMdd=text, date_yyyymmdd=yyMMdd_to_yyyymmdd(text))
    await update.message.reply_text("2) 출발역. 예: 대전")
    return S_DEP

async def on_dep(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["dep"] = (update.message.text or "").strip()
    await update.message.reply_text("3) 도착역. 예: 서울")
    return S_ARR

async def on_arr(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["arr"] = (update.message.text or "").strip()
    await update.message.reply_text("4) 출발시간 시작(HHMM). 예: 1700")
    return S_START

async def on_start_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not _valid_hhmm(text):
        await update.message.reply_text("형식 오류. HHMM 예: 1700")
        return S_START
    context.user_data["start_hhmm"] = text
    await update.message.reply_text("5) 출발시간 끝(HHMM). 예: 2000")
    return S_END

async def on_end_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not _valid_hhmm(text):
        await update.message.reply_text("형식 오류. HHMM 예: 2000")
        return S_END
    if hhmmss_to_min(hhmm_to_hhmmss(text)) < hhmmss_to_min(hhmm_to_hhmmss(context.user_data["start_hhmm"])):
        await update.message.reply_text("끝 시간이 시작보다 빠릅니다. 다시 입력.")
        return S_END
    context.user_data["end_hhmm"] = text
    await update.message.reply_text("6) 성인 인원. 예: 2")
    return S_ADULTS

async def on_adults(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text.isdigit() or not 1 <= int(text) <= 6:
        await update.message.reply_text("1~6 숫자로 입력.")
        return S_ADULTS
    context.user_data["adults"] = int(text)
    await update.message.reply_text("7) 조회 주기(초). 예: 30")
    return S_INTERVAL

async def on_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    if not text.isdigit() or not 3 <= int(text) <= 600:
        await update.message.reply_text("3~600초 사이로 입력.")
        return S_INTERVAL

    if STATE.running:
        await update.message.reply_text("이미 감시 중입니다. /stop 후 재시작.")
        return ConversationHandler.END

    sh, eh = context.user_data["start_hhmm"], context.user_data["end_hhmm"]
    p = {
        "date_yyMMdd":   context.user_data["date_yyMMdd"],
        "date_yyyymmdd": context.user_data["date_yyyymmdd"],
        "dep":           context.user_data["dep"],
        "arr":           context.user_data["arr"],
        "start_hhmm":    sh,
        "end_hhmm":      eh,
        "start_hhmmss":  hhmm_to_hhmmss(sh),
        "end_hhmmss":    hhmm_to_hhmmss(eh),
        "adults":        context.user_data["adults"],
        "interval_sec":  int(text),
        "jitter_sec":    DEFAULT_JITTER,
        "option":        ReserveOption.GENERAL_FIRST,
        "train_type":    TrainType.ALL,
    }
    STATE.params = p
    task = asyncio.create_task(
        runner_loop(context.application, update.effective_chat.id, p)
    )
    STATE.task = task
    await update.message.reply_text("✅ 설정 완료! 감시 시작.")
    return ConversationHandler.END

async def cmd_book(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """자연어 한 줄 예약: /book 2026년 3월 1일 17시부터 20시사이에 오송출발 서울도착 기차표 2명 예약해줘"""
    if not authorized(update):
        await deny(update); return

    if STATE.running:
        await update.message.reply_text("이미 감시 중입니다. /stats 확인 또는 /stop 중단")
        return

    text = update.message.text or ""
    body = re.sub(r'^/book\s*', '', text).strip()
    if not body:
        await update.message.reply_text(
            "사용법:\n/book 2026년 3월 1일 17시부터 20시사이에 오송출발 서울도착 기차표 2명 예약해줘"
        )
        return

    p = parse_korean_booking(body)
    if not p:
        await update.message.reply_text(
            "파싱 실패. 형식 확인해주세요.\n\n"
            "예시:\n/book 2026년 3월 1일 17시부터 20시사이에 오송출발 서울도착 기차표 2명 예약해줘"
        )
        return

    if not is_valid_yyMMdd(p["date_yyMMdd"]):
        await update.message.reply_text(f"날짜가 이상합니다: {p['date_yyyymmdd']}")
        return
    if hhmmss_to_min(p["end_hhmmss"]) < hhmmss_to_min(p["start_hhmmss"]):
        await update.message.reply_text("끝 시간이 시작 시간보다 빠릅니다.")
        return

    await update.message.reply_text(
        f"📋 파싱 결과:\n"
        f"📅 {p['date_yyyymmdd']}  🚆 {p['dep']} → {p['arr']}\n"
        f"⏰ {p['start_hhmm']} ~ {p['end_hhmm']}  👥 성인 {p['adults']}명  🔄 {p['interval_sec']}초\n\n"
        "로그인 중..."
    )

    STATE.params = p
    task = asyncio.create_task(
        runner_loop(context.application, update.effective_chat.id, p)
    )
    STATE.task = task
    logger.info(f"[/book] 파싱 성공: {p}")

async def cmd_stats(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await deny(update); return
    if not STATE.started_at:
        await update.message.reply_text("대기 중. /start 또는 /book 으로 시작하세요.")
        return
    p = STATE.params
    lines = [
        f"{'✅ 감시 중' if STATE.running else '💤 대기 중'}",
        f"시작: {STATE.started_at.strftime('%m/%d %H:%M:%S')}",
        f"마지막 조회: {STATE.last_check_at.strftime('%H:%M:%S') if STATE.last_check_at else '-'}",
        f"조회 횟수: {STATE.attempts}회 / 후보: {STATE.last_candidates}개",
        f"다음 대기: {STATE.next_delay_sec or '-'}초",
        f"오류: {STATE.last_error or '없음'}",
    ]
    if p:
        lines += [
            "",
            f"📅 {p.get('date_yyMMdd')}  🚆 {p.get('dep')}→{p.get('arr')}",
            f"⏰ {p.get('start_hhmm')}~{p.get('end_hhmm')}  👥 {p.get('adults')}명  🔄 {p.get('interval_sec')}초",
        ]
    await update.message.reply_text("\n".join(lines))

async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await deny(update); return
    if STATE.task and not STATE.task.done():
        STATE.task.cancel()
    reset_state()
    await update.message.reply_text("⛔ 감시 중단 완료. /start 또는 /book 으로 재시작 가능.")

async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text(f"your_user_id = {u.id}")

async def cancel_conv(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not authorized(update):
        await deny(update); return ConversationHandler.END
    await update.message.reply_text("취소. /start 로 다시 시작하세요.")
    return ConversationHandler.END


def main():
    try:                      # 옛 CLI 봇 전용 — POSIX 에만 있다(윈도우 배포본에서는 이 함수를 쓰지 않는다)
        import fcntl
    except ModuleNotFoundError:
        logger.error("텔레그램 봇 모드는 이 플랫폼에서 지원하지 않습니다"); return
    lock_file = "/tmp/korail_bot.lock"
    lock_fp = open(lock_file, "w")
    try:
        fcntl.flock(lock_fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except IOError:
        logger.error("이미 실행 중인 korail 봇 인스턴스가 있습니다. 종료합니다.")
        return
    lock_fp.write(str(os.getpid()))
    lock_fp.flush()

    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", cmd_start)],
        states={
            S_DATE:     [MessageHandler(filters.TEXT & ~filters.COMMAND, on_date)],
            S_DEP:      [MessageHandler(filters.TEXT & ~filters.COMMAND, on_dep)],
            S_ARR:      [MessageHandler(filters.TEXT & ~filters.COMMAND, on_arr)],
            S_START:    [MessageHandler(filters.TEXT & ~filters.COMMAND, on_start_time)],
            S_END:      [MessageHandler(filters.TEXT & ~filters.COMMAND, on_end_time)],
            S_ADULTS:   [MessageHandler(filters.TEXT & ~filters.COMMAND, on_adults)],
            S_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, on_interval)],
        },
        fallbacks=[CommandHandler("cancel", cancel_conv)],
        allow_reentry=True,
    )
    app.add_handler(conv)
    app.add_handler(CommandHandler("book",  cmd_book))
    app.add_handler(CommandHandler("stats", cmd_stats))
    app.add_handler(CommandHandler("stop",  cmd_stop))
    app.add_handler(CommandHandler("myid",  cmd_myid))

    logger.info("코레일 자동예약 봇 시작 (korail2 API)")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
