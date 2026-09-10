"""쓰기 파일과 읽기 전용 리소스의 위치를 한 곳에서 결정한다.

윈도우 배포본은 프로그램 폴더가 임시 폴더이거나 쓰기 금지일 수 있어(임베디드 파이썬 번들,
Program Files 설치) 상태 파일을 소스 옆에 두면 매 실행마다 사라지거나 부팅이 실패한다.
맥 운영본은 AUTOREZV_DATA_DIR 환경변수로 지금 쓰던 폴더를 그대로 가리켜 데이터가 움직이지 않는다.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))


def _default_data_dir() -> str:
    if sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "Trainer")
    if sys.platform == "darwin":
        return os.path.join(os.path.expanduser("~"), "Library", "Application Support", "Trainer")
    return os.path.join(os.path.expanduser("~"), ".local", "share", "Trainer")


DATA_DIR = os.path.abspath(os.environ.get("AUTOREZV_DATA_DIR") or _default_data_dir())
try:
    os.makedirs(DATA_DIR, exist_ok=True)
except Exception:
    DATA_DIR = _HERE   # 만들 수 없으면 최소한 동작은 하게(개발 환경 폴백)


def data_path(name: str) -> str:
    """읽고 쓰는 파일(감시 목록·로그·캐시·계정 DB)."""
    return os.path.join(DATA_DIR, name)


# 자동 업데이트로 내려받은 파일이 사는 덮어쓰기 층. 번들보다 먼저 본다.
APP_OVERLAY = os.path.join(DATA_DIR, "app")


def resource_path(name: str) -> str:
    """번들에 함께 실린 읽기 전용 리소스(static/ 등). 업데이트된 파일이 있으면 그쪽을 쓴다."""
    over = os.path.join(APP_OVERLAY, name)
    if os.path.exists(over):
        return over
    base = getattr(sys, "_MEIPASS", None) or _HERE
    return os.path.join(base, name)


# 윈도우 단독 실행 모드 — 인증·재개 정책·알림이 이 값으로 갈린다
LOCAL_MODE = os.environ.get("AUTOREZV_LOCAL") == "1" or bool(getattr(sys, "frozen", False))
