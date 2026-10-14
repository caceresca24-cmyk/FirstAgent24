# Current update: captured fare panels supported

The uploaded Windows captures now document outbound cards, return cards and fare-package panels. The scanner can inspect visible nonstop combinations, verify search-date labels and matching round-trip totals, parse carry-on/checked allowances per leg, and save confirmed exclusions. A 23 kg departure/25 kg return allowance is included checked baggage and fails your rule. Paid upgrades are skipped. Missing flight numbers remain explicitly unknown. The original direct-complete-card parser is retained for compatible pages.

43 tests pass, including 17 Chromium tests. These replay captured layouts and synthetic interactions; a complete automatic Windows run remains unverified. No recurring browser schedule is enabled. Do not increase batches until `python browser_agent.py scan --headed --max-pairs 1` passes on the actual page. The dashboard must run on the same computer/database as the agent.

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

## If the live scan reports unreadable cards

The error means the rendered page does not match the currently supported complete-return-card layout. Do not remove itinerary validation or combine arbitrary displayed leg prices. To inspect the live layout without exposing your login session:

```sh
python browser_agent.py diagnose
```

Wait for results in the opened browser, leave that screen visible and press Enter in the terminal. The command writes `data/flight-card-diagnostics.json`, containing up to 20 result cards' visible text, element tag names and button labels. It does not collect cookies, passwords, browser storage, full-page HTML or the account header, and does not import any prices. Review the file before sharing and remove any personal information accidentally present in a card. A screenshot of the flight-results screen can additionally show whether return flights require a second selection step.

## Separate outbound and return screens (observed Windows layout)

The uploaded live diagnostic shows eight single-leg outbound choices with prices labelled Round-trip. Two are nonstop; one explicitly includes checked baggage. Generic Included labels do not identify a baggage type. The runner now recognises this layout, skips connecting flights and explicitly checked-inclusive fares, and clicks only the scoped outbound Select control on a candidate for further inspection. Unknown baggage remains unknown.

The return layout and final fare selection have not yet been captured. The runner saves the return-card diagnostic and stops before importing prices; it cannot yet claim a confirmed live return itinerary. Run `python browser_agent.py diagnose-return` to open results, select the first suitable outbound candidate for inspection, and capture the return choices. Press Enter once the initial results are visible and again when the return results are visible. Review `data/flight-card-diagnostics.json` before sharing. This does not book a ticket.

## Return choices now observed; fare/baggage inspection next

The second uploaded diagnostic confirms separate return cards with three nonstop options. Their displayed round-trip prices are not imported as verified fares: checked baggage remains unknown, and generic Included labels do not identify carry-on. The observed-layout regression tests confirm selection of the cheapest visible nonstop return for inspection, skipping explicitly checked-inclusive fares and connecting flights.

Run `python browser_agent.py diagnose-fare`. Press Enter after initial outbound results load, again after return results load, and once more after the chosen return opens fare details. Before the third Enter, open a visible baggage-information control if available; do not enter passenger/payment information or click Book. The command collects scoped visible fare/dialog panels plus selected flight-card text, never form values/cookies/full-page HTML. It refuses to capture a recognised booking/passenger/payment URL. Review the diagnostic before sharing. No fare is imported by this command. The current automatic scan still stops at return diagnostics until the fare and baggage layout is verified.
