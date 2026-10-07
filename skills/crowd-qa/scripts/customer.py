#!/usr/bin/env python3
"""Simulated customers: sends inbound events to a business inside the product, the way a third party
(a messaging provider, a payment gateway, a form service) would deliver them, signed the same way.

Channels are declared in the JSON file named by CUSTOMERS_CONFIG in config.env (templates/customers.example.json):

  {"channels": {"chat": {
      "url": "http://localhost:4100/webhook/{to}", "method": "POST",
      "headers": {"content-type": "application/json"},
      "signing": {"type": "hmac-sha256", "header": "X-Signature", "prefix": "sha256=", "secret": "{secret}"},
      "body": {"from": "{from}", "name": "{name}", "text": "{text}", "ts": "{ts_ms}"}}}}

Placeholders: {to} {secret} (from the business's registration), {from} {name} {text} (from the command line),
{ts} unix seconds, {ts_ms} milliseconds, {id} a unique event id, and any --var key=value.

  customer.py <run> register <business> --channel chat --to 4584072633 --secret abc [--var key=value]
  customer.py <run> list
  customer.py <run> send <business> --from 15550001111 --name "Ravi" --text "hi" [--ago 25h] [--var k=v]

Owners register what they connected (the CONNECT_CMD output); customer characters only send. --ago backdates
the event's timestamps, for testing windows and late deliveries.
"""
import argparse
import hashlib
import hmac
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
import uuid


def env(run, key):
    for line in open(os.path.join(run, "config.env")):
        m = re.match(rf'^{key}="?([^"#]*)"?', line.strip())
        if m:
            return m.group(1).strip()
    return ""


def fill(value, vals):
    if isinstance(value, str):
        out = value
        for k, v in vals.items():
            out = out.replace("{" + k + "}", str(v))
        return out
    if isinstance(value, dict):
        return {k: fill(v, vals) for k, v in value.items()}
    if isinstance(value, list):
        return [fill(v, vals) for v in value]
    return value


def ago_s(v):
    if not v:
        return 0
    m = re.fullmatch(r"(\d+)([smhd])", v)
    if not m:
        sys.exit("customer: --ago takes 90s / 30m / 25h / 2d")
    return int(m[1]) * {"s": 1, "m": 60, "h": 3600, "d": 86400}[m[2]]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("run")
    ap.add_argument("cmd", choices=["register", "list", "send"])
    ap.add_argument("business", nargs="?")
    ap.add_argument("--channel")
    ap.add_argument("--to")
    ap.add_argument("--secret", default="")
    ap.add_argument("--from", dest="sender")
    ap.add_argument("--name", default="")
    ap.add_argument("--text", default="hello")
    ap.add_argument("--ago", default="")
    ap.add_argument("--var", action="append", default=[])
    a = ap.parse_args()

    reg_path = os.path.join(a.run, "world", "businesses.json")
    reg = json.load(open(reg_path)) if os.path.exists(reg_path) else {}
    extra = dict(v.split("=", 1) for v in a.var if "=" in v)

    if a.cmd == "list":
        for name, b in reg.items():
            print(f"{name}\tchannel={b['channel']}\tto={b['to']}")
        return
    if not a.business:
        sys.exit("customer: name the business")

    if a.cmd == "register":
        if not (a.channel and a.to):
            sys.exit("customer: register needs --channel and --to")
        reg[a.business] = {"channel": a.channel, "to": a.to, "secret": a.secret, "vars": extra}
        os.makedirs(os.path.dirname(reg_path), exist_ok=True)
        tmp = reg_path + ".tmp"
        json.dump(reg, open(tmp, "w"), indent=1)
        os.replace(tmp, reg_path)
        print(f"registered {a.business} on {a.channel} ({a.to})")
        return

    # send
    b = reg.get(a.business) or next((v for k, v in reg.items() if k.lower() == a.business.lower()), None)
    if not b:
        sys.exit(f"customer: no business {a.business!r}; run `customer.py {a.run} list`")
    cfg_path = env(a.run, "CUSTOMERS_CONFIG")
    if not cfg_path or not os.path.exists(cfg_path):
        sys.exit("customer: CUSTOMERS_CONFIG in config.env must point to the channels file")
    ch = json.load(open(cfg_path))["channels"].get(b["channel"])
    if not ch:
        sys.exit(f"customer: channel {b['channel']!r} is not in {cfg_path}")
    if not a.sender:
        sys.exit("customer: --from is required")
    now = time.time() - ago_s(a.ago)
    vals = {**b.get("vars", {}), **extra, "to": b["to"], "secret": b.get("secret", ""), "from": a.sender,
            "name": a.name or a.sender, "text": a.text, "ts": int(now), "ts_ms": int(now * 1000),
            "id": uuid.uuid4().hex}
    body = json.dumps(fill(ch.get("body", {}), vals), separators=(",", ":")).encode()
    headers = fill(ch.get("headers", {"content-type": "application/json"}), vals)
    sign = ch.get("signing")
    if sign and sign.get("type") == "hmac-sha256":
        key = fill(sign["secret"], vals).encode()
        headers[sign["header"]] = sign.get("prefix", "") + hmac.new(key, body, hashlib.sha256).hexdigest()
    req = urllib.request.Request(fill(ch["url"], vals), data=body, method=ch.get("method", "POST"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            print(f"{req.get_method()} {req.full_url} -> {r.status} {r.read().decode()[:300]}")
    except urllib.error.HTTPError as e:
        print(f"{req.get_method()} {req.full_url} -> {e.code} {e.read().decode()[:300]}")
        sys.exit(1)
    except Exception as e:
        print(f"{req.get_method()} {req.full_url} -> failed: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
