from toy.app import App


def test_run_returns_port():
    assert App("t").run(1) == 1
