# Multilingual Generative Voice Advisory & Mandi Price Predictor

## Problem Statement

Millions of Indian farmers make one of their most financially important decisions — when and where to sell their crop — with incomplete information. Mandi (market) prices fluctuate daily based on arrivals, seasonality, and local demand, but most farmers only find out the price after they've already reached the market, often relying on word-of-mouth or middlemen who may not act in the farmer's interest.

Existing digital solutions largely assume a smartphone, a stable data connection, and comfort reading English or navigating an app — conditions that exclude a large share of the farmers who would benefit most. Even where price data is available, it's rarely turned into an actual recommendation: raw numbers on a screen don't tell a farmer whether to sell today or wait three days for a better rate.

**We are building a voice-first, multilingual advisory system that:**

1. Predicts near-term mandi prices for specific crops and markets using historical trend data, not just today's number.
2. Uses a generative AI model to turn that prediction into a short, natural-language recommendation — spoken in the farmer's own language.
3. Works without assuming a smartphone or reliable internet, via a low-bandwidth web app, SMS, and (as a stretch goal) a plain phone call.

## How We're Building It

| Layer | Approach |
|---|---|
| Price data | Historical daily mandi prices (e.g. data.gov.in's Agmarknet dataset) for a focused set of crops and markets |
| Prediction | Time-series forecasting (trend + seasonality), validated against a naive baseline |
| Generative advisory | An LLM turns the prediction and the farmer's question into a short, spoken-style answer, generated directly in the target language |
| Voice interface | Speech-to-text captures the spoken question; text-to-speech reads the answer back |
| Multilingual support | At least one Indian language end-to-end beyond English; more if time allows |
| Offline / low-bandwidth access | Cached last-synced data for the web app, plus an SMS fallback that needs no app or data plan |

## Task Breakdown

Each task is scoped to be finished in a single sitting. Use this as a build checklist, and hand individual tasks to an AI coding assistant one at a time as a work order — each is self-contained enough to prompt on its own.

### 1. Data & backend foundation
- [ ] Find and download historical mandi price data for 3–5 crops across 2–3 mandis in your state
- [ ] Clean the data into a simple table: date, crop, mandi, price
- [ ] Set up a Flask (or FastAPI) project with one placeholder endpoint
- [ ] Get it running locally with a working "hello world" response

### 2. Prediction model
- [ ] Load the cleaned price data into a notebook and plot it (check for gaps or outliers)
- [ ] Build a naive baseline ("tomorrow's price = today's price") and measure its error
- [ ] Build a forecasting model (Prophet, or a simple regression) trained on the historical data
- [ ] Compare the model's error against the naive baseline — write down the improvement number
- [ ] Wrap the model in an endpoint: `/predict?crop=X&mandi=Y` returns a short forecast

### 3. Generative advisory layer
- [ ] Draft a prompt that takes a price forecast plus a farmer's question and returns a 2–3 sentence recommendation
- [ ] Test the prompt in English first, then explicitly in your target language
- [ ] Wire the prompt to an LLM API call from your backend
- [ ] Add basic guardrails: what happens if the crop or mandi isn't recognized?

### 4. Voice interface
- [ ] Add a microphone button to the web app using the browser's speech-to-text API
- [ ] Test which languages your target browser/OS actually transcribes well — note the gaps
- [ ] Extract the crop name and mandi name from the transcribed text (simple keyword matching is enough)
- [ ] Add text-to-speech so the generated advisory is read aloud
- [ ] Add a typed-text fallback for when voice input isn't available

### 5. Offline / low-bandwidth layer
- [ ] Add local/service-worker caching so the last-fetched prices are viewable with no connection
- [ ] Set up SMS (e.g. a Twilio trial) so a farmer can text a crop name and get a price plus one-line advisory back
- [ ] (Stretch) Set up a phone-call flow so a farmer can dial in, speak, and hear the advisory — attempt only once the core path is solid

### 6. Integration & testing
- [ ] Connect every piece into one working path: spoken question → transcription → prediction → generated advisory → spoken answer
- [ ] Test the full flow with at least 5 different real questions
- [ ] Add error handling so an unrecognized crop or mandi fails gracefully instead of crashing
- [ ] Expand to a second language once the first is solid

### 7. Presentation & demo prep
- [ ] Write a 4–6 slide pitch: problem, solution, architecture, live demo, impact, next steps
- [ ] Record a backup video of the demo in case live audio or wifi fails
- [ ] Rehearse the exact demo script out loud as a team
- [ ] Prepare answers for likely judge questions (accuracy numbers, scalability, data source)
