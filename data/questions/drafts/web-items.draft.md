# Code + Web draft items (Requests 2.32.5, Flask 3.1.2)

**Status of every item below: DRAFT - needs human review.**

Drafted 2026-11-22. Code line numbers are 1-based and were read directly from the pinned checkouts:

- Flask `artifacts/checkouts/flask-3.1.2` @ `2c1b30d0503cfb064f1cb252e6614a06915a362a` (HEAD verified)
- Requests `artifacts/checkouts/requests-v2.32.5` @ `b25c87d7cb8d6a18a37fa12442b5f883f9e41741` (HEAD verified)

Web passages were taken from pages fetched on 2026-11-22: the RFC HTML/TXT from `www.rfc-editor.org` (quotes copied from the .txt rendering, anchors checked in the .html), and MDN pages fetched with curl and stripped to text. No forbidden host (flask/werkzeug palletsprojects, github.com, pypi.org, requests.readthedocs.io, docs.python-requests.org) is cited.

Notes on the retrievability check:
- It used Claude's WebSearch tool (standard mode), **not Tavily**, so rankings will differ from what the system sees. Re-run with Tavily and `web.exclude_domains` before freezing.
- "Top 5" means the first five result links returned. Several RFC hits come back as `www.rfc-editor.org/rfc/inline-errata/rfcNNNN.html`. That page has the same text as the canonical RFC page, and I counted it as an equally good allowed page.

---

## A. Requests: re-sourced existing items (PDF replaced by web)

### req-web-10 (re-sources item 10: HTTP request-target forms) - DRAFT - needs human review

**Question.** HTTP/1.1 defines four request-target forms. Which form does Requests 2.32.5 put on the request line for (a) a direct request, (b) a plain-`http` request sent through an HTTP proxy, and (c) an `https` request through an HTTP proxy? Can Requests produce the asterisk-form at all?

**Short answer.** (a) Origin-form: `PreparedRequest.path_url` builds path plus `?query` and substitutes `/` for an empty path, as RFC 9112 requires. (b) Absolute-form: `HTTPAdapter.request_url` returns the full URL (fragment and userinfo stripped) when an HTTP (non-SOCKS) proxy is selected and the scheme is not `https`. (c) The request line keeps origin-form for `https` through a proxy. The tunnel is opened by a CONNECT request, whose authority-form target is produced by urllib3, outside Requests' own code. Requests never builds `*`. An OPTIONS request to `http://host` goes out as `OPTIONS /`, because the empty path becomes `/`. RFC 9112 reserves asterisk-form for server-wide OPTIONS.

**Expected claims.**
1. [code] `path_url` returns the URL's path, or `/` if the path is empty, followed by `?` and the query when present.
2. [code] `request_url` switches to the full URL (`urldefragauth(request.url)`) only when a proxy is selected, the scheme is not `https`, and the proxy is not SOCKS. Otherwise it uses `path_url`.
3. [web] RFC 9112 §3.2: origin-form is used for direct requests to an origin server, absolute-form for requests to a proxy, authority-form only for CONNECT, and asterisk-form only for server-wide OPTIONS.
4. [code+web] No Requests code path emits `*`, so Requests cannot send a server-wide OPTIONS request, and an empty path is sent as `/`, which matches the origin-form rule.

**Code evidence.**
- `src/requests/models.py` lines 85-104 (`RequestEncodingMixin.path_url`)
- `src/requests/adapters.py` lines 523-553 (`HTTPAdapter.request_url`) and 617 (called from `send`)

**Web evidence.**
- https://www.rfc-editor.org/rfc/rfc9112.html#section-3.2.1 (§3.2.1 origin-form): "When making a request directly to an origin server, other than a CONNECT or server-wide OPTIONS request (as detailed below), a client MUST send only the absolute path and query components of the target URI as the request-target. If the target URI's path component is empty, the client MUST send "/" as the path within the origin-form of request-target."
- https://www.rfc-editor.org/rfc/rfc9112.html#section-3.2.2 (§3.2.2 absolute-form): "When making a request to a proxy, other than a CONNECT or server-wide OPTIONS request (as detailed below), a client MUST send the target URI in "absolute-form" as the request-target."
- https://www.rfc-editor.org/rfc/rfc9112.html#section-3.2.3 (§3.2.3): "The "authority-form" of request-target is only used for CONNECT requests (Section 9.3.6 of [HTTP])."
- https://www.rfc-editor.org/rfc/rfc9112.html#section-3.2.4 (§3.2.4): "The "asterisk-form" of request-target is only used for a server-wide OPTIONS request (Section 9.3.7 of [HTTP])."

**Why web is needed.** The code says "path portion" versus "full URL" but never names the forms or says which contexts call for each one. Claims 3-4 (the names, the CONNECT and OPTIONS contexts, and whether Requests can produce asterisk-form) need the RFC.

