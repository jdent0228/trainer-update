"""윈도우 배포본 자동 업데이트.

실행 파일(임베디드 파이썬 + 휠 ~30MB)은 그대로 두고, **바뀐 소스 파일만** 데이터 폴더의
덮어쓰기 층(DATA_DIR/app)에 내려받는다. paths.resource_path 와 sitecustomize 의 sys.path 가
이 층을 번들보다 먼저 보므로, 새 파일이 놓이면 다음 실행부터 새 코드가 뜬다.

한계: 새 파이썬 패키지가 필요한 버전은 이 방식으로 못 올린다. 그때는 매니페스트에
needs_exe=true 를 실어 보내고, 앱은 새 실행 파일을 받으라고 안내한다.
"""
import hashlib
import json
import os
import shutil
import threading
import time
import urllib.request

from paths import DATA_DIR, LOCAL_MODE, resource_path

FEED = os.environ.get("TRAINER_UPDATE_FEED") or \
    "https://raw.githubusercontent.com/jdent0228/trainer-update/main"
OVERLAY = os.path.join(DATA_DIR, "app")          # 적용된 새 파일이 사는 곳
STAGING = os.path.join(DATA_DIR, "app.staged")   # 받는 중인 파일
CHECK_EVERY = 6 * 3600

_LOCK = threading.Lock()
STATE = {
    "current": "",        # 지금 돌고 있는 버전
    "latest": "",         # 피드에서 본 최신 버전
    "ready": "",          # 내려받기까지 끝난 버전(재시작하면 적용)
    "needs_exe": False,   # 새 실행 파일이 필요한 버전
    "checking": False,
    "error": "",
    "checked_at": 0,
}


def _vt(v):
    try:
        return tuple(int(x) for x in str(v).strip().split("."))
    except Exception:
        return (0,)


def current_version() -> str:
    """적용된 덮어쓰기 층의 버전이 있으면 그것, 없으면 번들 버전."""
    for p in (os.path.join(OVERLAY, "VERSION"), resource_path("VERSION")):
        try:
            with open(p, encoding="utf-8") as f:
                v = f.read().strip()
            if v:
                return v
        except Exception:
            continue
    return "0.0.0"


def _fetch(url: str, timeout: int = 20) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "Trainer-Updater"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _staged_version() -> str:
    try:
        with open(os.path.join(STAGING, "VERSION"), encoding="utf-8") as f:
            return f.read().strip()
    except Exception:
        return ""


def _have(rel: str, want: str) -> bool:
    """이미 그 내용을 갖고 있는지 — 덮어쓰기 층 → 번들 순으로 본다."""
    for base in (OVERLAY, None):
        p = os.path.join(base, rel) if base else resource_path(rel)
        try:
            with open(p, "rb") as f:
                if hashlib.sha256(f.read()).hexdigest() == want:
                    return True
        except Exception:
            continue
    return False


def check_and_download() -> dict:
    """피드를 보고 새 버전이면 바뀐 파일만 받아 STAGING 에 쌓는다. 재시작 때 적용된다."""
    with _LOCK:
        if STATE["checking"]:
            return dict(STATE)
        STATE["checking"] = True
    try:
        STATE["error"] = ""
        STATE["current"] = current_version()
        mf = json.loads(_fetch(f"{FEED}/manifest.json").decode("utf-8"))
        latest = str(mf.get("version") or "")
        STATE["latest"] = latest
        STATE["needs_exe"] = bool(mf.get("needs_exe"))
        if not latest or _vt(latest) <= _vt(STATE["current"]):
            STATE["ready"] = ""
            return dict(STATE)
        if STATE["needs_exe"]:
            return dict(STATE)                 # 받아도 소용없다 — 새 exe 안내만
        if _staged_version() == latest:
            STATE["ready"] = latest            # 이미 다 받아 뒀다
            return dict(STATE)

        tmp = STAGING + ".part"
        shutil.rmtree(tmp, ignore_errors=True)
        os.makedirs(tmp, exist_ok=True)
        files = mf.get("files") or {}
        for rel, meta in files.items():
            want = meta.get("sha256") or ""
            dst = os.path.join(tmp, rel.replace("/", os.sep))
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if _have(rel, want):               # 안 바뀐 파일은 갖고 있는 걸 옮긴다
                src = os.path.join(OVERLAY, rel)
                if not os.path.exists(src):
                    src = resource_path(rel)
                shutil.copy2(src, dst)
                continue
            blob = _fetch(f"{FEED}/v/{latest}/{rel}")
            if want and hashlib.sha256(blob).hexdigest() != want:
                raise RuntimeError(f"{rel} 무결성 불일치")
            with open(dst, "wb") as f:
                f.write(blob)
        with open(os.path.join(tmp, "VERSION"), "w", encoding="utf-8") as f:
            f.write(latest)

        shutil.rmtree(STAGING, ignore_errors=True)
        os.replace(tmp, STAGING)
        STATE["ready"] = latest
    except Exception as e:
        STATE["error"] = str(e)[:120]
    finally:
        STATE["checking"] = False
        STATE["checked_at"] = int(time.time())
    return dict(STATE)


def apply_staged() -> bool:
    """받아 둔 파일을 덮어쓰기 층으로 옮긴다. 실행 중인 모듈은 이미 메모리에 있으므로 재시작이 필요하다."""
    if not _staged_version():
        return False
    old = OVERLAY + ".old"
    shutil.rmtree(old, ignore_errors=True)
    if os.path.isdir(OVERLAY):
        os.replace(OVERLAY, old)
    try:
        os.replace(STAGING, OVERLAY)
    except Exception:
        if os.path.isdir(old):
            os.replace(old, OVERLAY)           # 되돌린다
        raise
    shutil.rmtree(old, ignore_errors=True)
    return True


def start_background() -> None:
    """윈도우 단독 실행에서만 — 부팅 직후 한 번, 이후 6시간마다."""
    if not LOCAL_MODE:
        return
    def loop():
        time.sleep(20)                          # 첫 화면이 뜬 뒤에 조용히
        while True:
            try:
                check_and_download()
            except Exception:
                pass
            time.sleep(CHECK_EVERY)
    threading.Thread(target=loop, daemon=True).start()
