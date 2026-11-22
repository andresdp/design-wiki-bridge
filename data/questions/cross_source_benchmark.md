# Cross-source Web Development QA Benchmark

This benchmark has 25 items: 20 answerable questions and 5 intentionally unanswerable controls. The answerable questions cover code-only, PDF-only, and code-plus-PDF grounding. Sources are pinned to the provided Flask 3.1.2 and Requests 2.32.5 checkouts and the listed PDFs; line numbers are 1-based source lines and PDF page numbers are physical PDF pages.

## Source inventory

- **Flask checkout:** `artifacts/checkouts/flask-3.1.2`, commit `2c1b30d0503cfb064f1cb252e6614a06915a362a`.
- **Requests checkout:** `artifacts/checkouts/requests-v2.32.5`, commit `b25c87d7cb8d6a18a37fa12442b5f883f9e41741`.
- **Flask Web Development, 2nd ed.** Miguel Grinberg: `artifacts/books/Flask Web Development Developing Web Applications With Python, 2nd Edition (Miguel Grinberg) (z-library.sk, 1lib.sk, z-lib.sk).pdf`.
- **Project paper:** `artifacts/paper/asse-paper.pdf`.
- **RFC 9110:** `artifacts/pdfs/rfc9110.pdf`.
- **RFC 9112:** `artifacts/pdfs/rfc9112.pdf`.
- **RFC 3875:** `artifacts/pdfs/rfc3875.pdf`.
- **Requests Web documentation:** [Advanced Usage — Requests documentation](https://requests.readthedocs.io/en/stable/user/advanced/), consulted 2026-11-21. The live stable docs currently identify themselves as Requests 2.34.2; the question uses only the Session behaviors corroborated by the pinned 2.32.5 code.

## Answerable items

### A. Code-only (1–7)

**1. Flask request lifecycle.** In Flask 3.1.2, what work does `full_dispatch_request` perform around endpoint dispatch?

**Answer:** It marks that the first request has occurred, sends the request-started signal, runs preprocessing, dispatches only if preprocessing returned `None`, routes exceptions through user-exception handling, and finalizes the resulting value as a response.

**Sources:** Flask `src/flask/app.py`, lines 904–920.

**2. Before-request short circuit.** What happens when a Flask `before_request` handler returns a non-`None` value?

**Answer:** Flask returns that value from preprocessing and stops further request handling; `full_dispatch_request` then finalizes it as the response instead of calling the view dispatch function.

**Sources:** Flask `src/flask/app.py`, lines 904–920 and 1271–1296.

**3. Flask return-value conversion.** Which common view return values does Flask convert, and how is a 2-tuple interpreted?

**Answer:** Strings and bytes become response bodies; dicts and lists become JSON responses; generators/iterators become streaming responses; response objects are retained or coerced. A 2-tuple is interpreted as `(body, headers)` when its second element is a headers-like object, otherwise as `(body, status)`.

**Sources:** Flask `src/flask/app.py`, lines 1129–1202.

**4. Flask response hooks.** In what order does Flask run after-request handlers, and when does it save the session?

**Answer:** It first runs request-context after-request callbacks, then blueprint callbacks from the current blueprint outward to the app, reversing registration order within each scope. It saves the session after these callbacks, unless the session is a null session.

**Sources:** Flask `src/flask/app.py`, lines 1298–1324.

**5. Flask signed-cookie sessions.** How does the default session interface load a session, and what does it do with a bad signature?

**Answer:** It obtains the configured signing serializer, reads the session cookie, and loads the signed data using the permanent-session lifetime as the maximum age. If the cookie is missing or has a bad signature, it returns a new empty session. If no secret key produces a serializer, it returns `None`.

**Sources:** Flask `src/flask/sessions.py`, lines 317–349.

**6. Secure file serving.** Why does Flask recommend `send_from_directory` for a client-supplied file path?

**Answer:** It uses `safe_join` to prevent a client-crafted path from escaping the specified directory, and raises 404 when the final path is not an existing regular file. The directory itself must be trusted and must not be client-provided.

**Sources:** Flask `src/flask/helpers.py`, lines 533–574.

**7. Requests error handling.** Does calling `Response.raise_for_status()` raise for 3xx responses? Which status ranges raise?

**Answer:** No. It raises `HTTPError` for 4xx client errors and 5xx server errors; 3xx responses do not satisfy either condition.

**Sources:** Requests `src/requests/models.py`, lines 999–1026.

### B. PDF-only (8–13)

**8. Safe HTTP methods.** How does RFC 9110 define a safe method, and which standard methods does it identify as safe?

**Answer:** A method is safe when the client does not request, and does not expect, a state change in the target resource. GET, HEAD, OPTIONS, and TRACE are defined as safe.

**Sources:** `artifacts/pdfs/rfc9110.pdf`, §9.2.1, PDF pp. 72–73.

**9. Idempotency.** What does idempotent mean in HTTP, and which methods does RFC 9110 classify as idempotent?

**Answer:** A request method is idempotent when multiple identical requests have the same intended effect on the server as one request. PUT, DELETE, and safe methods are idempotent.

**Sources:** `artifacts/pdfs/rfc9110.pdf`, §9.2.2, PDF p. 73.

**10. HTTP request target forms.** Name the four request-target forms in HTTP/1.1 and their usual contexts.

**Answer:** Origin-form is used for ordinary direct requests; absolute-form is used when requesting through a proxy; authority-form is used with CONNECT; and asterisk-form is used with OPTIONS to address the server as a whole.

**Sources:** `artifacts/pdfs/rfc9112.pdf`, §3.2, PDF pp. 9–10.

**11. CGI path variables.** In CGI 1.1, what is the distinction between `SCRIPT_NAME` and `PATH_INFO`?

**Answer:** `SCRIPT_NAME` identifies the URI path leading to the script, while `PATH_INFO` is the additional path information following the script name. `PATH_INFO` may be empty.

**Sources:** `artifacts/pdfs/rfc3875.pdf`, §§4.1.5–4.1.6, PDF pp. 12–13.

**12. CGI query string.** Does CGI 1.1 require a query string to be form-decoded before it is placed in `QUERY_STRING`?

**Answer:** No. `QUERY_STRING` carries the query component of the URL, with the original encoding preserved; CGI does not require form decoding at this stage.

**Sources:** `artifacts/pdfs/rfc3875.pdf`, §4.1.7, PDF p. 13.

**13. Flask contexts in the supplied book.** Which Flask context exposes `request` and `session`, and which exposes `current_app` and `g`?

**Answer:** The request context exposes `request` and `session`; the application context exposes `current_app` and `g`. Flask pushes both contexts before dispatching a request and removes them after it is handled.

**Sources:** *Flask Web Development, 2nd ed.*, PDF pp. 39–40, in the source listed above.

### C. Code + external source (14–20)

**14. Request dispatch and method safety.** Flask automatically creates an OPTIONS response for a matched rule when automatic OPTIONS is enabled. Does RFC 9110 classify OPTIONS as safe, and is that classification contradicted by Flask's dispatch behavior?

**Answer:** OPTIONS is safe under RFC 9110. Flask's branch responds to the OPTIONS request without invoking the endpoint view, which is consistent with the method's safe semantics; the RFC definition concerns the requested effect on the resource, not whether a server performs internal work.

**Sources:** Flask `src/flask/app.py`, lines 879–902; `artifacts/pdfs/rfc9110.pdf`, §9.2.1, PDF pp. 72–73.

**15. Flask file responses and HTTP validators.** What conditional-response behavior does Flask's `send_file` expose by default, and what HTTP mechanisms help explain that behavior?

**Answer:** `send_file` defaults `conditional=True`, enabling conditional and range responses when a file path and request environment are available. RFC 9110 describes conditional requests and range requests as HTTP mechanisms for validating or selecting parts of representations.

**Sources:** Flask `src/flask/helpers.py`, lines 407–468; `artifacts/pdfs/rfc9110.pdf`, §§13–14, PDF pp. 80–88.

**16. Requests streaming and HTTP message bodies.** What does Requests `stream=True` change, and what does HTTP/1.1 say about message-body framing?

**Answer:** Requests avoids downloading the response content all at once; callers can consume it incrementally with `iter_content`. HTTP/1.1 framing determines the message body's length from rules such as `Content-Length`, `Transfer-Encoding`, or the response's connection semantics. Streaming affects client consumption, not the HTTP framing rules.

**Sources:** Requests `src/requests/models.py`, lines 799–835; `artifacts/pdfs/rfc9112.pdf`, §§6 and 6.3, PDF pp. 18–21.

**17. Flask WSGI entry and CGI variables.** At Flask's WSGI entry point, what input is used to create the request context, and which CGI 1.1 variables describe the request method and query component?

**Answer:** `wsgi_app` takes a WSGI `environ` mapping and passes it to `request_context`. CGI 1.1 names the request method `REQUEST_METHOD` and the URL query component `QUERY_STRING`.

**Sources:** Flask `src/flask/app.py`, lines 1479–1512; `artifacts/pdfs/rfc3875.pdf`, §§4.1.7 and 4.1.12, PDF pp. 13 and 15.

**18. Redirect method changes.** When Requests follows redirects, what does it do with request bodies and entity headers for statuses other than 307/308, and how does that align with RFC 9110's redirect semantics?

**Answer:** Requests removes `Content-Length`, `Content-Type`, and `Transfer-Encoding` and clears the body for redirect statuses other than 307/308, after rebuilding the method. RFC 9110 permits historical method rewriting for 301/302 and specifies that 307/308 preserve the method, matching the special handling in Requests.

**Sources:** Requests `src/requests/sessions.py`, lines 159–233; `artifacts/pdfs/rfc9110.pdf`, §§15.4.2–15.4.9, PDF pp. 100–103.

**19. Requests session cookies and connection reuse.** How does the pinned Requests implementation persist cookies, and what additional Session behavior does the official documentation describe?

**Answer:** Requests merges the Session cookie jar with request cookies during preparation and extracts response cookies into the Session jar after sending, so later requests can reuse them. The official documentation also describes Session-level persistence of cookies and connection pooling, which can reuse the underlying TCP connection for repeated requests to the same host.

**Sources:** Requests `src/requests/sessions.py`, lines 467–477 and 712–718; [Requests Advanced Usage, “Session Objects”](https://requests.readthedocs.io/en/stable/user/advanced/), consulted 2026-11-21.

**20. What does the paper say its hybrid RAG combines?** Which sources are integrated, and what mechanism does the paper describe for preserving attribution?

**Answer:** It integrates PDFs, GitHub repositories, and Web content in a unified retrieval and generation pipeline. The described pipeline preserves source metadata through format-specific processing and uses chunking, retrieval, and reranking to support explicit provenance attribution in generated responses.

**Sources:** `artifacts/paper/asse-paper.pdf`, PDF p. 3 (English paper page 2, “Our approach”); see also PDF p. 1, abstract.

## Unanswerable controls (21–25)

These questions intentionally ask for facts absent from the supplied project checkouts, paper, books, and RFC PDFs. The correct response is that the corpus does not establish the requested fact; do not guess or use outside knowledge.

**21. Unanswerable — Flask benchmark test coverage.** What percentage of Flask's full upstream test suite passed on Python 3.13 in the most recent CI run?

**Answer:** Cannot be answered from the supplied sources. They contain no current upstream CI run result or Python 3.13 pass percentage.

**Sources checked:** Flask checkout and supplied PDFs; no source in the corpus reports this CI metric.

**22. Unanswerable — Requests performance.** What is the median latency improvement, in milliseconds, of Requests 2.32.5 over Requests 2.31.0 on 100 concurrent HTTP/2 connections?

**Answer:** Cannot be answered from the supplied sources. The corpus contains no such benchmark results, and HTTP/2 concurrency is not established as a tested performance scenario here.

**Sources checked:** Requests checkout and supplied PDFs; no benchmark dataset or measurements for this comparison.

**23. Unanswerable — deployment history.** Which Flask maintainer first proposed the exact default session-cookie name used by Flask, and on what date?

**Answer:** Cannot be answered from the supplied sources. The code can expose the configured/default cookie-name behavior, but the proposal author and date require historical issue or commit records not included here.

**Sources checked:** Flask checkout and supplied PDFs; no proposal history is included.

**24. Unanswerable — runtime environment.** Which TLS cipher suite did the Requests checkout use in the developer's last production deployment?

**Answer:** Cannot be answered from the supplied sources. A source checkout does not identify a deployment's runtime TLS negotiation, and no deployment logs or configuration are supplied.

**Sources checked:** Requests checkout and supplied PDFs; no deployment configuration or runtime trace is included.

**25. Unanswerable — unsupported project claim.** How many Flask users in Argentina encountered a session-cookie signature failure during October 2026?

**Answer:** Cannot be answered from the supplied sources. The corpus contains implementation details but no production telemetry, user geography, or October 2026 incident counts.

**Sources checked:** Flask checkout and supplied PDFs; no production analytics or incident data is included.

## Coverage map

| Items | Grounding | Count |
|---|---|---:|
| 1–7 | Code only | 7 |
| 8–13 | PDF only (including the supplied book) | 6 |
| 14–18 | Code + PDF | 5 |
| 19 | Code + Web | 1 |
| 20 | Paper PDF only | 1 |
| 21–25 | Unanswerable controls | 5 |