**Retrievability.** Query: `HTTP/1.1 request-target origin-form absolute-form proxy authority-form asterisk-form`. Rank 2 is `https://www.rfc-editor.org/rfc/inline-errata/rfc9112.html` (same text as the cited page), so **yes**. Ranks 3-5 were datatracker pages and RFC 2616 on w3.org.

**Reviewer notes.** The claim about urllib3 and CONNECT in (c) is outside the pinned code. Drop it, or keep it only as "Requests does not switch to absolute-form for https". Also decide whether the wiki run (condition B) may cite `request_url`'s docstring (lines 526-527). It says "full URL" versus "path portion" but does not name the RFC forms.

---

### req-web-16 (re-sources item 16: stream=True and message-body framing) - DRAFT - needs human review

**Question.** What does `stream=True` change about how Requests reads a response body, and how does an HTTP/1.1 recipient determine where that body ends?

**Short answer.** When `stream=True`, Requests skips its eager `r.content` read, and the caller pulls data through `iter_content`, which reads from urllib3's `raw.stream` in chunks. The adapter always opens responses with `preload_content=False`. Without streaming, `Session.send` reads `r.content` right away. HTTP/1.1 framing does not depend on this choice. RFC 9112 §6.3 decides body length by precedence: HEAD/1xx/204/304 responses have no body, then Transfer-Encoding with chunked as the final coding, then a valid Content-Length, and otherwise, for a response, connection close. Streaming changes when the client reads the body, not how the body is delimited.

**Expected claims.**
1. [code] Without `stream`, `Session.send` touches `r.content` before returning, which reads the whole body. With `stream=True`, it does not.
2. [code] `iter_content` yields chunks from `self.raw.stream(chunk_size, decode_content=True)` (or `raw.read`), and the adapter calls urllib3 with `preload_content=False`.
3. [web] RFC 9112 §6.3: if Transfer-Encoding is present with chunked as the final coding, the length is found by decoding chunks. Otherwise a valid Content-Length gives the length in octets.
4. [web] A response with no declared length is delimited by the server closing the connection.

**Code evidence.**
- `src/requests/sessions.py` lines 745-746 (`if not stream: r.content`)
- `src/requests/models.py` lines 799-835 (`Response.iter_content` generator)
- `src/requests/adapters.py` lines 644-656 (`conn.urlopen(..., preload_content=False, ...)`)

**Web evidence.**
- https://www.rfc-editor.org/rfc/rfc9112.html#section-6.3 (§6.3 rule 4): "If a Transfer-Encoding header field is present and the chunked transfer coding (Section 7.1) is the final encoding, the message body length is determined by reading and decoding the chunked data until the transfer coding indicates the data is complete."
- same page, §6.3 rule 6: "If a valid Content-Length header field is present without Transfer-Encoding, its decimal value defines the expected message body length in octets."
- same page, §6.3 rule 8: "Otherwise, this is a response message without a declared message body length, so the message body length is determined by the number of octets received prior to the server closing the connection."
- https://www.rfc-editor.org/rfc/rfc9112.html#section-6 (§6): "The presence of a message body in a request is signaled by a Content-Length or Transfer-Encoding header field. Request message framing is independent of method semantics."

**Why web is needed.** Requests code never states the framing precedence (claims 3-4). The question asks for both sides.

**Retrievability.** Query: `HTTP/1.1 message body length Content-Length Transfer-Encoding chunked precedence`. Rank 4 is `https://www.rfc-editor.org/rfc/inline-errata/rfc9112.html`, so **yes**. Ranks 1-3 were the RFC 2068 mirror on freesoft.org, RFC 2616 on w3.org, and a draft on datatracker.

**Reviewer notes.** The `iter_content` docstring (lines 800-804) states the "avoids reading the content at once into memory" half of the answer, so claim 1 could come from the docstring alone. Claims 3-4 still need the web source. This narrows the original span 799-835 by adding sessions.py 745-746.

---

### req-web-18 (re-sources item 18: redirect method and body handling) - DRAFT - needs human review

**Question.** When Requests 2.32.5 follows a redirect, how does it change the method and the request body for 301, 302, 303, 307 and 308? Where does that agree with RFC 9110, and where does it go beyond what the RFC describes?

**Short answer.** `rebuild_method` turns 303 into GET (except HEAD), any non-HEAD method on 302 into GET, and POST on 301 into GET. For every redirect status except 307/308, `resolve_redirects` then removes `Content-Length`, `Content-Type` and `Transfer-Encoding` and sets the body to `None`. RFC 9110 says 307 MUST NOT change the method (308 is the permanent counterpart) and allows POST→GET on 301/302 "for historical reasons", so 307/308 preservation and 301/302 POST→GET agree. Requests goes further in two ways. It rewrites every non-HEAD method on 302, not only POST. It also drops the body on 301/302/303 even when the method did not change (for example PUT on 301), while RFC 9110's redirect steps tie removal of content headers to the method becoming GET or HEAD.

