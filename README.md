# Q4R

**Public-source research with evidence you can review.**

Q4R is a Python command-line tool for Kali Linux and other systems with Python 3.10+. Enter a full name, a username and optionally an email. Q4R retrieves public leads, records response evidence and produces a searchable local report and relationship graph.

Q4R **0.2.0** includes **18 built-in source connectors**, optional Brave indexed web search and optional Wayback lookup. Twelve of those connectors perform direct public social/community profile checks. **A Brave key is not required for those checks.** A separate registry of **48 domains** generates indexed web-search queries; those are not 48 direct account detectors. Q4R is independent of Maltego and SpiderFoot and makes no claim to search the entire internet or outperform every other tool.

## Install on Kali

```bash
sudo apt update
sudo apt install -y git python3 python3-venv pipx
git clone https://github.com/gameleader16161-dotcom/Q4R-OSINT.git
cd Q4R-OSINT
bash install.sh
export PATH="$HOME/.local/bin:$PATH"
q4r
```

`git clone` downloads all repository files directly from the URL. Installation is a separate, one-time step that registers the `q4r` command. You do not need to download individual files. `pipx` isolates Q4R from Kali's system Python. Run it as your ordinary user, without `sudo`. If already installed, update with `git pull` followed by `pipx install --force .`.

Alternatively, run directly from the clone without installing:

```bash
python3 -m q4r --help
python3 -m q4r scan --username YOUR_OWN_USERNAME --sources github
```

## Update an existing install

```bash
cd Q4R-OSINT
git pull --ff-only
pipx install --force .
q4r --version
```

Version 0.2.0 adds direct social profile retrieval, public account search on Bluesky and GitHub, parallel collection, per-profile checks and self-published link edges in GraphML. It retains the 0.1.2 packaging fix for checkouts containing `cases/` or extra folders. `git pull` alone updates the clone, not the copy installed by pipx; reinstall after pulling. `bash install.sh` can also be rerun to install the updated copy.

If an earlier install failed with `Multiple top-level packages discovered in a flat-layout: ['q4r', 'cases']`, update using the commands above. The build explicitly includes only the `q4r` package. You do not need to remove your reports or clone another copy. A failed installation leaves the previously installed command in place, which explains an old version still appearing after a successful `git pull`.

## Usage

```bash
q4r                              # Interactive Arabic/English prompts
q4r --help
q4r sources
q4r scan --name "Example Researcher" --username example_handle --out cases/review-001
q4r scan --name "محمد أحمد العلي" --username my_handle --email me@example.org
q4r scan --name "Example Researcher" --offline --out cases/plan-only
q4r scan --username YOUR_OWN_USERNAME --sources github,gitlab,keybase --wayback
q4r scan --username YOUR_OWN_USERNAME --sources social --workers 4
q4r scan --name "Example Researcher" --sources bluesky,github_search
q4r verify cases/review-001
```

At least one identifier is required. A name, when supplied, must have at least two whitespace-separated parts; names with three or more parts and Unicode/Arabic are supported. There is no artificial name-length limit in Q4R's input. Sources impose their own query budgets; oversized queries are marked skipped instead of silently shortening the name. Provider-specific username syntax is checked separately. Email is optional and is searched as a quoted public web mention. Q4R does not perform login, password reset, SMTP account probing or breach-database lookups.

## Troubleshooting: 0 leads / web search skipped

`0 leads` means the sources that actually ran returned no leads. It does not mean Q4R searched the whole web or proved that no account exists. An `ok` source status means the API request completed; each source now also prints its number of new leads.

- A handle containing `_` is valid input to Q4R, but not a valid GitHub handle. The GitHub connector skips it and keeps the exact spelling for other sources and web queries. Do not remove underscores to guess a different account.
- Archive and Open Library search item/book metadata. Many ordinary personal names have no catalog matches.
- `BRAVE_SEARCH_API_KEY` missing means Brave's general web index did not run. Direct social profile connectors and Bluesky/GitHub account search can still run without that key. Configure Brave for indexed web coverage, or use the manual web queries alongside the direct results.

Q4R 0.1.1 warns before collection when the key is missing. The report shows limited web coverage, empty reports expand the search plan, and every case includes `searches.txt`. To print links from an existing case, including one made with v0.1.0:

