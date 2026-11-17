"""WSGI entry point."""

from toy.app import make_app


def application(environ, start_response):
    app = make_app()
    start_response("200 OK", [("Content-Type", "text/plain")])
    return [app.name.encode()]


class Router:
    def run(self):
        return "router"
