"""윈도우 단독 실행 진입점.

이 창을 닫으면 감시가 멈춘다 — PC 앞에 없을 때도 계속 돌리려면 창을 열어 둔다.
임베디드 파이썬에는 tkinter가 없으므로(확인함) 별도 GUI 없이 콘솔 창 하나로 상태를 보여준다.
"""
import os
import socket
import sys
import threading
import time
import webbrowser

os.environ.setdefault("AUTOREZV_LOCAL", "1")
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:   # 빌드 시 주입된 구글 데스크톱 클라이언트(_env.py). 개발 환경에는 없다.
    import _env
    os.environ.setdefault("TRAINER_GOOGLE_ID", _env.GOOGLE_ID)
    os.environ.setdefault("TRAINER_GOOGLE_SECRET", _env.GOOGLE_SECRET)
except Exception:
    pass

_P0 = int(os.environ.get("AUTOREZV_PORT") or 8767)
PORTS = [_P0 + i for i in range(5)]


def _set_title(text: str) -> None:
    if sys.platform == "win32":
        try:
            import ctypes
            ctypes.windll.kernel32.SetConsoleTitleW(text)
        except Exception:
            pass


def _in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            s.bind(("127.0.0.1", port))
            return False
        except OSError:
            return True


def _already_running(port: int) -> bool:
    """이 앱이 이미 그 포트에 떠 있으면 True — 두 번 실행하면 브라우저만 열고 끝낸다."""
    try:
        import urllib.request, json
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/api/version", timeout=1.5) as r:
            return bool(json.load(r).get("ok"))
    except Exception:
        return False


def main() -> int:
    _set_title("기차놀이")
    port = None
    for p in PORTS:
        if not _in_use(p):
            port = p
            break
        if _already_running(p):
            print(f"이미 실행 중입니다 (포트 {p}). 브라우저를 엽니다.")
            webbrowser.open(f"http://127.0.0.1:{p}/")
            time.sleep(2)
            return 0
    if port is None:
        port = 0   # 전부 막혔으면 OS가 빈 포트를 준다

    import uvicorn
    from web_server import app
    from paths import DATA_DIR
    import updater
    updater.start_background()      # 바뀐 파일만 조용히 받아 둔다(적용은 재시작 때)

    cfg = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning",
                         http="h11", ws="none", lifespan="on", loop="asyncio",
                         timeout_graceful_shutdown=3)
    server = uvicorn.Server(cfg)
    t = threading.Thread(target=server.run, daemon=True)
    t.start()

    for _ in range(100):          # 실제로 열린 포트를 확인한 뒤 브라우저를 연다
        if server.started:
            break
        time.sleep(0.1)
    real = port
    try:
        real = server.servers[0].sockets[0].getsockname()[1]
    except Exception:
        pass
    url = f"http://127.0.0.1:{real}/"

    print()
    print(f"  기차놀이 (Trainer) v{updater.current_version()}")
    print("  " + "-" * 46)
    print(f"  주소     : {url}")
    print(f"  데이터   : {DATA_DIR}")
    print("  " + "-" * 46)
    print("  이 창을 닫으면 감시가 멈춥니다.")
    print("  브라우저 창만 닫는 것은 괜찮습니다.")
    print()
    _set_title(f"기차놀이 — 실행 중 ({real})")
    webbrowser.open(url)

    try:
        while t.is_alive():
            t.join(1)
    except KeyboardInterrupt:
        print("\n  종료하는 중…")
    finally:
        server.should_exit = True
        t.join(5)
    return 0


if __name__ == "__main__":
    sys.exit(main())