**Expected claims.**
1. [code] 303 → GET unless HEAD; 302 → GET unless HEAD; 301 → GET only for POST.
2. [code] For any status other than 307/308, the three headers are popped and `body = None`.
3. [web] RFC 9110: 307 MUST NOT change the method, and 301/302 MAY change POST to GET for historical reasons.
4. [web] RFC 9110 §15.4 step 5 removes content-specific headers when the method has been changed to GET or HEAD.

**Code evidence.**
- `src/requests/sessions.py` lines 221-232 (calls `rebuild_method`, then purges the headers and body unless 307/308)
- `src/requests/sessions.py` lines 333-353 (`rebuild_method`)

**Web evidence.**
- https://www.rfc-editor.org/rfc/rfc9110.html#section-15.4.3 (§15.4.3 302 Found, Note): "For historical reasons, a user agent MAY change the request method from POST to GET for the subsequent request. If this behavior is undesired, the 307 (Temporary Redirect) status code can be used instead." (§15.4.2 has the same note for 301, pointing to 308.)
- https://www.rfc-editor.org/rfc/rfc9110.html#section-15.4.8 (§15.4.8): "The 307 (Temporary Redirect) status code indicates that the target resource resides temporarily under a different URI and the user agent MUST NOT change the request method if it performs an automatic redirection to that URI."
- https://www.rfc-editor.org/rfc/rfc9110.html#section-15.4 (§15.4, step 5): "If the request method has been changed to GET or HEAD, remove content-specific header fields, including (but not limited to) Content-Encoding, Content-Language, Content-Location, Content-Type, Content-Length, Digest, Last-Modified."

**Why web is needed.** The code comments link only to RFC 7231 and to GitHub issues, and say "Do what the browsers do, despite standards". The question asks how far the code matches the standard (claims 3-4).

**Retrievability.** Query: `HTTP redirect 301 302 POST changed to GET 307 308 preserve method RFC 9110`. Ranks 1-4 include `https://www.rfc-editor.org/rfc/inline-errata/rfc9110.html` (rank 1) and `https://www.rfc-editor.org/rfc/rfc9110.txt` (rank 4), so **yes**. MDN "Redirections in HTTP" appeared at rank 6 and is another allowed page.

**Reviewer notes.** The original item cited lines 159-233. I narrowed it to 221-232 plus 333-353. The claim that Requests goes beyond the RFC ("PUT on 301 loses its body") is an inference from reading the code; consider a quick offline test to confirm it. The RFC 9110 §15.4.8 text has a known erratum (EID 7109) about the 307/308 notes. The quoted sentence is unaffected.

---

### req-web-19 (re-sources item 19: session cookies and connection reuse) - DRAFT - needs human review

**Question.** How does a Requests 2.32.5 `Session` carry cookies from one response into later requests, and what lets it reuse a TCP connection across requests? Relate each to what the HTTP cookie and HTTP/1.1 connection specifications require.

**Short answer.** Cookies: `prepare_request` merges the Session jar with any per-request cookies. After every send, `Session.send` extracts `Set-Cookie` values from the final response and from every response in the redirect history into `self.cookies`, with no status-code filter. This is the RFC 6265 model: the server stores state with Set-Cookie, and the user agent returns it in a Cookie header on later requests, processing Set-Cookie on 4xx/5xx responses too. Connection reuse: each Session mounts `HTTPAdapter`s whose urllib3 `PoolManager` keeps connections. HTTP/1.1 connections persist by default, but a client may reuse one only after reading the whole previous response body. That is why Requests reads `r.content` when not streaming, and why redirect handling consumes `resp.content` before calling `resp.close()` to return the connection to the pool.

**Expected claims.**
1. [code] Session cookies are merged into each prepared request, and response cookies (including those from redirect history) are extracted into the Session jar after each send.
2. [web] RFC 6265 §3: the server sends Set-Cookie, and the user agent returns stored cookies in a Cookie header on subsequent requests. Set-Cookie on 4xx/5xx responses must still be processed.
3. [code] A Session mounts an `HTTPAdapter` for `http://` and `https://`, each backed by a urllib3 `PoolManager`, and redirect handling reads the old response body and then calls `resp.close()` to release the connection to the pool.
4. [web] RFC 9112 §9.3: HTTP/1.1 connections are persistent by default, and a client MUST read the entire response body if it intends to reuse the connection.

**Code evidence.**
- `src/requests/sessions.py` lines 467-476 (merge Session and request cookies)
- `src/requests/sessions.py` lines 712-718 (persist cookies from history and the final response)
- `src/requests/sessions.py` lines 446-449 (mount default `HTTPAdapter`s)
- `src/requests/adapters.py` lines 216-240 (`init_poolmanager` → `PoolManager(num_pools=..., maxsize=...)`)
- `src/requests/sessions.py` lines 185-196 (redirect loop: `resp.content  # Consume socket so it can be released` … `resp.close()`)

