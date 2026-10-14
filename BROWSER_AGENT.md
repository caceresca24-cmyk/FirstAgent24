# Browser agent: implementation and current limits

The runner opens ordinary Trip.com flight-search pages with Playwright Chromium. It enumerates every departure date through 15 December 2026 and each return 7–10 days later (latest return 25 December). Each search is one adult, economy. It stores a complete round-trip total, verifies both reciprocal flight legs, and applies nonstop, carry-on and no-checked-baggage rules to both legs. Unknown baggage is stored separately for verification, never treated as excluded baggage.

**Live Trip.com extraction is not verified.** Chromium launched during development, but navigation failed with `ERR_TUNNEL_CONNECTION_FAILED`; a follow-up proxy check returned HTTP 403 Forbidden for Trip.com. Trip.com's current result selectors, date rendering and outbound/return selection flow could not be inspected. This is a tested browser runner with a conservative DOM parser, not a proven live feed. The parser requires complete rendered return-itinerary cards with two routes, four explicit ISO timestamps, two flight numbers and an explicit return total. If the site renders outbound choices first or different text, it deliberately stops: we must adapt the parser/navigation against the actual page before claiming it works. No hidden API calls, price guesses or CAPTCHA bypasses are used.

## Install

In the repository folder:

```sh
python -m pip install -r requirements-browser.txt
python -m playwright install chromium
```

On a Linux machine missing browser libraries, the supported Playwright installer may additionally require `python -m playwright install --with-deps chromium` with administrator access. Do not share your account credentials or browser profile.

## Login and first test

The runner uses its own persistent browser profile, ignored by Git. Signing in to an ordinary browser tab does not sign this profile in. On a computer with a graphical desktop:

```sh
python browser_agent.py login
```

Sign in yourself in the browser that opens, then press Enter in the terminal. Passwords, session cookies and browser state stay in `browser_data/` on your computer. Do not upload that folder. Do not run two agents against the same profile.

Then run ONE search first:

```sh
python browser_agent.py scan --headed --max-pairs 1
```

This opens the first date pair. It imports results only if it recognises complete priced itineraries. Login challenges, CAPTCHA, unreadable cards and incomplete pages stop the run and leave previous observations intact. No booking action exists. Use the profile only on a trusted computer.

**Codespaces does not normally provide a graphical desktop.** A headless scan can run there, but the interactive login command needs a supported desktop/remote-desktop setup. The normal Codespaces browser tab does not expose its Trip.com session to Chromium. We have not configured or verified a remote desktop. For the initial login and selector verification, run the repository on your own desktop computer; run `python app.py serve` there too so both processes share the same SQLite database. Moving browser sessions between machines is not part of this implementation.

## Coverage and resume

```sh
python browser_agent.py plan
python browser_agent.py scan --max-pairs 10 --resume
```

The default is ONE date pair, with at least 15 seconds between searches. Increase the batch only after the live first-search test succeeds. A checkpoint advances only after a validated import. Reusing `--resume` with changed dates/currency/selectors stops and asks you to start a new scan. Profiles, checkpoints and statuses are local. A batch is a manual run, not an enabled recurring schedule. The dashboard shows progress and blockage errors. A completed date pair only covers rendered cards; it never asserts every fare in Trip.com's inventory was retrieved. Infinite scrolling is bounded, and unseen fare variants or separate selection steps are not inferred.

A selectors JSON can supply `cards` and `text` CSS selectors after inspection of the actual page:

```json
{"cards":"[data-testid=flight-card]","text":"body"}
```

Use `--selectors /path/to/selectors.json`. This changes element targeting only; it does not prove the parser matches the live site. Both parser and any required selection navigation must be checked against visible pages. Until then, a stopped extraction is the correct outcome, not evidence of zero flights.

## Tests

```sh
python -m unittest discover -s tests -v
```

Real Chromium tests use explicitly synthetic fixtures and intercepted test navigations. They verify parsing, database ingestion, navigation/checkpoints, incomplete-result rejection and verification stops. They do not test real prices, the user's login or Trip.com's current DOM. Core tests run without the optional Playwright package; browser tests require the package plus Chromium.

No unattended browser schedule or external notifications have been activated. The existing provider scheduler remains separate. Connect the runner to recurring execution only after live extraction and rate handling are verified.
