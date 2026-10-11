#!/usr/bin/env python3
"""pdf.py <email> <password> <document id> [out dir]: fetch a document the product lets a user download (a ticket,
an invoice, a report) through the same API call as its Download button, and render page 1 to a PNG the tester can
open. With remote browsers (BROWSER_SLOTS remote:N) a download lands on the other machine, where the tester cannot
read it; this gets the same file here. Prints the PDF path and the PNG path.

Copy to <run>/stack/pdf.py, replace the endpoints below with the product's, name it in context.md, and run it once
in the stack check. Rendering uses qlmanage (macOS) or pdftoppm (poppler) when present.
"""
import json
import os
import shutil
import subprocess
import sys
import urllib.request

API = "http://localhost:8080/api"


def req(method, path, token=None, body=None, raw=False):
    h = {"Accept": "application/json", "Content-Type": "application/json"}
    if token:
        h["Authorization"] = f"Bearer {token}"
    r = urllib.request.Request(API + path, data=json.dumps(body).encode() if body else None, method=method, headers=h)
    with urllib.request.urlopen(r, timeout=60) as resp:
        data = resp.read()
        return data if raw else json.loads(data or b"{}")


def render(pdf, out):
    if shutil.which("qlmanage"):
        subprocess.run(["qlmanage", "-t", "-s", "1400", "-o", out, pdf], capture_output=True)
        return pdf + ".png"
    if shutil.which("pdftoppm"):
        base = os.path.splitext(pdf)[0]
        subprocess.run(["pdftoppm", "-png", "-r", "110", "-f", "1", "-l", "1", "-singlefile", pdf, base], capture_output=True)
        return base + ".png"
    return ""


def main(a):
    if len(a) < 3:
        sys.exit(__doc__)
    email, pw, doc = a[:3]
    out = a[3] if len(a) > 3 else os.path.join(os.environ.get("OUT", "/tmp"), "evidence")
    os.makedirs(out, exist_ok=True)
    d = req("POST", "/auth/login", body={"email": email, "password": pw})
    tok = (d.get("data") or d).get("token")
    pdf = os.path.join(out, f"document-{doc}.pdf")
    with open(pdf, "wb") as f:
        f.write(req("GET", f"/documents/{doc}/download", tok, raw=True))   # the Download button's own call
    png = render(pdf, out)
    print(pdf)
    print(png if png and os.path.exists(png) else "(no PNG render: read the PDF with your tools)")


main(sys.argv[1:])