**Web evidence.**
- https://www.rfc-editor.org/rfc/rfc6265.html#section-3 (§3 Overview): "To store state, the origin server includes a Set-Cookie header in an HTTP response. In subsequent requests, the user agent returns a Cookie request header to the origin server."
- same section: "User agents MAY ignore Set-Cookie headers contained in responses with 100-level status codes but MUST process Set-Cookie headers contained in other responses (including responses with 400- and 500-level status codes)."
- https://www.rfc-editor.org/rfc/rfc9112.html#section-9.3 (§9.3 Persistence): "HTTP/1.1 defaults to the use of "persistent connections", allowing multiple requests and responses to be carried over a single connection."
- same section: "Likewise, a client MUST read the entire response message body if it intends to reuse the same connection for a subsequent request."

**Why web is needed.** The code shows the mechanisms (jar merge, pool manager, consume-then-release). Why the body must be drained before reuse, and that Set-Cookie on error responses must be honoured, come only from the RFCs (claims 2 and 4).

**Retrievability.**
- Cookie query: `Set-Cookie response header Cookie request header user agent returns cookies subsequent requests RFC 6265`. Rank 1 is `https://www.rfc-editor.org/rfc/rfc6265` (a canonical variant), so **yes**.
- Persistence query, natural phrasing: `HTTP/1.1 persistent connections client must read entire response body to reuse connection`. No RFC in the top 5 (OpenJDK and Go threads), so **no**. With the RFC named (`RFC 9112 persistence HTTP/1.1 persistent connections self-defined message length`), rank 1 is `https://www.rfc-editor.org/rfc/inline-errata/rfc9112.html`, so **yes**.

**Reviewer notes.** The original answer's phrase "official documentation describes Session-level persistence" is replaced. The new answer no longer depends on any Requests docs page. The `Session` class docstring (sessions.py line 359: "Provides cookie persistence, connection-pooling, and configuration.") gives the headline, so make sure the graded claims are claims 2-4. Persistence retrieval is weak without RFC terms. Decide whether the question wording should mention "HTTP/1.1 persistent connections" to help it.

---

## A2. Requests: new code + web items

### req-web-new-1: Fragment handling across redirects - DRAFT - needs human review

**Question.** Requests 2.32.5 requests `http://example.org/a#frag` and gets a 301 whose `Location` has no fragment. What URL does Requests request next, and what HTTP rule does this follow?

**Short answer.** Requests records the original URL's fragment (`previous_fragment`). When a redirect target has no fragment, it puts the previous fragment back on the target URL. When the target has its own fragment, that fragment becomes the new `previous_fragment` for later hops. The next request therefore uses the Location URL with `#frag` added. This follows RFC 9110 §10.2.2: a user agent MUST process a 3xx redirect whose Location lacks a fragment as if it inherited the original reference's fragment. (The fragment is not sent on the wire. `path_url` sends only the path and query.)

**Expected claims.**
1. [code] `previous_fragment` starts as the fragment of the original request URL.
2. [code] If the parsed Location has an empty fragment and a previous fragment exists, the URL is rebuilt with the previous fragment. A non-empty Location fragment replaces it.
3. [web] RFC 9110 §10.2.2 requires a user agent to treat a fragment-less 3xx Location as inheriting the original fragment.

**Code evidence.**
- `src/requests/sessions.py` lines 175-176 (`previous_fragment = urlparse(req.url).fragment`)
- `src/requests/sessions.py` lines 203-209 (fragment inheritance)
- `src/requests/models.py` lines 86-104 (`path_url` omits the fragment; supports the "not on the wire" remark)

**Web evidence.**
- https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.2 (§10.2.2 Location): "If the Location value provided in a 3xx (Redirection) response does not have a fragment component, a user agent MUST process the redirection as if the value inherits the fragment component of the URI reference used to generate the target URI (i.e., the redirection inherits the original reference's fragment, if any)."

**Why web is needed.** The code comment cites "RFC 7231 7.1.2" but does not state the rule or whether it is mandatory. Claim 3 (the current RFC 9110 rule and its MUST) needs the web source.

**Retrievability.** Query: `Location header 3xx redirect without fragment inherits fragment of original URI`. Top 5: Mozilla bugzilla, drupal.org, WebKit bugzilla, two w3.org mailing-list posts, so **no**. Query `RFC 9110 Location header redirect fragment inherits`: the top 5 has only RFC 7231 erratum pages (rfc-editor.org/errata/eid6354), and `rfc-editor.org/rfc/rfc9110.txt` is at rank 14, so **no**.

