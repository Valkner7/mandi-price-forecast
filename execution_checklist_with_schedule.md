# Execution Checklist — Multilingual Generative Voice Advisory & Mandi Price Predictor

Refined 24-step checklist with a 2-person schedule and technical watch-outs folded in. Supersedes the earlier task breakdown.

## Watch-outs before you start

- **Step 11 (Web Speech API) is not actually offline.** Chrome sends the audio to Google's servers to transcribe it. Keep your "works without internet" claim tied to the SMS and cached-price paths (Steps 15–16), not the voice interface itself — a judge who knows this will ask.
- **Pick one TTS method and commit.** Mixing browser `speechSynthesis` and `gTTS` (Step 13) adds a failure point with no real upside. Browser-native is more demo-safe since it needs no extra API call once the page has loaded.
- **Validate the forecast on held-out data.** For Steps 5–6, measure both the baseline's and your model's error on the most recent N days that neither saw during training — not on the training data itself. Otherwise the "beats baseline" number won't survive a follow-up question.
- **Test SMS to a real Indian number on Day 1, not Day 4.** Twilio (and to a lesser extent Exotel) can run into DLT/regulatory friction sending to Indian mobile numbers. Confirm it actually works before you build a demo moment around it.

## Suggested 5-day schedule (2 people)

| Day | Person A | Person B |
|---|---|---|
| 1 | Steps 1–2: pull & clean price data | Step 3: FastAPI skeleton + health check, then start Step 11 UI scaffold (no logic yet) |
| 2 | Steps 4–7: EDA, baseline, train model, `/predict` route | Steps 8–9: draft & test the LLM prompt on sample forecast data |
| 3 *(pair up — highest integration risk)* | Step 10: wire LLM into FastAPI + Steps 12–14 | Step 18: connect the full path end-to-end |
| 4 | Step 15: offline cache, Step 21: second language | Step 16: SMS webhook, Step 17 stretch (IVR) if time allows |
| 4 (together, end of day) | Steps 19–20: run test scenarios, add exception handling | |
| 5 (together) | Steps 22–24: pitch deck, backup video, rehearsal | |

## Full checklist

### Phase 1: Data & Backend Foundation
- [ ] Step 1: Download historical daily price data for 3–5 crops across 2–3 local mandis from data.gov.in (Agmarknet)
- [ ] Step 2: Clean raw data into `date`, `crop`, `mandi`, `price`; forward-fill missing dates
- [ ] Step 3: Initialize a FastAPI repo with a `/health` endpoint, verify it runs locally

### Phase 2: Forecasting Engine
- [ ] Step 4: Load the clean dataset in a notebook, plot time-series curves, spot gaps/outliers
- [ ] Step 5: Write a naive baseline (P(t+1) = P(t)), compute MAE/RMSE **on a held-out test window**
- [ ] Step 6: Train Prophet or Exponential Smoothing, confirm it beats the baseline **on the same held-out window**
- [ ] Step 7: Create `/predict?crop=X&mandi=Y` returning latest price, trend, and short-term forecast

### Phase 3: Generative Advisory Layer
- [ ] Step 8: Draft a system prompt: price data + user question → 2–3 sentence recommendation
- [ ] Step 9: Benchmark in English, then explicitly test native-script output (Hindi, Punjabi, etc.)
- [ ] Step 10: Wire the prompt to an LLM API call inside FastAPI, add fallback for unknown crop/mandi

### Phase 4: Multilingual Voice Layer
- [ ] Step 11: Build the frontend mic button with `webkitSpeechRecognition` — needs internet, not offline
- [ ] Step 12: Keyword-match the transcribed text to extract crop and mandi
- [ ] Step 13: Add TTS (pick one: browser `speechSynthesis` or `gTTS`, not both) to read advice aloud
- [ ] Step 14: Add a text input fallback for noisy environments or unsupported browsers

### Phase 5: Offline & Low-Bandwidth Layer
- [ ] Step 15: Configure a service worker to cache last-fetched mandi records for offline viewing
- [ ] Step 16: Build an SMS webhook (Twilio/Exotel) for text-in, text-out price queries — test against a real Indian number early
- [ ] Step 17 (Stretch): Set up a basic IVR phone flow for voice-in, voice-out calls

### Phase 6: Integration & Refinement
- [ ] Step 18: Link the full path: mic → STT → `/predict` + LLM → TTS playback
- [ ] Step 19: Run 5 distinct farmer scenario queries end-to-end, log latency
- [ ] Step 20: Add global exception handling for bad audio or missing data
- [ ] Step 21: Add prompt/dictionary support for a second regional language

### Phase 7: Demo & Presentation Prep
- [ ] Step 22: Draft a 5-slide pitch deck (Problem, Architecture, Live Demo, Metrics, Expansion)
- [ ] Step 23: Record a fallback demo video (screen + audio) in case of live network/mic issues
- [ ] Step 24: Rehearse the live script; compile accuracy figures, data lineage, and latency metrics for Q&A