```bash
q4r queries cases/YOUR_CASE_DIRECTORY --limit 20
# Or read the complete plan:
cat cases/YOUR_CASE_DIRECTORY/searches.txt
```

The text file is new in 0.1.1; the `queries` command also reads old `report.json` files without needing that text file. Links are manual tasks, not results. You can open `report.html` with your browser using its printed file URI.

To require configured automatic web search before any collection starts:

```bash
q4r scan --username YOUR_OWN_USERNAME --require-web
```

This validates local configuration, not API-key validity, credits or service availability. A skipped source remains a skip; successful empty API responses are not errors. Exit code 0 can still mean limited coverage, so check the coverage section or use `--require-web` when web collection is essential.

## Automatic sources and honest coverage

### Direct social/community profiles (no Brave key)

| Connector | Retrieval and positive evidence | Important limits |
|---|---|---|
| `bluesky` | Public getProfile and searchActors; returns DID, handle, display name and description | Short handles are tried as `HANDLE.bsky.social` only when valid; search results are candidates |
| `mastodon` | Exact public lookup on mastodon.social; account ID and acct | Other instances are not directly queried; optional name search requires `Q4R_MASTODON_TOKEN` |
| `telegram` | Public t.me profile/channel page with title and account detail block | Generic “Contact @handle” pages are **unconfirmed**, not existence evidence |
| `instagram` | Public profile metadata or structured profile ID tied to the requested canonical URL | Login/rate limits can prevent retrieval; private content is not collected |
| `facebook` | Matching canonical URL, public profile ID and vanity username | Handles are not guessed from real names; login restrictions may block the page |
| `youtube` | Channel metadata with channel ID and a matching owner/vanity handle URL | Checks `@handle`, not arbitrary video URLs |
| `x` | Canonical profile URL and explicitly matching username/profile metadata | JavaScript/login-only responses remain unconfirmed or blocked |
| `pinterest` | Canonical profile URL and matching profile metadata/structured identity | A successful HTTP response by itself is not evidence |
| `tiktok` | Matching canonical URL and structured uniqueId plus profile ID | Network restrictions and layout changes may make retrieval unavailable |
| `reddit` | Public user-about JSON with matching username and public ID | Reddit may deny unauthenticated requests; a 403 is **blocked**, not missing |
| `devto` | Public user API: matching username, public ID, display name and summary | Also records explicitly published website/GitHub/X links without following them |
| `hackernews` | Official public HN user API: matching ID and public about text | Null is reported as not found in that API |

Use `--sources social` to run these twelve only. The default runs all built-in connectors and optional Brave. `--sources` also accepts a comma-separated subset. It does not remove punctuation from a supplied handle to invent another account. An exact-handle lookup can only find that spelling; it does not prove that accounts on different platforms belong to the same person.

HTML retrieval is conservative: `found` requires matching profile evidence, not just HTTP 200. `not_found` means an explicit endpoint absence response; `blocked` means denial, rate limit, login/challenge or an unfollowed redirect; `unconfirmed` means insufficient evidence; `error` means a failed request/schema; `skipped` means incompatible input. `partial` means a connector returned some evidence and also encountered blocked/failed checks. All these outcomes appear in the report's **Direct profile checks** table with evidence IDs. Unknown HTML remains a check, not a finding.

Website layouts, restrictions and regional responses change. See [validation notes](VALIDATION.md) for the observed 0.2.0 smoke results, including unavailable services; fixture tests are not proof of worldwide availability.

### Other built-in connectors and optional indexes

| Source | What Q4R actually does | Requirements / limitations |
|---|---|---|
| GitHub | Exact username public profile lookup | Optional `Q4R_GITHUB_TOKEN` for API limits; no private data |
| `github_search` | Public display-name account search, independent of exact username rules | Name required; search candidates need review; public search rate limits apply |
| GitLab | Exact username public API lookup | Service may require authentication or block requests |
| Keybase | Public username lookup | Source-specific username syntax |
| Internet Archive | Quoted name search in archive item metadata | Not a full-text search of every archived web page |
| Open Library | Quoted name search in bibliographic catalog | Book mentions are leads, not personal identity evidence |
| Brave Search | Quoted global and site-specific indexed web searches | Requires `BRAVE_SEARCH_API_KEY`; index coverage, not direct access to every social network |
| Wayback (opt-in) | Availability lookup for up to 10 retrieved URLs | Requires `--wayback`; returns closest available snapshot, not every snapshot |