**Reviewer notes.** Retrieval is poor. Keep the item only if the benchmark accepts a hard web item, or adjust the question wording toward "RFC 9110 Location". The code comment's RFC 7231 reference may lead a system to cite RFC 7231 instead. Decide whether that counts.

---

### req-web-new-2: Request-body framing (Content-Length vs chunked) - DRAFT - needs human review

**Question.** How does Requests 2.32.5 frame a request body whose length it cannot determine (for example a generator), and how does it frame a POST with no body? Do both choices meet HTTP's framing requirements?

**Short answer.** For a streamed body, Requests calls `super_len`. If a length is found, it sets `Content-Length`. Otherwise it sets `Transfer-Encoding: chunked` and no Content-Length, and the adapter passes `chunked=True` to urllib3 because the body is non-null and has no Content-Length. For a method other than GET or HEAD with no body, `prepare_content_length` sets `Content-Length: 0`. Both meet the requirements. RFC 9112 says a sender MUST NOT send Content-Length together with Transfer-Encoding, and describes chunked as the way to send content of unknown size. RFC 9110 says a user agent normally sends Content-Length in a POST even when it is 0.

**Expected claims.**
1. [code] Streamed bodies get `Content-Length` when `super_len` returns a length, and `Transfer-Encoding: chunked` otherwise, never both.
2. [code] The adapter sends chunked when `request.body is not None` and there is no `Content-Length` header.
3. [code] With no body, methods other than GET/HEAD get `Content-Length: 0`.
4. [web] RFC 9112 §6.2 forbids sending Content-Length with Transfer-Encoding. §7.1 says chunked lets content of unknown size be transferred. RFC 9110 §8.6 says a user agent normally sends Content-Length in a POST even when it is 0.

**Code evidence.**
- `src/requests/models.py` lines 524-551 (streamed-body branch: `super_len`, Content-Length or `Transfer-Encoding: chunked`)
- `src/requests/models.py` lines 572-586 (`prepare_content_length`, including `"0"` for methods that are not GET/HEAD)
- `src/requests/adapters.py` line 627 (`chunked = not (request.body is None or "Content-Length" in request.headers)`) and line 655 (`chunked=chunked`)

**Web evidence.**
- https://www.rfc-editor.org/rfc/rfc9112.html#section-6.2 (§6.2): "A sender MUST NOT send a Content-Length header field in any message that contains a Transfer-Encoding header field."
- https://www.rfc-editor.org/rfc/rfc9112.html#section-7.1 (§7.1): "Chunked enables content streams of unknown size to be transferred as a sequence of length-delimited buffers, which enables the sender to retain connection persistence and the recipient to know when it has received the entire message."
- https://www.rfc-editor.org/rfc/rfc9110.html#section-8.6 (§8.6): "For example, a user agent normally sends Content-Length in a POST request even when the value is 0 (indicating empty content)."

**Why web is needed.** The code makes the choices but never states the rules they must satisfy, such as the MUST NOT on sending both headers or the expectation of Content-Length 0 on POST (claim 4).

**Retrievability.** Query: `request body unknown length Transfer-Encoding chunked must not send Content-Length with Transfer-Encoding`. Top 5: golang issue, two IBM support pages, curl mailing list, nginx trac, so **no**. The RFC text is quoted inside those pages, but they are not allowed sources.

**Reviewer notes.** A user can pass both headers by hand, and this code does not prevent it, so restrict the claim to Requests-generated headers. Retrieval is weak. A query with "RFC 9112" in it would probably reach the RFC (not tested).

---

### req-web-new-3: Authorization and Cookie headers on redirects - DRAFT - needs human review

**Question.** When Requests 2.32.5 follows a redirect, when does it drop the `Authorization` header, and is its rule the same as a strict "cross-origin" rule (scheme, host, port)? What does it do with the `Cookie` header?

**Short answer.** `rebuild_auth` deletes `Authorization` when `should_strip_auth(old, new)` is true. That happens when the hostname changes, or when the scheme or port changes. There are two exceptions: same-scheme changes between the default port and an omitted port, and an `http` (port 80 or unset) to `https` (port 443 or unset) upgrade on the same host, where the header is kept. Under the origin definition (scheme, host, port; RFC 9110 §4.3.1 and MDN), `http://h` and `https://h` are different origins. Browsers strip `Authorization` on cross-origin redirects, so Requests' http→https exception is more permissive than a strict cross-origin rule. Its code comment says the exception is kept for backwards compatibility. For cookies, Requests always removes the old `Cookie` header and rebuilds it from the cookie jar for the new URL. This matches RFC 9110's advice to remove or reconsider Authorization and Cookie when redirecting.

