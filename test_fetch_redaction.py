"""Checks that the data.gov.in API key never reaches output or logs.

Run:  python test_fetch_redaction.py
Exit code 0 = all checks passed, 1 = at least one failure.

Why: a failed price fetch used to print the full request URL, including
api-key=<the key>, and the job scripts copy that output into log files
(Issue 11 of the integration report).

What is checked, using a made-up key and no real network:
  1. redact() hides the literal key, URL-encoded spellings and any
     api-key= parameter, and never mangles text when the key is empty.
  2. Connection refused: the key is absent from the retry lines, from the
     FetchFailed message, and from a full formatted traceback (the exception
     chain used to carry the unredacted URL).
  3. HTTP 400 whose body echoes the request (a server reflecting the URL):
     the key is absent there too.
  4. The whole script (main()) on a dead API prints nothing with the key,
     exits 1 and leaves clean_mandi_prices.csv byte-for-byte unchanged.
  5. The messages still say what went wrong (host/port or HTTP status), so
     redaction did not make failures undiagnosable.
"""
import contextlib
import io
import sys
import threading
import traceback
import warnings
from http.server import BaseHTTPRequestHandler, HTTPServer

warnings.filterwarnings("ignore")

import fetch_daily_mandi_data as f

KEY = "SECRETKEY-abc123/+x"      # includes characters that get URL-encoded
failures = []


def check(label, ok, detail=""):
    print(f"  {'ok  ' if ok else 'FAIL'} {label}" + (f"  [{detail}]" if detail and not ok else ""))
    if not ok:
        failures.append(label)


def has_key(text):
    return "SECRETKEY-abc123" in text


f.time.sleep = lambda s: None            # no waiting between retries

print("[1] redact()")
check("literal key hidden", f.redact("x?k=abc&y=1", "abc") == "x?k=***&y=1")
check("URL-encoded key hidden", "abc%2Bdef" not in f.redact("api-key=abc%2Bdef&z=1", "abc+def"))
check("api-key= parameter hidden even for an unknown key", f.redact("u?api-key=zzz&a=1") == "u?api-key=***&a=1")
check("api_key= and upper case hidden", f.redact("API_KEY=zzz") == "API_KEY=***")
check("empty key does not mangle text", f.redact("hello", "") == "hello")
check("None key does not mangle text", f.redact("hello", None) == "hello")
check("text without a key is unchanged", f.redact("HTTP 400: bad filter", KEY) == "HTTP 400: bad filter")

print("[2] connection refused (closed local port)")
f.API_URL = "http://127.0.0.1:9/resource/x"
buf = io.StringIO()
err = None
with contextlib.redirect_stdout(buf):
    try:
        f.fetch_page(KEY, 0, "20/10/2026")
    except f.FetchFailed as e:
        err = e
tb = "".join(traceback.format_exception(type(err), err, err.__traceback__)) if err else ""
check("FetchFailed was raised", err is not None)
check("retry lines do not contain the key", not has_key(buf.getvalue()))
check("exception message does not contain the key", err is not None and not has_key(str(err)))
check("full traceback (with chain) does not contain the key", not has_key(tb))
check("message still names the host and port", err is not None and "127.0.0.1" in str(err) and "9" in str(err))
check("message still shows api-key=***", err is not None and "api-key=***" in str(err))


print("[3] HTTP 400 with a body that echoes the request")


class Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"bad request, you sent: {self.path}".encode()
        self.send_response(400)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


server = HTTPServer(("127.0.0.1", 0), Echo)
threading.Thread(target=server.serve_forever, daemon=True).start()
f.API_URL = f"http://127.0.0.1:{server.server_port}/resource/x"
buf = io.StringIO()
err = None
with contextlib.redirect_stdout(buf):
    try:
        f.fetch_page(KEY, 0, "20/10/2026")
    except f.FetchFailed as e:
        err = e
tb = "".join(traceback.format_exception(type(err), err, err.__traceback__)) if err else ""
check("key absent from retry lines", not has_key(buf.getvalue()))
check("key absent from message", err is not None and not has_key(str(err)))
check("key absent from traceback", not has_key(tb))
check("message still shows the HTTP status", err is not None and "HTTP 400" in str(err))

print("[4] whole script on a dead API")
f.API_URL = "http://127.0.0.1:9/resource/x"
before = f.CLEAN_PATH.read_bytes()
buf = io.StringIO()
code = None
import os
os.environ["DATA_GOV_API_KEY"] = KEY
old_argv, sys.argv = sys.argv, ["fetch_daily_mandi_data.py"]
with contextlib.redirect_stdout(buf):
    try:
        f.main()
    except SystemExit as e:
        code = e.code
sys.argv = old_argv
check("script exits 1", code == 1, str(code))
check("nothing printed contains the key", not has_key(buf.getvalue()))
check("prices file unchanged", f.CLEAN_PATH.read_bytes() == before)
check("failure line is still printed", "API CALL FAILED" in buf.getvalue())

server.shutdown()
print()
if failures:
    print(f"{len(failures)} check(s) FAILED: " + "; ".join(failures))
    sys.exit(1)
print("All API-key redaction checks passed.")
