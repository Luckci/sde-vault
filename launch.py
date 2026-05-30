"""
Double-click launcher: starts the Flask server and opens the browser automatically.
Also usable from the terminal: python launch.py
"""
import sys
import threading
import time
import webbrowser

PORT = 5000


def open_browser():
    time.sleep(1.5)
    webbrowser.open(f"http://localhost:{PORT}")


if __name__ == "__main__":
    from app import app

    t = threading.Thread(target=open_browser, daemon=True)
    t.start()

    print(f"📚 SDE Vault kører på http://localhost:{PORT}")
    print("Tryk Ctrl+C for at stoppe.\n")

    try:
        app.run(port=PORT, debug=False, threaded=True)
    except KeyboardInterrupt:
        print("\nServer stoppet.")
        sys.exit(0)
