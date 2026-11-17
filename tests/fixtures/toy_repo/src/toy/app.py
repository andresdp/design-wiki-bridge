"""A tiny application object."""

import logging

LOG = logging.getLogger(__name__)
DEFAULT_PORT = 8000


class App:
    """Application with a request handler table."""

    def __init__(self, name):
        self.name = name
        self.handlers = {}

    def route(self, path):
        def decorator(fn):
            self.handlers[path] = fn
            return fn

        return decorator

    def run(self, port=DEFAULT_PORT):
        LOG.info("running %s on %s", self.name, port)
        return port


def make_app(name="toy"):
    return App(name)


def helper(x):
    return x * 2