**Expected claims.**
1. [code] `should_strip_auth` returns True on a hostname change. It returns False for an http:80/None → https:443/None upgrade and for default-port equivalence within the same scheme. Otherwise it returns True on a port or scheme change.
2. [code] On redirect, the `Cookie` header is popped and re-prepared from the cookie jars, and `rebuild_auth` deletes `Authorization` when `should_strip_auth` is True.
3. [web] An origin is the (scheme, host, port) triple, and two origins are distinct if any part differs.
4. [web] Browsers strip `Authorization` on cross-origin redirects (MDN), and RFC 9110 §15.4 tells user agents to consider removing Authorization and Cookie when following redirects.

**Code evidence.**
- `src/requests/sessions.py` lines 127-157 (`should_strip_auth`)
- `src/requests/sessions.py` lines 234-242 (pop `Cookie`, merge jars, `prepare_cookies`)
- `src/requests/sessions.py` lines 282-295 (`rebuild_auth` deletes `Authorization`)

**Web evidence.**
- https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Authorization (introduction): "This header is stripped from cross-origin redirects."
- https://developer.mozilla.org/en-US/docs/Glossary/Origin (introduction): "Web content's origin is defined by the scheme (protocol), hostname (domain), and port of the URL used to access it. Two objects have the same origin only when the scheme, hostname, and port all match."
- https://www.rfc-editor.org/rfc/rfc9110.html#section-4.3.1 (§4.3.1): "Two origins are distinct if they differ in scheme, host, or port."
- https://www.rfc-editor.org/rfc/rfc9110.html#section-15.4 (§15.4, step 3): "Consider removing header fields that were not automatically generated by the implementation (i.e., those present in the request because they were added by the calling context) where there are security implications; this includes but is not limited to Authorization and Cookie."

**Why web is needed.** The code gives Requests' rule but not the definition of an origin or what browsers and the RFC recommend. Comparing the two (claims 3-4 and the "more permissive" verdict) needs the web sources.

**Retrievability.** Query: `Authorization header stripped on cross-origin redirect`. Top 5: support.mozilla.org, caniuse.com (an MDN compat entry), two blink-dev threads, bugzilla. The MDN page itself is **not** in the top 5. `developer.mozilla.org/en-US/docs/Glossary/origin` was at rank 16 for `RFC 9110 URI origin scheme host port distinct origins`, so **no** for the origin page as well.

**Reviewer notes.** The comment at sessions.py 133-136 says the http→https exception "isn't specified by RFC 7235", which hints at the answer. Check that the reference answer's "more permissive" verdict is acceptable wording. Retrieval is weak. Consider shortening the item to the RFC 9110 sources (§4.3.1 and §15.4), which share one page.

---

## B. Flask: new code + web items

### flask-web-new-1: Default session-cookie attributes and browser treatment - DRAFT - needs human review

**Question.** With Flask 3.1.2's default configuration, which of the `HttpOnly`, `Secure` and `SameSite` attributes does the session cookie carry, and how will a browser treat a cookie with that combination?

