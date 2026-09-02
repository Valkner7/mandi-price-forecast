"""
Standalone Gemini diagnostic — no FastAPI, no thread executor, no 10s cutoff.

Run this directly to see what ACTUALLY happens on a raw call: does it hang
forever, error out after 30-60s (classic SDK retry-on-503 behavior), fail
fast with an auth error, or something else? This tells us whether the real
problem is Google's servers, this machine's network path, or the model name.

Usage (same venv/terminal setup as before):
    $env:GEMINI_API_KEY="your_key_here"
    python diagnose_gemini.py
"""

import os
import sys
import time
import socket
import urllib.request

print("=" * 70)
print("STEP 1 — Is the API key actually set?")
print("=" * 70)
key = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
if not key:
    print("NO KEY FOUND in this terminal's environment.")
    print("-> Run: $env:GEMINI_API_KEY=\"your_key_here\"  (this exact terminal, every session)")
    sys.exit(1)
print(f"Key found. Length={len(key)}, starts with '{key[:6]}...' (not printing the rest).")

print()
print("=" * 70)
print("STEP 2 — Can this machine even reach Google's API servers?")
print("=" * 70)
host = "generativelanguage.googleapis.com"
try:
    t0 = time.perf_counter()
    ip = socket.gethostbyname(host)
    print(f"DNS OK: {host} -> {ip} ({(time.perf_counter()-t0)*1000:.0f}ms)")
except Exception as e:
    print(f"DNS FAILED: {e}")
    print("-> This points to a network/DNS/VPN/firewall problem on this machine, not Gemini itself.")
    sys.exit(1)

try:
    t0 = time.perf_counter()
    urllib.request.urlopen(f"https://{host}", timeout=8)
except urllib.error.HTTPError as e:
    # Any HTTP response (even 404) proves the network path is open.
    print(f"Reachable: got HTTP {e.code} back in {(time.perf_counter()-t0)*1000:.0f}ms (this is expected/fine).")
except Exception as e:
    print(f"CONNECTION FAILED: {e}")
    print("-> This machine cannot reach Google's API over HTTPS at all.")
    print("-> Likely a corporate/campus firewall, antivirus HTTPS inspection, or VPN blocking it.")
    print("-> Try: a different network (phone hotspot) to confirm.")
    sys.exit(1)

print()
print("=" * 70)
print("STEP 3 — Raw Gemini call, NO timeout wrapper, generous 90s limit")
print("=" * 70)
model_name = os.getenv("GEMINI_MODEL", "gemini-3.7-flash")
print(f"Using model: {model_name}")
print("Sending a trivial one-word prompt and timing it for real...")

from google import genai

client = genai.Client(api_key=key)
t0 = time.perf_counter()
try:
    response = client.models.generate_content(
        model=model_name,
        contents="Reply with exactly one word: OK",
    )
    elapsed = time.perf_counter() - t0
    print(f"\nSUCCESS in {elapsed:.1f}s")
    print(f"Response: {response.text!r}")
    if elapsed > 10:
        print(f"\n-> This took {elapsed:.1f}s, longer than the app's 10s ADVISORY_TIMEOUT_SECONDS.")
        print("   That's why every scenario hit the fallback — not a bug, just a timeout set")
        print("   shorter than Gemini's real (slow) response time right now.")
        print("   Fix: either raise ADVISORY_TIMEOUT_SECONDS, or this is genuinely how slow")
        print("   the model is today (worth re-testing at a different time of day).")
    else:
        print(f"\n-> This was fast ({elapsed:.1f}s) and worked fine just now.")
        print("   That means the earlier timeouts were likely a TRANSIENT issue at that moment")
        print("   (Gemini-side high demand), not a persistent problem. Re-run test_scenarios.py now.")
except Exception as e:
    elapsed = time.perf_counter() - t0
    print(f"\nFAILED after {elapsed:.1f}s")
    print(f"Exception type: {type(e).__name__}")
    print(f"Exception detail: {e}")
    print()
    if "404" in str(e) or "not found" in str(e).lower():
        print("-> Looks like an invalid/unsupported MODEL NAME, not a network or key issue.")
        print(f"   Check the current valid model name in Google AI Studio and update GEMINI_MODEL")
        print(f"   (currently set to: {model_name})")
    elif "401" in str(e) or "403" in str(e) or "permission" in str(e).lower() or "api key" in str(e).lower():
        print("-> Looks like an API KEY problem (invalid, restricted, or wrong project).")
        print("   Generate a fresh key at https://aistudio.google.com/apikey using a personal")
        print("   (non-Workspace/institutional) Google account.")
    elif "503" in str(e) or "overloaded" in str(e).lower() or "unavailable" in str(e).lower():
        print("-> This IS the 503/high-demand issue — confirms Gemini's servers are genuinely")
        print("   overloaded right now, not a problem on your end. Your fallback advisory is")
        print("   doing exactly what it should. Worth re-testing at a different time of day too.")
    else:
        print("-> Unrecognized error type — paste this whole output back for a specific fix.")

print()
print("=" * 70)
print("Done.")
print("=" * 70)
