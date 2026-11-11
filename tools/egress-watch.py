#!/usr/bin/env python3
"""egress-watch — 一个只负责记日志的本地 HTTP CONNECT 代理。

用来回答一个问题：某个程序到底往哪儿发数据。

把它的地址设成 HTTPS_PROXY，程序的所有 HTTPS 请求都会先经过它。
它只打印目标域名，不读也不改任何内容（TLS 是端到端透传的）。

用法:
    # 直连
    ./tools/egress-watch.py --port 8899

    # 经过已有的代理（比如 Clash / mihomo 的 mixed-port）
    ./tools/egress-watch.py --port 8899 --upstream 127.0.0.1:7897

    # 在另一个终端
    HTTPS_PROXY=http://127.0.0.1:8899 claude -p "say OK"

输出示例:
    07:41:02  CONNECT  flaios.com:443
    07:41:05  CONNECT  api.anthropic.com:443

按 Ctrl-C 结束，会打印去重后的域名汇总。
"""

from __future__ import annotations

import argparse
import signal
import socket
import sys
import threading
import time
from collections import Counter
from urllib.parse import urlsplit

LOCK = threading.Lock()
HOSTS: Counter[str] = Counter()
START = time.time()
QUIET = False


def log(line: str) -> None:
    if QUIET:
        return
    ts = time.strftime("%H:%M:%S")
    with LOCK:
        print(f"{ts}  {line}", flush=True)


def note_host(host: str) -> None:
    with LOCK:
        HOSTS[host] += 1


def pump(a: socket.socket, b: socket.socket) -> None:
    """双向搬运字节。任何一侧关闭就结束。"""
    try:
        while True:
            r, _, _ = select([a, b], [], [], 120)
            if not r:
                return
            for s in r:
                data = s.recv(65536)
                if not data:
                    return
                (b if s is a else a).sendall(data)
    except OSError:
        pass
    finally:
        for s in (a, b):
            try:
                s.close()
            except OSError:
                pass


def select(rlist, wlist, xlist, timeout):
    import select as _select
    return _select.select(rlist, wlist, xlist, timeout)


def upstream_open(host: str, port: int, upstream: tuple[str, int] | None) -> socket.socket:
    if upstream is None:
        return socket.create_connection((host, port), 15)

    s = socket.create_connection(upstream, 15)
    s.sendall(f"CONNECT {host}:{port} HTTP/1.1\r\nHost: {host}:{port}\r\n\r\n".encode())
    buf = b""
    while b"\r\n\r\n" not in buf:
        chunk = s.recv(4096)
        if not chunk:
            raise OSError("upstream closed during CONNECT")
        buf += chunk
    status = buf.split(b"\r\n", 1)[0].decode(errors="replace")
    if " 200 " not in status:
        s.close()
        raise OSError(f"upstream refused: {status}")
    return s


def handle(client: socket.socket, upstream: tuple[str, int] | None) -> None:
    server = None
    try:
        req = b""
        while b"\r\n\r\n" not in req:
            chunk = client.recv(65536)
            if not chunk:
                return
            req += chunk

        header, rest = req.split(b"\r\n\r\n", 1)
        lines = header.decode(errors="replace").split("\r\n")
        method, target, *_ = (lines[0].split() + ["", ""])[:3]

        if method == "CONNECT":
            host, _, port = target.partition(":")
            port = int(port or 443)
            note_host(host)
            log(f"CONNECT  {host}:{port}")
            server = upstream_open(host, port, upstream)
            client.sendall(b"HTTP/1.1 200 Connection Established\r\n\r\n")
            if rest:
                server.sendall(rest)
            threading.Thread(target=pump, args=(client, server), daemon=True).start()
            pump(server, client)
        else:
            url = urlsplit(target)
            host = url.hostname or ""
            port = url.port or 80
            note_host(host)
            log(f"HTTP     {method} {host}{url.path[:80]}")
            server = upstream_open(host, port, upstream)
            path = url.path + (f"?{url.query}" if url.query else "")
            out = f"{method} {path or '/'} HTTP/1.1\r\n"
            out += "\r\n".join(l for l in lines[1:] if not l.lower().startswith("proxy-connection"))
            out += "\r\n\r\n"
            server.sendall(out.encode() + rest)
            threading.Thread(target=pump, args=(client, server), daemon=True).start()
            pump(server, client)
    except Exception as e:  # 单个连接失败不该拖垮监听
        msg = str(e)
        # 复用连接被对端关闭是正常现象，不值得刷屏
        if not any(k in msg for k in ("Bad file descriptor", "Broken pipe",
                                      "Connection reset", "Connection aborted")):
            log(f"FAIL     {e}")
        try:
            client.close()
        except OSError:
            pass
        if server:
            try:
                server.close()
            except OSError:
                pass


def summary() -> None:
    with LOCK:
        hosts = HOSTS.most_common()
    took = time.time() - START
    out = [f"\n--- 共 {sum(HOSTS.values())} 次连接，{len(hosts)} 个域名，{took:.0f}s ---"]
    for host, n in hosts:
        out.append(f"  {n:>4}  {host}")

    suspicious = [
        h for h, _ in hosts
        if any(k in h for k in ("sentry", "statsig", "datadoghq", "event_logging",
                                "amplitude", "mixpanel", "segment.io", "bugsnag"))
    ]
    if suspicious:
        out.append("\n仍然出现遥测类域名:")
        out += [f"  ! {h}" for h in suspicious]
    else:
        out.append("\n未见 sentry / statsig / datadog / event_logging 类域名。")
    print("\n".join(out), flush=True)


def main() -> int:
    global QUIET
    ap = argparse.ArgumentParser(description="记录程序真实外联域名的 CONNECT 代理")
    ap.add_argument("--port", type=int, default=8899)
    ap.add_argument("--bind", default="127.0.0.1")
    ap.add_argument("--upstream", help="上游代理 host:port，例如 127.0.0.1:7897")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()
    QUIET = args.quiet

    upstream = None
    if args.upstream:
        h, _, p = args.upstream.partition(":")
        upstream = (h, int(p or 7890))

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((args.bind, args.port))
    srv.listen(200)

    def stop(signum, frame):
        raise KeyboardInterrupt

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    print(f"监听 {args.bind}:{args.port}"
          + (f"  上游 {upstream[0]}:{upstream[1]}" if upstream else "  直连")
          + "\nCtrl-C 结束并汇总", flush=True)

    try:
        while True:
            client, _ = srv.accept()
            threading.Thread(target=handle, args=(client, upstream), daemon=True).start()
    except KeyboardInterrupt:
        print()
        summary()
    finally:
        srv.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
