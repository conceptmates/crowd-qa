#!/usr/bin/env python3
"""Serves the live pages of a run over HTTP, so the 3D world and the square can be opened from another machine.

  square-serve.py <run> [--port N] [--bind ADDR]

Only what the pages need is served: everything under square/ and image files under lanes/ (the characters'
screenshots the pages link to). config.env, context.md, state/ and logs are never served: they hold accounts,
keys and paths. --bind defaults to SQUARE_BIND from config.env, else this machine's Tailscale address, else
127.0.0.1; 0.0.0.0 shares on every network. A busy port moves to the next free one. The URLs are printed and
written to square/URL.
"""
import functools
import http.server
import os
import socket
import subprocess
import sys

IMAGES = (".png", ".jpg", ".jpeg", ".gif", ".webp")


def config(run):
    out = subprocess.run(["bash", "-c", f'set -a; . "{run}/config.env" >/dev/null 2>&1; env -0'],
                         capture_output=True).stdout.decode()
    return dict(kv.split("=", 1) for kv in out.split("\0") if "=" in kv)


def tailscale_ip():
    for cmd in (["tailscale", "ip", "-4"], ["/Applications/Tailscale.app/Contents/MacOS/Tailscale", "ip", "-4"]):
        try:
            ip = subprocess.run(cmd, capture_output=True, text=True, timeout=5).stdout.split()
            if ip:
                return ip[0]
        except (OSError, subprocess.TimeoutExpired):
            pass
    return ""


def lan_ip():
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return ""
    finally:
        s.close()


class Handler(http.server.SimpleHTTPRequestHandler):
    def send_head(self):
        rel = self.path.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        allowed = rel == "" or rel.startswith("square/") or (rel.startswith("lanes/") and rel.lower().endswith(IMAGES))
        if not allowed or ".." in rel.split("/"):
            self.send_error(404)
            return None
        if rel == "":
            self.send_response(302)
            self.send_header("Location", "/square/index.html")
            self.end_headers()
            return None
        return super().send_head()

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")   # state.json changes every pass
        super().end_headers()

    def log_message(self, *a):
        pass


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    run = os.path.abspath(sys.argv[1])
    args = dict(zip(sys.argv[2::2], sys.argv[3::2]))
    c = config(run)
    port = int(args.get("--port") or c.get("SQUARE_PORT") or 8790)
    bind = args.get("--bind") or c.get("SQUARE_BIND") or tailscale_ip() or "127.0.0.1"
    handler = functools.partial(Handler, directory=run)
    for p in range(port, port + 20):
        try:
            srv = http.server.ThreadingHTTPServer((bind, p), handler)
            break
        except OSError:
            continue
    else:
        sys.exit(f"no free port in {port}-{port + 19} on {bind}")
    if bind == "0.0.0.0":
        hosts = [h for h in dict.fromkeys([tailscale_ip(), lan_ip(), "localhost"]) if h]
    elif bind in ("127.0.0.1", "localhost"):
        hosts = ["localhost"]
    else:
        hosts = [bind]
    urls = [f"http://{h}:{p}/square/index.html" for h in hosts]
    os.makedirs(os.path.join(run, "square"), exist_ok=True)
    with open(os.path.join(run, "square", "URL"), "w") as f:
        f.write("\n".join(urls) + "\n")
    print("\n".join(urls), flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