Each source reports its own status and result count. A blocked, unavailable or malformed response is never an “account exists” result. The original GitHub exact-lookup connector still records a missing-profile HTTP 404 as an HTTP error. An empty successful search is not proof that no account exists. Providers read bounded result pages; exhaustive crawling and recursive expansion are not implemented.

Optional indexed web/social mentions use Brave's API:

```bash
# Keep secrets in your shell environment, never in the repository.
read -rsp 'Brave API key: ' BRAVE_SEARCH_API_KEY; echo
export BRAVE_SEARCH_API_KEY
q4r scan --name "Example Researcher" --username example_handle --web-budget 150 --limit 5
unset BRAVE_SEARCH_API_KEY
```

Without this key Q4R still runs selected direct connectors and exports manual Google search links. It does **not** claim those manual tasks were executed. `--web-budget` defaults to 12 queries and can be set to 1–500; these requests may consume your provider credits. The plan starts with global queries, then registry domains in order. `--limit` is 1–20 results per query/source; it is not a total scan limit. `--workers` runs 1–8 sources in parallel (default 4), with a shared per-host lock enforcing at least 1.1 seconds between requests. Evidence IDs, response writes and reports are synchronized. Wayback runs after collection because it needs retrieved URLs. A 429/502/503/504 gets at most one bounded retry, honoring numeric `Retry-After` up to five seconds. Redirects, CAPTCHA and login gates are not bypassed. Ctrl+C cancels queued requests and lets in-flight requests finish or time out before saving the final manifest.

Add domains by editing `q4r/sites.json`, or provide a local registry:

```json
[{"domain": "example.org", "category": "research"}]
```

```bash
q4r scan --name "Example Researcher" --sites my-sites.json
```

This changes search queries; it does not add a new direct API connector. To implement one, add a function in `q4r/providers.py`, register it in `PROVIDERS`, and add its official HTTPS API host to `q4r/http.py`. Use `client.get()` so requests remain paced and auditable. Do not add arbitrary private-network endpoints.

## Reports and evidence

Each run creates a new case directory; existing directories are never overwritten:

```text
report.html       Searchable local report, source status and SVG lead graph
report.json       Structured results, direct checks, source coverage and search plan
findings.csv      Spreadsheet-compatible lead export
searches.txt      Complete manual search plan as text (since 0.1.1)
graph.graphml     Generic graph interchange (all leads)
requests.jsonl    Endpoint, status, UTC time, elapsed time, response hash
evidence/*.bin    Received API response bodies, including HTTP errors
manifest.json     SHA-256 hashes of all generated case files
ai-summary.json   Optional, separate machine-generated draft
```

Responses are capped at 4 MiB; a truncated response is recorded as an error. Request credentials and authorization headers are never saved. Request URLs may contain the supplied name/email and reports contain personal data. Cases are private local files, excluded from Git by default; new directories use restrictive permissions on Unix. Windows access follows your user-folder ACLs. Do not upload case files to a public repository.

`q4r verify CASE_DIR` detects changed/missing files against `manifest.json`. Hashes support local integrity checks; they are not digital signatures, trusted timestamps or a legal chain of custody. Someone who can alter both evidence and the manifest can defeat this check. An interruption saves partial results; an operating-system crash during a write can still leave a partial file.

The **evidence score is a transparent ranking heuristic, not confidence probability**: exact public username +25, exact public display name +25 (otherwise a name phrase in returned text +10), exact explicitly published email +40; maximum 90. Source URLs and echoed query strings do not count as evidence. Scores never establish identity. Seed-to-finding graph edges mean “returned for this input”; accounts are not merged into one person. GraphML also records website/account links explicitly published in GitHub and DEV profiles, with the original finding's evidence ID. Identical linked URLs share a URL node; that is a published link relationship, not proof of common ownership. External links are not fetched automatically.

## Optional local AI draft

If you already run Ollama with an installed local model:

```bash
q4r scan --username YOUR_OWN_USERNAME --sources github --ai-model YOUR_INSTALLED_LOCAL_MODEL
```

This explicitly sends up to 40 result titles/snippets/signals to `127.0.0.1:11434` with proxy use disabled. No AI call runs by default, and offline mode never invokes it. Configure your Ollama server for local-only models and disable cloud features if strict local processing is required. Model names containing `cloud` are rejected, but Q4R cannot audit the server's internals. Nothing is downloaded or installed by this option.

