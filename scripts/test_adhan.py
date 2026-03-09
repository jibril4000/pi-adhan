#!/usr/bin/env python3
"""Test adhan playback with background audio integration.

Connects to the running background mpv process, fades it out,
plays a short adhan clip, then fades background back in.

Usage:
    python scripts/test_adhan.py          # 10 second test
    python scripts/test_adhan.py 20       # 20 second test
    python scripts/test_adhan.py full     # full adhan
"""

import os
import signal
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.background import BackgroundPlayer, SOCKET_PATH
from src.config import load_config


def main():
    config_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "config.yaml",
    )
    config = load_config(config_path)

    # Parse duration argument
    duration = None
    if len(sys.argv) > 1 and sys.argv[1] != "full":
        duration = int(sys.argv[1])
    elif len(sys.argv) <= 1:
        duration = 10

    # Find the running background mpv process via the socket
    bg = BackgroundPlayer(config)
    if os.path.exists(SOCKET_PATH):
        # Find the mpv PID from the socket
        import json
        import socket as sock
        try:
            s = sock.socket(sock.AF_UNIX, sock.SOCK_STREAM)
            s.settimeout(2)
            s.connect(SOCKET_PATH)
            s.sendall(json.dumps({"command": ["get_property", "pid"]}).encode() + b"\n")
            resp = json.loads(s.recv(4096).decode().strip().split("\n")[0])
            s.close()
            mpv_pid = resp.get("data")
            if mpv_pid:
                # Create a minimal process-like object for _freeze/_unfreeze
                class FakeProcess:
                    def __init__(self, pid):
                        self.pid = pid
                    def poll(self):
                        return None
                bg._process = FakeProcess(mpv_pid)
                print(f"Found background mpv (PID {mpv_pid})")
        except Exception as e:
            print(f"Could not connect to background mpv: {e}")
            bg._process = None
    else:
        print("No background audio running (socket not found)")
        bg._process = None

    # Fade out background
    if bg._process:
        print("Fading out background...")
        bg.notify_adhan_start()

    # Build mpv command
    cmd = [
        "mpv", "--no-video", "--really-quiet",
        f"--volume={config.audio.volume}",
    ]
    if duration:
        cmd.append(f"--length={duration}")
        print(f"Playing adhan ({duration}s test)...")
    else:
        print("Playing full adhan...")

    audio_path = os.path.join(config.base_dir, config.audio.default_file)
    cmd.append(audio_path)

    try:
        subprocess.run(cmd)
    except KeyboardInterrupt:
        print("\nAdhan interrupted.")
    finally:
        # Always fade back in, even on Ctrl+C
        if bg._process:
            print("Fading background back in...")
            bg.notify_adhan_end()

    print("Done.")


if __name__ == "__main__":
    main()
