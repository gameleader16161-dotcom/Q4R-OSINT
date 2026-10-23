# Q4R 0.2.0 validation

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
