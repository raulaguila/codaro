from pathlib import Path

from codaro.chunks import chunks_for


def test_decorators_async_and_multiline_signature():
    source = "@guard\nasync def foo(\n    user,\n    project,\n):\n    return user\n"
    chunk = next(item for item in chunks_for(Path("x.py"), source) if item.symbol == "foo")
    assert chunk.start == 1
    assert chunk.end == 6
    assert chunk.signature == "async def foo( user, project, ):"
    assert "return" not in chunk.signature


def test_nested_qualified_names():
    chunks = chunks_for(
        Path("x.py"), "class A:\n    def method(self):\n        def inner(): pass\n"
    )
    assert {"A", "A.method", "A.method.inner"} <= {item.symbol for item in chunks}


def test_one_line_definition():
    chunk = next(
        item for item in chunks_for(Path("x.py"), "def f(): return 1") if item.symbol == "f"
    )
    assert chunk.signature == "def f():"
    assert chunk.content == "def f(): return 1"
