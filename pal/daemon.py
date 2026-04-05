"""PAL agent daemon — unix socket server.

Accepts connections from CLI clients, manages conversation state per connection,
dispatches chat messages to the inference server, and streams responses back.
"""
import asyncio
import logging
from pathlib import Path

from pal.config import Config
from pal.wiki import WikiManager
from pal.conversation import Conversation
from pal.inference import InferenceClient
from pal.retrieval import RetrievalClient
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
        self.wiki = WikiManager(config.vault_path)
        self.wiki.init_vault()
        self.retrieval = RetrievalClient(
            base_url=config.inference_url,
            collection_id=config.collection_id,
        )

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
                except ValueError as exc:
                    error = ErrorMessage(error=str(exc))
                    writer.write(encode_message(error))
                    await writer.drain()
                    continue

                if isinstance(msg, ChatMessage):
                    await self._handle_chat(msg, conv, writer)
                elif isinstance(msg, CommandMessage):
                    await self._handle_command(msg, conv, writer)
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
        conv: Conversation,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle a slash command."""
        if msg.name in ("quit", "exit"):
            resp = ResponseMessage(text="Goodbye.", command="quit")
            writer.write(encode_message(resp))
            await writer.drain()
        elif msg.name == "status":
            articles = self.wiki.list_articles()
            resp = ResponseMessage(
                text=(
                    f"Model: {self.inference.model}\n"
                    f"Server: {self.inference.base_url}\n"
                    f"Vault: {self.wiki.vault_path} ({len(articles)} articles)"
                ),
                command="status",
            )
            writer.write(encode_message(resp))
            await writer.drain()
        elif msg.name == "read":
            await self._handle_read(msg.args, writer)
        elif msg.name == "lint":
            await self._handle_lint(writer)
        elif msg.name == "note":
            await self._handle_note(msg.args, writer)
        elif msg.name == "search":
            await self._handle_search(msg.args, writer)
        elif msg.name == "get":
            await self._handle_get(msg.args, writer)
        else:
            error = ErrorMessage(error=f"Unknown command: /{msg.name}")
            writer.write(encode_message(error))
            await writer.drain()

    async def _handle_read(self, path: str, writer: asyncio.StreamWriter) -> None:
        """Handle /read <path> — return article content."""
        path = path.strip()
        if not path:
            error = ErrorMessage(error="Usage: /read <path>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            meta, body = self.wiki.read_article(path)
            title = meta.get("title", path)
            tags = meta.get("tags", [])
            header = f"**{title}**"
            if tags:
                header += f"  tags: {', '.join(tags)}"
            resp = ResponseMessage(text=f"{header}\n\n{body}", command="read")
            writer.write(encode_message(resp))
            await writer.drain()
        except FileNotFoundError:
            error = ErrorMessage(error=f"Article not found: {path}")
            writer.write(encode_message(error))
            await writer.drain()

    async def _handle_lint(self, writer: asyncio.StreamWriter) -> None:
        """Handle /lint — run vault health check."""
        issues = self.wiki.lint()
        if not issues:
            resp = ResponseMessage(text="Vault is clean — no issues found.", command="lint")
        else:
            lines = [f"Found {len(issues)} issue(s):\n"]
            for issue in issues:
                lines.append(f"- **{issue['path']}**: {issue['issue']}")
            resp = ResponseMessage(text="\n".join(lines), command="lint")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_note(
        self,
        topic: str,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle /note <topic> — create or update a wiki article."""
        topic = topic.strip()
        if not topic:
            error = ErrorMessage(error="Usage: /note <topic>")
            writer.write(encode_message(error))
            await writer.drain()
            return

        prompt = (
            f"Write a concise wiki article about: {topic}\n\n"
            "Format: Start with a markdown heading, then clear explanatory paragraphs. "
            "Be informative and concise."
        )

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            body = await self.inference.complete(messages)
        except Exception as exc:
            logger.exception("Inference error during /note: %s", exc)
            error = ErrorMessage(error=f"Inference error: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        slug = topic.lower().replace(" ", "-")
        slug = "".join(c for c in slug if c.isalnum() or c == "-")
        slug = slug.strip("-")
        if not slug:
            slug = "untitled"
        path = f"{slug}.md"

        self.wiki.write_article(path=path, title=topic, body=body + "\n")
        self.wiki.rebuild_index()
        self.wiki.git_init()
        self.wiki.git_commit(f"note: {topic}")

        resp = ResponseMessage(
            text=f"Created article: {path}\n\n{body}",
            command="note",
        )
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_search(self, query: str, writer: asyncio.StreamWriter) -> None:
        """Handle /search <query> — semantic search over the vault collection."""
        query = query.strip()
        if not query:
            error = ErrorMessage(error="Usage: /search <query>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            results = await self.retrieval.search(query, limit=5)
        except Exception as exc:
            logger.exception("Search failed: %s", exc)
            error = ErrorMessage(error=f"Search failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        if not results:
            resp = ResponseMessage(text="No results found.", command="search")
        else:
            lines = [f"Found {len(results)} result(s):\n"]
            for r in results:
                score = r.get("score", 0.0)
                summary = r.get("summary", "")
                lines.append(f"- **{r['id']}** (score: {score:.2f})")
                if summary:
                    lines.append(f"  {summary}")
            resp = ResponseMessage(text="\n".join(lines), command="search")
        writer.write(encode_message(resp))
        await writer.drain()

    async def _handle_get(self, doc_id: str, writer: asyncio.StreamWriter) -> None:
        """Handle /get <doc_id> — fetch full document content."""
        doc_id = doc_id.strip()
        if not doc_id:
            error = ErrorMessage(error="Usage: /get <doc_id>")
            writer.write(encode_message(error))
            await writer.drain()
            return
        try:
            doc = await self.retrieval.get_document(doc_id)
        except FileNotFoundError:
            error = ErrorMessage(error=f"Document not found: {doc_id}")
            writer.write(encode_message(error))
            await writer.drain()
            return
        except Exception as exc:
            logger.exception("Get document failed: %s", exc)
            error = ErrorMessage(error=f"Get failed: {exc}")
            writer.write(encode_message(error))
            await writer.drain()
            return

        content = doc.get("content", "")
        name = doc.get("name", doc_id)
        resp = ResponseMessage(
            text=f"**{name}** ({doc_id})\n\n{content}",
            command="get",
        )
        writer.write(encode_message(resp))
        await writer.drain()
