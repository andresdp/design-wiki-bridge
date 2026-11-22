# Test-set review checklist

Every test question must be reviewed before freezing. Mark each row: **accept**, **edit** (then fix the record in `test/<repo>.jsonl`), or **reject** (remove it). For drafted questions, after accepting, replace `review pending` in `provenance.edits` with `reviewed by <you>`.

Rules while reviewing: do not open the generated wikis or any run traces; book answers must hold for Flask 3.1.2.

## flask (30 questions)

| ID | Type | Origin | What to check | Note | Decision |
|---|---|---|---|---|---|
| `flask-pub-001` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; _got_registered_once / _check_setup_finished / setupmethod all present at the cited lines (sansio/blueprints.py | |
| `flask-pub-002` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; Flask and Blueprint send_static_file/get_send_file_max_age duplicates and the blueprint max_age comment match a | |
| `flask-pub-004` | public | SWE-QA, answer corrected | confirm the reference answer and the expected claims match the pinned code | sampled; test_blueprint_with_subdomain lines match; chain through BlueprintSetupState, testing.EnvironBuilder and create | |
| `flask-pub-008` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; FlaskCliRunner.invoke creates ScriptInfo(create_app=lambda: self.app) exactly as cited in testing.py 265-298 | |
| `flask-pub-018` | public | SWE-QA, answer corrected | confirm the reference answer and the expected claims match the pinned code | sampled; Blueprint.add_url_rule records a lambda via record(); deferred functions run in register() at the cited lines | |
| `flask-pub-022` | public | SWE-QA, answer corrected | confirm the reference answer and the expected claims match the pinned code | sampled; ResponseValue Mapping/TypedDict comment, StatusJSON test file and make_response dict/status handling verified ( | |
| `flask-pub-027` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; open_resource read-only check and open_instance_resource contrast verified in app.py and docs/config.rst The an | |
| `flask-pub-035` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; make_config swaps root_path for instance_path when instance_relative is true, as cited in sansio/app.py 482-496 | |
| `flask-pub-036` | public | SWE-QA, answer corrected | confirm the reference answer and the expected claims match the pinned code | sampled; FlaskGroup.get_command three-stage lookup verified (lines drift by about +8) | |
| `flask-pub-044` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; Config.from_object uses dir(obj), and test_config_from_class Base/Test inheritance verified | |
| `flask-cs-01` | code | yours, claims split by Claude | confirm the expected claims match your answer | orig item 1; code-only | |
| `flask-cs-02` | code | yours, claims split by Claude | confirm the expected claims match your answer | orig item 2; code-only | |
| `flask-cs-03` | code | yours, claims split by Claude | confirm the expected claims match your answer | orig item 3; code-only | |
| `flask-cs-04` | code | yours, claims split by Claude | confirm the expected claims match your answer | orig item 4; code-only | |
| `flask-cs-05` | code | yours, claims split by Claude | confirm the expected claims match your answer | orig item 5; code-only | |
| `flask-cs-06` | code | yours, claims split by Claude | confirm the expected claims match your answer | orig item 6; code-only | |
| `flask-cs-12r` | pdf | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | rewrites orig item 12 (CGI QUERY_STRING) against the book; book-only | |
| `flask-cs-13` | pdf | yours, claims split by Claude | confirm the expected claims match your answer | orig item 13; book-only (Grinberg pp. 39-40 verified) | |
| `flask-cs-14r` | code+pdf | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | rewrites orig item 14 (OPTIONS safety) against the book; code+book; HEAD is Werkzeug, kept out of claims | |
| `flask-cs-15r` | code+pdf | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | rewrites orig item 15 (send_file conditional) against the book; code+book; caching claim is code-only | |
| `flask-cs-17r` | code+pdf | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | rewrites orig item 17 (wsgi_app + CGI) against the book; code+book; do not ask about werkzeug.contrib imports | |
| `flask-cs-n1` | code+pdf | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | new code+book; stale book claims on the same page (before_first_request, after_request) must not be graded | |
| `flask-cs-n2` | code+pdf | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | new code+book | |
| `flask-cs-n3` | code+pdf | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | new code+book | |
| `flask-cs-n4` | code+pdf | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | new code+book | |
| `flask-cs-web1` | code+web | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | new code+web; SameSite omission is Werkzeug behaviour, shown through Flask's test | |
| `flask-cs-web2` | code+web | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | new code+web; weak retrievability in the draft check | |
| `flask-ctl-21` | control | yours | confirm the corpus really cannot answer it | orig item 21; control | |
| `flask-ctl-23` | control | yours | confirm the corpus really cannot answer it | orig item 23; control | |
| `flask-ctl-25` | control | yours | confirm the corpus really cannot answer it | orig item 25; control | |

## requests (20 questions)

| ID | Type | Origin | What to check | Note | Decision |
|---|---|---|---|---|---|
| `requests-pub-002` | public | SWE-QA, answer corrected | confirm the reference answer and the expected claims match the pinned code | sampled; InvalidSchema(RequestException, ValueError) and both raise sites (sessions.py:792, adapters.py:63) verified | |
| `requests-pub-016` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; resolve_redirects cookie extract/merge/prepare steps (sessions.py:235-242, 276) verified | |
| `requests-pub-020` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; Response lazy _content, __getstate__/__setstate__ and iter_content reuse logic verified in models.py | |
| `requests-pub-027` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; prepare_body NotImplementedError for stream+files and _encode_files fp.read() verified in models.py The incompa | |
| `requests-pub-029` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; morsel_to_cookie max-age to expires conversion (cookies.py:492-518) verified | |
| `requests-pub-033` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; ConnectionError hierarchy and HTTPAdapter.send translation sites verified, backed by quickstart docs | |
| `requests-pub-039` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; Server context manager and run() event/socket lifecycle (tests/testserver/server.py) verified | |
| `requests-pub-042` | public | SWE-QA, answer corrected | confirm the reference answer and the expected claims match the pinned code | sampled; _encode_files 'Data must not be a string' check and prepare_body dispatch verified, test at test_requests.py:91 | |
| `requests-pub-044` | public | SWE-QA, answer corrected | confirm the reference answer and the expected claims match the pinned code | sampled; prepare_url netloc reconstruction and prepare_auth/get_auth_from_url/HTTPBasicAuth path verified | |
| `requests-pub-046` | public | SWE-QA | confirm the reference answer and the expected claims match the pinned code | sampled; CaseInsensitiveDict.__iter__ over _store (structures.py:46-58) and test_list verified | |
| `requests-cs-07` | code | yours, claims split by Claude | confirm the expected claims match your answer | orig item 7; code-only | |
| `requests-cs-10r` | code+web | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | re-sources orig item 10 (RFC 9112 PDF) to the web; urllib3 CONNECT claim removed | |
| `requests-cs-16r` | code+web | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | re-sources orig item 16 (RFC 9112 PDF) to the web | |
| `requests-cs-18r` | code+web | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | re-sources orig item 18 (RFC 9110 PDF) to the web; the untested 'beyond the RFC' claims were left out | |
| `requests-cs-19r` | code+web | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | re-sources orig item 19 away from requests.readthedocs.io (excluded) to RFC 6265 and RFC 9112 | |
| `requests-cs-web1` | code+web | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | new code+web; weak retrievability in the draft check | |
| `requests-cs-web2` | code+web | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | new code+web; claims limited to Requests-generated headers; weak retrievability | |
| `requests-cs-web3` | code+web | drafted by Claude | accept, edit or reject; check claims, evidence and that it needs every cited source | new code+web; weak retrievability | |
| `requests-ctl-22` | control | yours | confirm the corpus really cannot answer it | orig item 22; control | |
| `requests-ctl-24` | control | yours | confirm the corpus really cannot answer it | orig item 24; control | |
