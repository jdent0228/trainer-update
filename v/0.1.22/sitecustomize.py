import os, sys, traceback

def _run():
    here = os.path.dirname(os.path.abspath(__file__))
    if here not in sys.path:
        sys.path.insert(0, here)
    # 자동 업데이트로 받은 파일을 번들보다 먼저 본다(%LOCALAPPDATA%\\Trainer\\app)
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or os.path.expanduser("~")
    over = os.path.join(os.environ.get("AUTOREZV_DATA_DIR") or os.path.join(base, "Trainer"), "app")
    if os.path.isdir(over):
        sys.path.insert(0, over)
    import app_main
    raise SystemExit(app_main.main())

if os.environ.get("TRAINER_NO_AUTORUN") != "1":
    try:
        _run()
    except SystemExit:
        raise
    except BaseException:
        traceback.print_exc()
        print()
        print("  기차놀이를 시작하지 못했습니다. 위 내용을 알려주세요.")
        print("  문의 jdent0228@gmail.com")
        try:
            input("  엔터를 누르면 닫힙니다... ")
        except Exception:
            pass
        raise SystemExit(1)