The model's response must cite existing finding IDs. Q4R validates that IDs exist, **not that each claim is factually supported**. Treat the draft and source text as untrusted. AI output never changes scores, evidence, identity relationships or source coverage. If Ollama is unavailable, collection continues and reports remain usable.

## Development and tests

```bash
python3 -m unittest discover -s tests -v
python3 tests/check_packaging.py
python3 -m q4r scan --name "Example Researcher" --offline --out cases/smoke
python3 -m q4r verify cases/smoke
```

Unit/integration tests use mocked API responses, including negative/blocked cases and concurrent evidence writes. The separate packaging regression creates synthetic case files, an unrelated package and a nested clone, builds a wheel, checks that extra files are excluded, and installs/runs the CLI outside the checkout in a fresh environment. CI runs on Ubuntu with Python 3.10, 3.12 and 3.14. Live smoke tests use public service-owned profiles (and an official HN API example), not the investigation subject. Exit codes: `0` completed (may include skipped/unconfirmed/not-found checks), `2` invalid input, `3` partial run with error/blocked/partial coverage, `130` interrupted.

## بالعربي

Q4R أداة سطر أوامر للبحث في المصادر العامة. بتثبتها مرة واحدة بالأوامر فوق، وبعدها بتكتب `q4r` من أي مكان. بتدخل الاسم من جزأين أو أكثر، اسم المستخدم، والبريد إذا متوفر. بتقدر تستخدم اسم عربي طويل؛ كل مصدر إله حدوده الخاصة للبحث.

النسخة **0.2.0** بتفحص ملفات عامة مباشرة على Instagram وFacebook وYouTube وX وPinterest وTelegram وBluesky وMastodon وReddit وTikTok وDEV وHacker News، بدون مفتاح Brave. نجاح كل منصة يعتمد على توفر صفحتها العامة؛ إذا في حظر أو تسجيل دخول أو بيانات غير كافية، بتظهر الحالة بوضوح بدل اختراع نتيجة. بحث الاسم متوفر عبر Bluesky وGitHub، لكنه بيرجع مرشحين بحاجة مراجعة.

لتشغيل موصلات التواصل فقط: `q4r scan --username YOUR_USERNAME --sources social`. وللتشغيل التفاعلي اكتب `q4r`. التحديث: `git pull --ff-only` وبعده `pipx install --force .`. الشرطة السفلية `_` بتبقى باليوزرنيم؛ بعض المنصات ما بتسمح فيها. مفتاح Brave اختياري للبحث الإضافي بفهرس الويب. وجود 48 نطاقاً في الخطة لا يعني فحص 48 منصة مباشرة، وصفر نتائج ما بيثبت عدم وجود الحساب.

افتح `report.html` من مجلد الحالة لمراجعة النتائج، الأدلة، الأخطاء والرسم. الذكاء الاصطناعي المحلي اختياري للتلخيص فقط، ومخرجاته بحاجة مراجعة. استخدم الأداة ضمن نطاق مصرح، واحفظ التقارير محلياً لأنها ممكن تحتوي معلومات شخصية.

## API references

- [GitHub public users](https://docs.github.com/en/rest/users/users#get-a-user)
- [GitLab users](https://docs.gitlab.com/api/users/)
- [Keybase user lookup](https://keybase.io/docs/api/1.0/call/user/lookup)
- [Internet Archive advanced search](https://archive.org/advancedsearch.php)
- [Wayback availability API](https://archive.org/help/wayback_api.php)
- [Open Library search](https://openlibrary.org/dev/docs/api/search)
- [Brave web search](https://api-dashboard.search.brave.com/app/documentation/web-search/codes)
- [Ollama generate API](https://docs.ollama.com/api/generate)
- [Bluesky public HTTP API](https://docs.bsky.app/docs/api/app-bsky-actor-search-actors)
- [Mastodon account endpoints](https://docs.joinmastodon.org/methods/accounts/)
- [Reddit user endpoints](https://www.reddit.com/dev/api/)
- [Forem public user API](https://developers.forem.com/api/v0#tag/users)
- [Hacker News public API](https://github.com/HackerNews/API)

MIT licensed. Q4R is not affiliated with these services. Source availability, terms, limits and API schemas can change.
