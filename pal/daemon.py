"""PAL agent daemon — unix socket server.

Accepts connections from CLI clients, manages conversation state per connection,
dispatches chat messages to the inference server, and streams responses back.
"""
import asyncio
import logging
from pathlib import Path

from pal.config import Config
from pal.conversation import Conversation
from pal.inference import InferenceClient
from pal.protocol import (
    ChatMessage,
    CommandMessage,
    StreamChunkMessage,
    ResponseMessage,
    ErrorMessage,
    Message,
    encode_message,
    decode_message,
)

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = (
    "You are PAL, a personal AI librarian and conversational companion. "
    "You help the user think, answer questions, and manage knowledge. "
    "Be concise, direct, and helpful."
)


class Daemon:
    def __init__(self, config: Config) -> None:
        self.config = config
        self.inference = InferenceClient(
            base_url=config.inference_url,
            model=config.model,
        )
        self._server: asyncio.AbstractServer | None = None
        self._should_exit = False

    async def serve(self) -> None:
        """Start listening on the unix socket."""
        sock_path = self.config.socket_path
        # Clean up stale socket
        if sock_path.exists():
            sock_path.unlink()
        sock_path.parent.mkdir(parents=True, exist_ok=True)

        self._server = await asyncio.start_unix_server(
            self._handle_connection,
            path=str(sock_path),
        )
        logger.info("Daemon listening on %s", sock_path)

        async with self._server:
            while not self._should_exit:
                await asyncio.sleep(0.1)

    def shutdown(self) -> None:
        """Signal the daemon to stop."""
        self._should_exit = True
        if self._server:
            self._server.close()

    async def _handle_connection(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a single client connection."""
        conv = Conversation(history_depth=self.config.history_depth)
        logger.info("Client connected")

        try:
            while True:
                line = await reader.readline()
                if not line:
                    break

                try:
                    msg = decode_message(line.strip())
                except (ValueError, Exception) as exc:
                    error = ErrorMessage(error=str(exc))
                    writer.write(encode_message(error))
                    await writer.drain()
                    continue

                if isinstance(msg, ChatMessage):
                    await self._handle_chat(msg, conv, writer)
                elif isinstance(msg, CommandMessage):
                    await self._handle_command(msg, writer)
                else:
                    error = ErrorMessage(error=f"Unexpected message type: {msg.type}")
                    writer.write(encode_message(error))
                    await writer.drain()
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Connection error: %s", exc)
        finally:
            writer.close()
            await writer.wait_closed()
            logger.info("Client disconnected")

    async def _handle_chat(
        self,
        msg: ChatMessage,
        conv: Conversation,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Process a chat message: add to history, stream inference, send response."""
        conv.add_user(msg.text)
        messages = conv.get_messages_for_api(system_prompt=SYSTEM_PROMPT)

        full_response = []
        try:
            async for token in self.inference.stream(messages):
                chunk = StreamChunkMessage(token=token)
                writer.write(encode_message(chunk))
                await writer.drain()
                full_response.append(token)
        except Exception as exc:
            logger.exception("Inference error: %s", exc)
            error = ErrorMessage(error=f"Inference error: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        response_text = "".join(full_response)
        conv.add_assistant(response_text)

        done = ResponseMessage(text=response_text)
        writer.write(encode_message(done))
        await writer.drain()

    async def _handle_command(
        self,
        msg: CommandMessage,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a slash command. Phase 1 only supports /quit and /status."""
        if msg.name == "quit":
            resp = ResponseMessage(text="Goodbye.", command="quit")
            writer.write(encode_message(resp))
            await writer.drain()
        elif msg.name == "status":
            resp = ResponseMessage(
                text=f"Model: {self.inference.model}\nServer: {self.inference.base_url}",
                command="status",
            )
            writer.write(encode_message(resp))
            await writer.drain()
        else:
            error = ErrorMessage(error=f"Unknown command: /{msg.name}")
            writer.write(encode_message(error))
            await writer.drain()
