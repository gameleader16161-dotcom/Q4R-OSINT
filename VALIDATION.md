# Q4R validation

## 0.8.0 checks

145 unit/engine regressions pass locally, including exact session-key delivery and restoration, real service AI planning/review using intercepted synthetic responses, complete-identifier preservation, invented citation rejection, consent/endpoint validation, unchanged finding scores and balanced platform query budgets. Real Chromium checks cover the AI settings/consent and draft display, source details, Arabic/English, mobile layout, saved-case reopening and cancellation.

The previous EXE's scan-start error was reproduced through its native bridge even with offline mode. A diagnostic build traced it to a missing runtime trust-policy PEM used by its distribution's SSLContext. Preserving that policy fixes offline startup without weakening HTTPS. The corrected packaged application also made a real HTTPS GitHub `octocat` lookup and retrieved one profile. This isolated public fixture is not evidence of recall or accuracy for people or other platforms. New Windows CI exercises an actual packaged offline search. Android and final binary job results are recorded in the Native applications workflow for the delivered commit.

No live paid AI call or face identification was performed. AI provider behavior is tested using synthetic intercepted responses. Model compatibility, remote allowance, platform changes, regional access and rate limits remain external constraints.


## 0.7.0 application validation

- 137 automated engine/unit regressions pass locally. The new coverage includes metadata-free reference images, case export boundaries, cancellation persistence, actual run status on reopen, score sorting and session-only API-key restoration.
- Real Chromium UI checks pass in Arabic RTL and English LTR at desktop and phone widths: form validation, local reference image, ranked 100/56/0 synthetic findings, filtering, export and saved-case reopen. Cancelled cases stay stopped with actual attempted progress in both languages. Synthetic fixtures are not search-accuracy measurements.
- Windows 11: the source Qt shell and packaged portable executable both booted the real interface and native bridge, returned exit code 0 and captured a nonblank interface. Bundled UI assets are compared byte-for-byte with source.
- Android: the debug APK and instrumentation APK compile with Gradle 8.13, AGP 8.13.2, Chaquopy 17.1, Python 3.12 and SDK 36. Android emulator runtime results are recorded by the Native applications CI workflow. A compile is not a runtime pass; check the workflow result for the delivered commit.
- Tests do not establish measured recall, precision, ownership, face similarity or broad platform availability. No face matching or password-reset discovery is implemented. Live source access remains dependent on platform changes and network conditions.


## 0.6.0 multilingual full-name candidates

The suite contains 125 unit tests. Fifteen added regressions exercise Arabic/Latin spelling provenance and bounds, unknown-name fallback, Portuguese country/accent context without renaming people, manual spellings and disabling expansion, accepting longer names while rejecting single-token/sub-token/discontiguous matches, separate evidence scores, never inferring email links from names, actual scripted Bluesky/Mastodon requests in both scripts, repeated-profile deduplication, exact-handle preservation, filtered versus absent states, no name truncation, distinct-query browser scheduling, bounded GitHub spelling queries without invented returned name fields, and offline saved metadata/display. Fixtures use synthetic profiles and do not investigate the screenshot's subject. These are implementation tests, not measured identity accuracy or proof that every spelling/site works live. The separate real-Firefox CI smoke still uses intercepted offline engine fixtures.

## 0.5.0 context and email visibility

The suite contains 110 unit tests. Eleven new tests cover country and alternate-handle validation, alias-only input, full-name preservation, absence of standalone country/father-name queries, quoted country terms and unscoped fallback, full email queries, no country identity-score bonus, parent-preserving lookup views, parallel per-handle isolation and counts, continuing after one handle fails, Arabic-comma prompts, and visible email rows in terminal/HTML/text even with zero results. Unchecked platforms are explicitly inconclusive. All new collection fixtures are synthetic and local; no private individuals or relatives' accounts were investigated. Country is a supplied query keyword, not verified geolocation. Family linkage and password-reset enumeration are not implemented.

## 0.4.1 update prompt

Updater regression tests cover Y/N and lowercase/empty/invalid responses, commit-pinned metadata parsing without execution, numeric version ordering and downgrade avoidance, bounded metadata reads/timeouts, noninteractive and offline suppression, disabling checks, saved-report commands, network errors, cancellation, installation failure, isolated installed-version verification and restart arguments. Installation-command tests distinguish system Python, source execution, installed virtualenvs and the ordinary pipx home. These tests mock network and installer processes; they do not claim a real remote upgrade. The packaging check separately installs and runs the complete package in an isolated environment.

