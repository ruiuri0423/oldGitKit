"""Invoked by git via GIT_ASKPASS (and SSH_ASKPASS).

git runs this with the prompt text as argv[1] when it needs a username /
password / passphrase. We relay that prompt to the running gitkit TUI over a
local socket (address + shared token in the environment), wait for the user to
type the answer into a popup, and print it back on stdout for git to consume.

If anything is missing or fails we print nothing — git then falls back, and
because GIT_TERMINAL_PROMPT=0 is set it fails fast instead of hanging.
"""
import os
import socket
import sys


def main() -> None:
    prompt = sys.argv[1] if len(sys.argv) > 1 else ""
    addr = os.environ.get("GITKIT_ASKPASS_ADDR", "")
    token = os.environ.get("GITKIT_ASKPASS_TOKEN", "")
    if not addr:
        return
    host, _, port = addr.rpartition(":")
    try:
        sock = socket.create_connection((host or "127.0.0.1", int(port)), timeout=300)
    except (OSError, ValueError):
        return
    try:
        sock.sendall((token + "\n" + prompt.replace("\n", " ") + "\n").encode("utf-8"))
        data = b""
        while b"\n" not in data:
            chunk = sock.recv(4096)
            if not chunk:
                break
            data += chunk
        answer = data.split(b"\n", 1)[0].decode("utf-8", "replace")
        sys.stdout.write(answer)
    finally:
        sock.close()


if __name__ == "__main__":
    main()
