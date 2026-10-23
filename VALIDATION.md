# Q4R validation

## 0.4.0 update prompt

Updater regression tests cover Y/N and lowercase/empty/invalid responses, commit-pinned metadata parsing without execution, numeric version ordering and downgrade avoidance, bounded metadata reads/timeouts, noninteractive and offline suppression, disabling checks, saved-report commands, network errors, cancellation, installation failure, isolated installed-version verification and restart arguments. Installation-command tests distinguish system Python, source execution, installed virtualenvs and the ordinary pipx home. These tests mock network and installer processes; they do not claim a real remote upgrade. The packaging check separately installs and runs the complete package in an isolated environment.

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