The full suite now contains 99 unit tests. The packaging check additionally starts the actual standalone worker from outside its target venv and verifies a real isolated restart, using a fixture installer command. A separate live Windows smoke used a disposable pipx home with an injected `packaging` dependency: the notification condition was simulated with an older in-memory version, while fetching official metadata, downloading/installing the pinned 0.4.0 archive, verifying the installed version and restarting were real. The injected dependency survived. This exposed the initial running-interpreter file lock; 0.4.1 hands off to base Python before installing and uses a base64 argument payload for Windows quoting. This smoke does not claim an upgrade performed on the user's Kali machine.

## 0.3.1 public email summary

Six additional regression tests cover the optional first-line email summary, explicit public email fields and response evidence, separation from username/search-snippet results, missing/failed/mismatched evidence, candidate exclusion, exact email matching without alias collapsing, one-line text escaping and old-case export with manifest verification. These use synthetic local fixtures, not password-reset requests or a person's accounts. Existing connectors are unchanged: only the GitHub connector currently supplies an explicit public email attribute.

## 0.3.0 additions

The 0.3.0 tests cover terminal result previews, old-case account export, exact final account-file path, absent versus blocked markers, search-candidate labeling, post-URL rejection, known search redirect decoding, search budgets, challenged-engine stopping, browser evidence typing and offline browser suppression. Account export preserves existing evidence hashes, including when a file has been tampered with.

Google, Bing and DuckDuckGo live result pages for the neutral query `Python Playwright documentation` were inspected in the in-app browser on 2026-10-23. Their visible result-link structures were confirmed. This used a different browser session/network from the user's Kali installation and is **not** an end-to-end claim that Playwright Firefox can access those engines from Kali. The separate real-Firefox CI smoke uses entirely intercepted offline fixtures to test the actual extraction driver without investigating a person or relying on live engine access.

## 0.2.0 direct-source smoke

Observed on 2026-10-23. Live smoke runs were made from the development machine with Q4R's actual bounded HTTP client, not a browser login session. They are observations of that network and time, not promises for every account, region or future page layout. CI tests use fixtures and do not call social sites.

| Connector | Public smoke input | Observed result |
|---|---|---|
| Bluesky | `bsky.app` | Public profile DID/handle returned; actor search also returned profile candidates |
| Mastodon | `Mastodon` on mastodon.social | Public account ID and acct returned without a token |
| DEV | `thepracticaldev` | Public user API returned ID, username and summary |
| Telegram | `telegram` | Public channel title and account detail block found |
| Instagram | `instagram` | Canonical URL, profile metadata and exact handle found |
| YouTube | `YouTube` | Channel metadata ID and matching owner handle URL found |
| Facebook | `facebook` | Matching vanity username, public profile ID and canonical URL found |
| X | `X` | Matching canonical profile metadata and username found |
| Pinterest | `pinterest` | Matching canonical profile URL and profile metadata found |
| Hacker News | `pg` (official API documentation example) | Public user object returned |
| Reddit | `reddit` | HTTP 403: recorded as blocked; no finding created |
| TikTok | `tiktok` | Transport failure: recorded as error; no finding created |

Ten of twelve direct connectors retrieved public evidence in these smoke runs. The other two are implemented but were unavailable here; their presence in the source list does not imply live availability. A made-up Telegram handle returned HTTP 200 with a generic Contact page; Q4R classified it **unconfirmed** and created no finding.

The tests also cover HTTP-200 login/challenge/generic pages, mismatched canonical URLs and handles, unsupported usernames, API schema failures, public-profile positives, null HN responses, parallel file/ID integrity, per-source counts, offline execution and build/install with existing case folders and a nested clone. Graph tests verify that explicit public links retain their evidence attribution without asserting identity equivalence.

Run local checks:

```bash
python3 -m unittest discover -s tests -v
python3 tests/check_packaging.py
```

To try a single public service-owned profile yourself:

```bash
q4r scan --username instagram --sources instagram
q4r scan --username telegram --sources telegram
q4r scan --username bsky.app --sources bluesky
```

These are separate service profiles; they are not one person's identity. Review source coverage and checks even when some findings were retrieved. Broader name/email mentions depend on the public sources' indexes or optional Brave; Q4R does not enumerate private accounts, probe logins or bypass access controls.
