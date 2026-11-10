"""A5: a daemon disconnect mid-command must not kill the REPL."""
from contextlib import nullcontext

import pytest

import pal.cli as cli


class _FakeConfig:
    socket_path = "/tmp/pal-test-socket-not-real"


class _FakeClient:
    async def connect(self):
        pass

    async def close(self):
        pass


class _FakeConsole:
    def __init__(self, *args, **kwargs):
        self.printed: list[str] = []

    def print(self, *args, **kwargs):
        self.printed.append(" ".join(str(a) for a in args))


@pytest.mark.asyncio
async def test_run_command_raises_connection_error_on_eof():
    """_run_command signals a vanished daemon by raising ConnectionError
    when the stream ends without a ResponseMessage. The run_repl catch
    relies on this being a ConnectionError, not something else."""

    class EofClient:
        async def command_stream(self, name, args, channel_id=None):
            return
            yield  # makes this an async generator that yields nothing

    with pytest.raises(ConnectionError, match="Connection closed"):
        await cli._run_command(
            EofClient(), "import", "raw/x.pdf", _FakeConsole(),
        )


@pytest.mark.asyncio
async def test_run_repl_renders_connection_error_and_keeps_loop(monkeypatch):
    """A5: a ConnectionError from a slash command (daemon restarted
    mid-command) is rendered in-REPL and the loop continues to the next
    prompt; it must not escape run_repl as a traceback."""
    inputs = ["/import raw/book.pdf"]  # then EOF ends the loop

    class _FakeSession:
        def __init__(self, *args, **kwargs):
            self._inputs = list(inputs)

        async def prompt_async(self, _prompt):
            if not self._inputs:
                raise EOFError
            return self._inputs.pop(0)

    consoles: list[_FakeConsole] = []

    def _console_factory(*args, **kwargs):
        c = _FakeConsole()
        consoles.append(c)
        return c

    async def _boom(client, name, args, console):
        raise ConnectionError("Connection closed")

    monkeypatch.setattr(cli, "load_config", lambda: _FakeConfig())
    monkeypatch.setattr(cli, "PalClient", lambda path: _FakeClient())
    monkeypatch.setattr(cli, "PromptSession", lambda: _FakeSession())
    monkeypatch.setattr(cli, "Console", _console_factory)
    monkeypatch.setattr(cli, "patch_stdout", lambda: nullcontext())
    monkeypatch.setattr(cli, "_run_command", _boom)

    await cli.run_repl()  # must return cleanly, not raise

    all_printed = "\n".join(line for c in consoles for line in c.printed)
    assert "Connection closed" in all_printed