**Short answer.** Defaults: `SESSION_COOKIE_HTTPONLY=True`, `SESSION_COOKIE_SECURE=False`, `SESSION_COOKIE_SAMESITE=None`. `save_session` passes these values straight to `response.set_cookie`. The cookie is therefore `HttpOnly`, not `Secure`, and has no `SameSite` attribute (Flask's own test asserts "samesite" is absent when the setting is None). Per MDN: `HttpOnly` stops JavaScript from reading the cookie through `document.cookie`. Without `Secure`, the browser also sends the cookie over plain `http:`, because `Secure` limits sending to `https:`. With `SameSite` unspecified, some browsers apply `Lax` by default. Flask does not tie `SameSite` to `Secure`, but MDN says `SameSite=None` requires `Secure`, so a developer who sets `SESSION_COOKIE_SAMESITE="None"` must also set `SESSION_COOKIE_SECURE=True`.

**Expected claims.**
1. [code] Default config: HttpOnly True, Secure False, SameSite None. `save_session` passes httponly, secure and samesite to `set_cookie` as configured.
2. [code] With `SESSION_COOKIE_SAMESITE=None`, the emitted Set-Cookie has no SameSite attribute (Flask test).
3. [web] MDN: HttpOnly forbids JavaScript access, and Secure means the cookie is sent only on `https:` requests (except localhost).
4. [web] MDN: some browsers default to Lax when SameSite is unspecified, and SameSite=None requires Secure.

**Code evidence.**
- `src/flask/app.py` lines 193-196 (defaults for HTTPONLY, SECURE, PARTITIONED, SAMESITE)
- `src/flask/sessions.py` lines 209-227 (`get_cookie_httponly` / `get_cookie_secure` / `get_cookie_samesite` return config values unchanged)
- `src/flask/sessions.py` lines 386-398 (`response.set_cookie(..., httponly=..., secure=..., samesite=...)`)
- `tests/test_basic.py` lines 345-348 (SameSite None → `"samesite" not in cookie`)

**Web evidence.**
- https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Set-Cookie#httponly : "Forbids JavaScript from accessing the cookie, for example, through the Document.cookie property."
- same page `#secure`: "Indicates that the cookie is sent to the server only when a request is made with the https: scheme (except on localhost), and therefore, is more resistant to manipulator in the middle (MITM) attacks."
- same page `#lax`: "Some browsers use Lax as the default value if SameSite is not specified: see Browser compatibility for details."
- same page `#none`: "Send the cookie with both cross-site and same-site requests. The Secure attribute must also be set when using this value."

**Why web is needed.** The code shows which attributes are set but not what browsers do with them, such as the missing-SameSite default, the SameSite=None plus Secure requirement, and the https-only sending (claims 3-4).

**Retrievability.** Query: `Set-Cookie SameSite default Lax not specified Secure HttpOnly attributes`. Rank 5 is `developer.mozilla.org/en-US/Web/HTTP/Headers/Set-Cookie` (an MDN redirect to the cited page), so **yes**, though only just. Ranks 1-4 were Google privacy-sandbox and web.dev pages, which are also allowed and give the same Lax-default guidance.

**Reviewer notes.** Leaving out SameSite when the value is None is done by Werkzeug's `dump_cookie`, which is outside the pinned checkout. Claim 2 therefore rests on the Flask test file, not on Flask source. The getter docstrings (sessions.py 222-226) describe the configuration but not browser behaviour. MDN wording is live and changes (the page shows "last modified" dates), so snapshot the passage when freezing.

---

### flask-web-new-2: Why Flask adds `Vary: Cookie` - DRAFT - needs human review

**Question.** When does Flask 3.1.2 add `Vary: Cookie` to a response, and what does that header oblige an HTTP cache to do?

**Short answer.** `SecureCookieSession` sets `accessed=True` whenever the session is read (`__getitem__`, `get`, `setdefault`) or modified. `save_session` adds `Vary: Cookie` when the session was accessed, even if no Set-Cookie is written. It also adds it when the session cookie is deleted or set. A request that never touches the session gets no `Vary: Cookie` from the session layer. Under RFC 9110 §12.5.5, a cache MUST NOT use such a stored response for a later request unless that request has the same Cookie value (or the origin validates reuse). Vary adds the listed fields to the cache key, so a page built from one user's session is not served to another user.

**Expected claims.**
1. [code] Reading the session through `[]`, `get` or `setdefault`, or modifying it, sets `accessed=True`.
2. [code] `save_session` adds `Vary: Cookie` if `session.accessed`, and also when it deletes or sets the cookie.
3. [web] RFC 9110 §12.5.5: caches MUST NOT reuse the response unless the later request has the same values for the listed header fields (or reuse is validated), so Vary widens the cache key.

**Code evidence.**
- `src/flask/sessions.py` lines 72-94 (`accessed` flag and the methods that set it)
- `src/flask/sessions.py` lines 362-364 (Vary when accessed), 379 (on delete), 399 (on set)

**Web evidence.**
- https://www.rfc-editor.org/rfc/rfc9110.html#section-12.5.5 (§12.5.5 Vary, purpose 1): "To inform cache recipients that they MUST NOT use this response to satisfy a later request unless the later request has the same values for the listed header fields as the original request (Section 4.1 of [CACHING]) or reuse of the response has been validated by the origin server. In other words, Vary expands the cache key required to match a new request to the stored cache entry."
- (alternative) https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Vary (introduction): "Including a Vary header ensures that responses are separately cached based on the headers listed in the Vary field."

**Why web is needed.** The code decides when the header is added. What a cache must do with it, including the MUST NOT and the cache-key effect, comes from the RFC (claim 3).

**Retrievability.** Query: `Vary: Cookie header cache meaning response varies by Cookie`. Top 5: developers.cloudflare.com (two), learn.microsoft.com blog, blog.cloudflare.com, bugzilla, so **no**. Query `RFC 9110 Vary header field cache key selecting header fields`: MDN Vary is at rank 9 and RFC 9110 is not returned, so **no**.

**Reviewer notes.** The comment at sessions.py 68-71 already says the purpose is to "allow caching proxies to cache different pages for different users". A code-only system can give the gist, so grading should weight claim 3's normative MUST NOT. Retrieval is poor. smashingmagazine.com ("Understanding The Vary Header") appeared for related queries, is not Flask-specific, and might be acceptable as a fallback.

---

### flask-web-new-3: Automatic OPTIONS responses and the Allow header - DRAFT - needs human review

**Question.** A Flask 3.1.2 route is declared with `methods=["GET", "POST"]`. What does Flask return for `OPTIONS` on that URL, how is the `Allow` value built, and what does HTTP say an OPTIONS response and the `Allow` header should contain?

**Short answer.** `add_url_rule` adds `OPTIONS` to the rule's methods and marks the rule `provide_automatic_options` when OPTIONS was not listed and `PROVIDE_AUTOMATIC_OPTIONS` is on. `dispatch_request` then answers OPTIONS without calling the view, through `make_default_options_response`. That method asks the URL adapter for `allowed_methods()` across all rules matching the URL and puts the result into `Allow` on an empty response. Flask's test expects `GET, HEAD, OPTIONS, POST`, with HEAD added by Werkzeug's routing. RFC 9110 says a successful OPTIONS response SHOULD include headers such as `Allow` that describe the resource's options. `Allow` lists the methods the target resource supports, and it MUST be sent on 405 responses.

**Expected claims.**
1. [code] OPTIONS is added automatically to a rule's methods unless the view lists it or automatic options are disabled.
2. [code] For OPTIONS on such a rule, Flask returns `make_default_options_response()`, an empty response whose `allow` set holds `adapter.allowed_methods()`, and the view function does not run.
3. [web] RFC 9110 §9.3.7: a successful OPTIONS response SHOULD send headers such as Allow that indicate optional features applicable to the target resource.
4. [web] RFC 9110 §10.2.1: Allow lists the methods supported by the target resource and MUST be generated in a 405 response.

**Code evidence.**
- `src/flask/sansio/app.py` lines 640-651 (automatic OPTIONS added to `required_methods`, then to `methods`; flag stored on the rule)
- `src/flask/app.py` lines 893-899 (dispatch short-circuits OPTIONS)
- `src/flask/app.py` lines 953-964 (`make_default_options_response`)
- `tests/test_basic.py` lines 30-50 (expected `Allow` sets for one and two rules on "/")

**Web evidence.**
- https://www.rfc-editor.org/rfc/rfc9110.html#section-9.3.7 (§9.3.7 OPTIONS): "A server generating a successful response to OPTIONS SHOULD send any header that might indicate optional features implemented by the server and applicable to the target resource (e.g., Allow), including potential extensions not defined by this specification."
- https://www.rfc-editor.org/rfc/rfc9110.html#section-10.2.1 (§10.2.1 Allow): "The "Allow" header field lists the set of methods advertised as supported by the target resource. … An origin server MUST generate an Allow header field in a 405 (Method Not Allowed) response and MAY do so in any other response."
- (alternative) https://developer.mozilla.org/en-US/docs/Web/HTTP/Reference/Headers/Allow (introduction): "The HTTP Allow response header lists the set of request methods supported by a resource. This header must be sent if the server responds with a 405 Method Not Allowed status code to indicate which request methods can be used instead."

**Why web is needed.** The code builds the Allow set but never says what OPTIONS responses or Allow are meant to convey, or that Allow is mandatory on 405 (claims 3-4).

**Retrievability.** Query: `OPTIONS method response Allow header list supported methods`. Rank 1 is MDN OPTIONS (`developer.mozilla.org/docs/Web/HTTP/Methods/OPTIONS`, which redirects to the Reference path), so **yes**. MDN Allow is at rank 6. RFC 9110 is not in the top 5. MDN OPTIONS says an example "response contains an Allow header that lists the allowed methods", which supports claim 3 in weaker wording.

**Reviewer notes.** This overlaps with existing item 14, which cites the same `dispatch_request` lines (879-902) for OPTIONS safety. This item is about Allow contents. Merge it with item 14 or drop one. HEAD comes from Werkzeug, so it can only be shown through the Flask test file. The 405 path itself is also Werkzeug (`MethodNotAllowed`), outside the checkout, so claim 4 stays web-only. Do not grade "Flask sends Allow on 405" as a code claim.

---

## Summary table

| id | repo | web host(s) | top-5 retrievable? |
|---|---|---|---|
| req-web-10 | Requests | rfc-editor.org (RFC 9112 §3.2) | yes (rank 2, inline-errata) |
| req-web-16 | Requests | rfc-editor.org (RFC 9112 §6, §6.3) | yes (rank 4, inline-errata) |
| req-web-18 | Requests | rfc-editor.org (RFC 9110 §15.4.x) | yes (rank 1, inline-errata) |
| req-web-19 | Requests | rfc-editor.org (RFC 6265 §3, RFC 9112 §9.3) | cookies yes; persistence only with "RFC 9112" in the query |
| req-web-new-1 | Requests | rfc-editor.org (RFC 9110 §10.2.2) | no |
| req-web-new-2 | Requests | rfc-editor.org (RFC 9112 §6.2, §7.1; RFC 9110 §8.6) | no |
| req-web-new-3 | Requests | MDN Authorization, MDN Origin, RFC 9110 §4.3.1/§15.4 | no |
| flask-web-new-1 | Flask | MDN Set-Cookie | yes (rank 5) |
| flask-web-new-2 | Flask | RFC 9110 §12.5.5 (MDN Vary alt) | no |
| flask-web-new-3 | Flask | RFC 9110 §9.3.7/§10.2.1, MDN OPTIONS/Allow | yes (MDN OPTIONS rank 1) |
