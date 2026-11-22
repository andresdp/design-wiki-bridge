# Flask book re-sourcing map for cross_source_benchmark.md

All page numbers are physical PDF pages, 1-based (PyMuPDF index + 1). All code spans were read from `artifacts/checkouts/flask-3.1.2` (commit `2c1b30d0…`). Printed page numbers are given in brackets where they differ.

## Book versions (as stated in the books)

| Book | Stated Flask version | Evidence | Notes |
|---|---|---|---|
| Grinberg, *Flask Web Development* 2nd ed. (O'Reilly, March 2018) | **Flask 0.12.2**, Werkzeug 0.12.2 (not 1.0) | `pip freeze` output, PDF p. 27 and p. 116 | Python 3.5/3.6, 2.7 optional (p. 24) |
| Aggarwal, *Flask Framework Cookbook* (Packt, Nov 2014; 1st ed.) | **Flask 0.10.1**, Werkzeug 0.9.6 | `requirements.txt`, PDF p. 223; traceback path `Flask-0.10.1-py2.7.egg` (p. 192) | Python 2.7 era |
| Aronowitz, *Flask Framework Cookbook* (97 pp.) | **Not stated** | Sample DB output is dated 2017-04-08; mixes `flask_sqlalchemy` and old `flask.ext.sqlalchemy` imports | Covers only Jinja filters, Flask-SQLAlchemy, WTForms validators. No coverage of HTTP, WSGI, contexts, routing internals, so it supports none of the items below |

Book claims that **no longer match Flask 3.1.2** (do not build questions on them):

1. Grinberg p. 42: `before_first_request` hook. Removed in Flask 2.3.0 (CHANGES.rst line 157).
2. Grinberg p. 42: `after_request` "only if no unhandled exceptions occurred". Since 1.1.0, `handle_exception` runs `finalize_request(..., from_error_handler=True)`, so after-request functions also run on the default 500 response (`src/flask/app.py` 833–835, 862). This is still true when exceptions propagate in debug/testing mode.
3. Grinberg pp. 262, 273: `werkzeug.contrib.profiler` / `werkzeug.contrib.fixers.ProxyFix`. `werkzeug.contrib` was removed in Werkzeug 1.0 (now `werkzeug.middleware.*`). The wrapping pattern is still valid. The import paths are not.
4. Grinberg p. 39: contexts make variables "globally accessible to a thread". Flask 3.1.2 uses `contextvars.ContextVar` (`src/flask/globals.py` 24, 42), not thread-locals. The context split itself is unchanged.
5. Aronowitz uses `flask.ext.sqlalchemy` imports (removed in Flask 1.0). This is irrelevant to the items here.

---

## Existing items

### Item 8 (safe HTTP methods): **(c) drop or move**

No book defines "safe" methods. The only trace is Aggarwal's AJAX CSRF snippet that skips `GET|HEAD|OPTIONS|TRACE` (PDF p. 110), which gives no definition or rationale. Keep it RFC-sourced or move it to a Web source. Otherwise, replace it with a book-only PDF item:

**Replacement 8′ (book-only).** In Grinberg's REST chapter, what do POST, PUT and DELETE mean for a resource, which success status codes go with them, and what happens when a client uses a method the resource does not support?
- **Answer:** POST to a collection URL creates a resource (201, with the new URL in `Location`). PUT to a resource URL modifies it, or creates it when the client picks the URL (200 or 204). DELETE removes a resource or collection (200 or 204). An unsupported method gets 405 Method Not Allowed, which Flask handles automatically.
- **Source:** Grinberg PDF p. 223 [printed 201].
- **Quote:** "If the client invokes a method that is not supported for a given resource, then a response with the 405 status code (Method Not Allowed) should be returned. Flask handles this error automatically."
- **3.1.2 check:** still true (Werkzeug routing raises MethodNotAllowed).

### Item 9 (idempotent methods): **(c) drop or move**

None of the three books contains "idempotent" or an equivalent definition (searched for "idempot", "same result", "side effect"). Keep it on RFC 9110 or a Web source. If section B needs a book item here, use 8′ above or 11′/12′ below.

### Item 11 (CGI SCRIPT_NAME vs PATH_INFO): **(c) drop or move**

The books never mention `SCRIPT_NAME`, `PATH_INFO`, or CGI. Grinberg covers only `request.path` ("The path portion of the URL", p. 42). Book-only replacement:

**Replacement 11′ (book-only).** According to Grinberg, how does Flask treat a route whose URL ends with a slash when the client requests it without the slash, and the reverse?
- **Answer:** If a route is defined with a trailing slash and the client omits it, Flask automatically redirects to the trailing-slash URL. No redirect is issued in the reverse case.
- **Source:** Grinberg PDF p. 222 [printed 200].
- **Quote:** "If a client requests a URL without a trailing slash and there is a matching route that has a slash at the end, then Flask will automatically respond with a redirect to the trailing-slash URL. No redirects are issued for the reverse case."
- **3.1.2 check:** the behavior is still correct (Werkzeug `strict_slashes`). The book names no status code, and Werkzeug now uses 308, so keep the answer free of status codes.

### Item 12 (CGI QUERY_STRING): **(a/b) re-source as a book-only item about the request object**

No CGI coverage. Grinberg's request-object table covers the same ground (raw query string vs parsed arguments):

**Replacement 12′ (book-only).** In Grinberg's description of the Flask request object, how do `query_string`, `args`, and `environ` differ?
- **Answer:** `query_string` is the URL's query-string portion as a raw binary value. `args` is a dictionary of the arguments parsed from the query string. `environ` is the raw WSGI environment dictionary for the request.
- **Source:** Grinberg PDF p. 41 (`args`, Table 2-2) and p. 42 (`query_string`, `environ`) [printed 19–20].
- **Quote (p. 42):** "query_string — The query string portion of the URL, as a raw binary value." … "environ — The raw WSGI environment dictionary for the request."
- **Quote (p. 41):** "args — A dictionary with all the arguments passed in the query string of the URL."
- Do not make this code+book: these attributes live in Werkzeug, not in the Flask checkout.

### Item 13 (request vs application context): **(a) keep. Pages 39–40 are correct.** Optional upgrade to code+book.

- **Verified:** PDF p. 39 [printed 17] opens "Application and Request Contexts" (motivation). PDF p. 40 [printed 18] holds Table 2-1 and the push/pop statement. Both pages are right, so no correction is needed.
- **Quote (p. 40):** "Flask activates (or pushes) the application and request contexts before dispatching a request to the application, and removes them after the request is handled. When the application context is pushed, the current_app and g variables become available to the thread. Likewise, when the request context is pushed, request and session become available as well."
- **3.1.2 check:** the split is unchanged. `current_app` and `g` are bound to `_cv_app`, and `request` and `session` to `_cv_request` (`src/flask/globals.py` 24–50). `RequestContext.push` pushes an app context first if none is active (`src/flask/ctx.py` 367–378). Wording drift: the book says "thread", while 3.1.2 uses ContextVars. Keep "thread" out of the expected answer.
- **Optional code+book version:** add code spans `src/flask/globals.py` 24–50 and `src/flask/ctx.py` 367–389 (app context pushed before the request context, and the session opened at push).

### Item 14 (OPTIONS handling + safety): **(b) rewrite. The books do not call OPTIONS "safe".**

- **New question:** Grinberg says Flask manages the HEAD and OPTIONS methods automatically. How does Flask 3.1.2 implement automatic OPTIONS handling for a route, and does the view function run for such a request?
- **Short answer:** When a rule is added, `add_url_rule` turns on `provide_automatic_options` and adds `OPTIONS` to the rule's methods, unless the view lists OPTIONS itself, sets the flag, or `PROVIDE_AUTOMATIC_OPTIONS` is false. At dispatch, an OPTIONS request on such a rule returns `make_default_options_response()` without calling the view. That response's `Allow` header lists the URL adapter's allowed methods.
- **Expected claims:** (1) OPTIONS is added automatically when the view did not declare it and the config allows it. (2) The view function is not invoked for automatic OPTIONS. (3) The default response carries an `Allow` header with the allowed methods.
- **Book:** Grinberg PDF p. 41 [printed 19]: "The HEAD and OPTIONS methods are managed automatically by Flask, so in practice it can be said that in this application the three routes in the URL map are attached to the GET method…". Corroborated on PDF p. 223: "…other methods, such as HEAD and OPTIONS, which are automatically implemented by Flask."
- **Code:** `src/flask/sansio/app.py` 635–651; `src/flask/app.py` 893–899 and 953–964.
- **Note:** HEAD is added by Werkzeug's `Rule`, not by Flask code. Keep HEAD out of the expected claims. The `PROVIDE_AUTOMATIC_OPTIONS` config key (`src/flask/app.py` 208) is newer than the book, so the book cannot support that detail. Treat it as code-only within the answer.

### Item 15 (send_file conditional responses): **(b) rewrite around static files. The books never cover send_file, ETag, conditional or range requests.**

- **New question:** Grinberg explains that Flask automatically adds a special route for static files. In Flask 3.1.2, how is that route registered, what function serves the file, and what browser caching does it use by default?
- **Short answer:** If the app has a static folder, the constructor registers `<static_url_path>/<path:filename>` with endpoint `static`, whose view calls `send_static_file`. That method looks up `max_age` via `get_send_file_max_age` and serves the file with `send_from_directory`. `SEND_FILE_MAX_AGE_DEFAULT` defaults to `None`, so browsers use conditional requests instead of a timed cache.
- **Expected claims:** (1) the static route is registered automatically under endpoint `static` when a static folder exists. (2) It is served by `send_static_file` → `send_from_directory`, which keeps paths inside the static folder. (3) The default max age is `None`, so conditional caching is preferred.
- **Book:** Grinberg PDF p. 59 [printed 37]: "Flask automatically supports static files by adding a special route to the application defined as /static/<filename>." The sentence continues on p. 60: "When the server receives a URL that maps to a static route, it generates a response that includes the contents of the corresponding file in the file system."
- **Code:** `src/flask/app.py` 262–279 (route registration), 281–306 (`get_send_file_max_age`), 308–328 (`send_static_file`).
- **Note:** the book writes `/static/<filename>`, and the code uses `<path:filename>`. They agree, because the book also says files can live in subdirectories. The book says nothing about caching, so claim (3) is code-only. The 12-hour default changed to `None` in 2.0, so do not cite older material for it.

### Item 17 (wsgi_app environ + CGI variables): **(b) rewrite around WSGI middleware.**

- **New question:** Grinberg adds WSGI middleware such as ProxyFix by wrapping `app.wsgi_app`. In Flask 3.1.2, why is middleware applied to `wsgi_app` rather than to the app object, and what does `wsgi_app` do with the WSGI environ it receives?
- **Short answer:** `__call__` delegates to `wsgi_app`, so wrapping `app.wsgi_app` applies middleware while keeping the original app object and its methods. `wsgi_app` builds a request context from the `environ` (`request_context(environ)` → `RequestContext`, which builds `app.request_class(environ)`), pushes it, runs `full_dispatch_request`, and always pops the context in `finally`.
- **Expected claims:** (1) middleware wraps `app.wsgi_app`, which keeps a reference to the app object. (2) `environ` is used to create the request context and request object. (3) The context is pushed before dispatch and popped afterwards, even on error.
- **Book:** Grinberg PDF p. 273 [printed 251]: "WSGI middlewares such as ProxyFix are added by wrapping the WSGI application. When a request comes, the middlewares get a chance to inspect the environment and make changes before the request is processed." Also PDF p. 262 [printed 240]: "This command attaches the ProfilerMiddleware from Werkzeug to the application, through its wsgi_app attribute."
- **Code:** `src/flask/app.py` 1479–1527 (docstring 1482–1493, context 1506–1511, pop 1519–1527), 1529–1536 (`__call__`), 1407–1421 (`request_context`); `src/flask/ctx.py` 309–320.
- **Outdated in book:** the `werkzeug.contrib.*` imports. Do not quote the import lines, and do not ask where ProxyFix is imported from.

---

## New Flask code + book questions

### N1. Teardown hooks and unhandled exceptions
- **Question:** Grinberg describes `teardown_request` as a hook that runs after each request even if an unhandled exception occurred. How does Flask 3.1.2 guarantee this, and what do teardown functions receive?
- **Short answer:** `wsgi_app` pops the request context in a `finally` block and passes the caught error. `RequestContext.pop` calls `do_teardown_request(exc)`, which calls the teardown functions of the request's blueprints and then the app's, in reverse registration order, passing each the exception (or `None`). It then sends `request_tearing_down`.
- **Expected claims:** (1) teardown runs from `ctx.pop()` in `wsgi_app`'s `finally`, so it runs on errors too. (2) Each teardown function receives the unhandled exception or `None`. (3) Blueprint teardown functions run before the app's, each list in reverse registration order.
- **Book:** Grinberg PDF p. 42 [printed 20]: "teardown_request — Registers a function to run after each request, even if unhandled exceptions occurred."
- **Code:** `src/flask/app.py` 1506–1527 and 1350–1358; `src/flask/ctx.py` 404–410.
- **Caution:** do not ask about `after_request` or `before_first_request` from this page. Both claims are outdated (see the list at the top).

### N2. Blueprint `errorhandler` vs `app_errorhandler`
- **Question:** Grinberg says a blueprint's `errorhandler` handles only errors from that blueprint's routes, while `app_errorhandler` installs an application-wide handler. How does Flask 3.1.2 implement this distinction and choose a handler?
- **Short answer:** `Blueprint.app_errorhandler` records a deferred call to `app.errorhandler(code)` that runs at registration, so the handler lands in the app's `None` (app-wide) slot. Plain blueprint handlers are merged into `app.error_handler_spec` under the blueprint's name. `_find_error_handler` searches the request's blueprints first, then the app, for the specific code before the exception class hierarchy.
- **Expected claims:** (1) `app_errorhandler` registers on the app when the blueprint is registered. (2) Blueprint-local handlers are stored under the blueprint name and consulted only when the request is handled by that blueprint. (3) Lookup order: blueprint code → app code → blueprint class → app class.
- **Book:** Grinberg PDF p. 113 [printed 91]: "A difference when writing error handlers inside a blueprint is that if the errorhandler decorator is used, the handler will be invoked only for errors that originate in the routes defined by the blueprint. To install application-wide error handlers, the app_errorhandler decorator must be used instead."
- **Code:** `src/flask/sansio/blueprints.py` 596–610 and 388–397; `src/flask/sansio/app.py` 823–846.

### N3. Blueprint `url_prefix`
- **Question:** Grinberg registers the auth blueprint with `url_prefix='/auth'`, so `/login` becomes `/auth/login`. How does Flask 3.1.2 apply the prefix and name the endpoint when a blueprint route is registered?
- **Short answer:** `BlueprintSetupState` takes `url_prefix` from the `register_blueprint` options, falling back to the blueprint's own `url_prefix`. Its `add_url_rule` joins the prefix and rule with a single `/` (or uses the prefix alone for an empty rule) and registers the endpoint as `<blueprint name>.<endpoint>` on the app.
- **Expected claims:** (1) a registration-time `url_prefix` overrides the blueprint's default. (2) Prefix and rule are joined with exactly one slash. (3) Endpoints are namespaced with the blueprint name.
- **Book:** Grinberg PDF p. 128 [printed 106]: "The url_prefix argument in the blueprint registration is optional. When used, all the routes defined in the blueprint will be registered with the given prefix, in this case /auth."
- **Code:** `src/flask/sansio/blueprints.py` 72–77 and 98–116.

### N4. Loading configuration from objects (uppercase rule)
- **Question:** Both cookbooks and Grinberg load settings with `app.config.from_object(...)`. Which attributes does Flask 3.1.2 actually copy into the config, and what argument types does `from_object` accept?
- **Short answer:** `from_object` accepts an import string (resolved with `import_string`) or an object such as a module or class, and copies only attributes whose names are uppercase. A plain `dict` does not work because its keys are not attributes.
- **Expected claims:** (1) only uppercase attribute names are loaded. (2) A string argument is imported first. (3) Class-based configs (base class plus per-environment subclasses) work because inherited uppercase attributes appear in `dir()`.
- **Book:** Aggarwal PDF p. 25 [printed 12]: "Flask is intelligent enough to pick up only those configuration variables that are written in uppercase. This allows us to define any local variables in our configuration files/objects and leave the rest to Flask." Class-based configs: Aggarwal PDF p. 26 and Grinberg PDF p. 111 [printed 89].
- **Code:** `src/flask/config.py` 218–254 (the logic is in 250–254).

### Optional N5. Signed-cookie sessions (only if item 5 should become code+book)
- **Book:** Grinberg PDF p. 74 [printed 52]: "By default, user sessions are stored in client-side cookies that are cryptographically signed using the configured secret key. Any tampering with the cookie content would render the signature invalid, thus invalidating the session."
- **Code:** `src/flask/sessions.py` 317–349 (no secret key → serializer `None`, and `BadSignature` → new empty session).
- This adds a book source to existing item 5. Do not add it as a separate question, because it would duplicate item 5.

---

## Summary table

| Item | Decision | Book pages (physical) | Code span |
|---|---|---|---|
| 8 | drop/move (or replace with 8′ REST methods) | Grinberg 223 | — |
| 9 | drop/move | — | — |
| 11 | drop/move (or replace with 11′ trailing slash) | Grinberg 222 | — |
| 12 | replace with 12′ (request object attributes) | Grinberg 41–42 | — |
| 13 | keep; pages 39–40 verified correct | Grinberg 39–40 | optional `globals.py` 24–50, `ctx.py` 367–389 |
| 14 | rewrite (automatic OPTIONS) | Grinberg 41, 223 | `sansio/app.py` 635–651; `app.py` 893–899, 953–964 |
| 15 | rewrite (static route) | Grinberg 59–60 | `app.py` 262–279, 281–306, 308–328 |
| 17 | rewrite (WSGI middleware + environ) | Grinberg 273, 262 | `app.py` 1479–1527, 1529–1536, 1407–1421; `ctx.py` 309–320 |
| N1 | new: teardown | Grinberg 42 | `app.py` 1506–1527, 1350–1358; `ctx.py` 404–410 |
| N2 | new: blueprint error handlers | Grinberg 113 | `sansio/blueprints.py` 596–610, 388–397; `sansio/app.py` 823–846 |
| N3 | new: url_prefix | Grinberg 128 | `sansio/blueprints.py` 72–77, 98–116 |
| N4 | new: from_object uppercase | Aggarwal 25–26; Grinberg 111 | `config.py` 218–254 |
