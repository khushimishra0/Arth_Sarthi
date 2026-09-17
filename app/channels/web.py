"""The web channel. Renders the same `OutboundMessage` the bot renders.

This file is where the Phase 2 architectural decision pays for itself. Every feature
already returns `{blocks[], images[], keyboard}` and no feature knows what a `chat_id`
is — so the dashboard needs a renderer and nothing else. There is no second scam
scorer, no second finance engine, no duplicated copy. §9: *"Same feature engines as
Telegram — zero duplicated logic."*

**Escaping.** Every block goes through Jinja2 with autoescape on. That is not a
formality here: scam detection quotes the suspicious message back to the user, and a
scam message is *precisely* the kind of text that contains `<script>`. The Telegram
renderer escapes to HTML entities for the same reason. There is a test that feeds a
hostile screenshot through this path and asserts nothing executable survives.
"""

from __future__ import annotations

import base64
from dataclasses import dataclass

from app.channels.base import (
    Button,
    ChannelAdapter,
    OutboundImage,
    OutboundMessage,
)
from app.models.enums import Channel

__all__ = ["RenderedBlock", "RenderedImage", "WebAdapter", "render_blocks", "render_images"]


@dataclass(frozen=True, slots=True)
class RenderedBlock:
    """A block flattened into what a template needs, and nothing more.

    The template does not branch on `BlockKind` — it iterates and switches on `kind`
    once. Keeping the shape flat means the HTML stays readable and the escaping stays
    the template engine's job rather than being hand-rolled here.
    """

    kind: str
    text: str = ""
    items: tuple[str, ...] = ()
    pairs: tuple[tuple[str, str], ...] = ()


@dataclass(frozen=True, slots=True)
class RenderedImage:
    """A chart, inlined as a data URI.

    Inline rather than a served file because the alternative is writing user-derived
    images to disk or holding them in a cache keyed by something guessable. §10 says
    uploads never touch disk; a chart derived from someone's income is the same class
    of data, so it goes straight into the page and dies with the response.
    """

    data_uri: str
    caption: str = ""

    @classmethod
    def from_image(cls, image: OutboundImage) -> RenderedImage:
        encoded = base64.b64encode(image.png).decode("ascii")
        return cls(data_uri=f"data:image/png;base64,{encoded}", caption=image.caption)


def render_blocks(message: OutboundMessage) -> tuple[RenderedBlock, ...]:
    """Flatten an outbound message for the template. No HTML is produced here."""
    rendered: list[RenderedBlock] = []
    for block in message.blocks:
        rendered.append(
            RenderedBlock(
                kind=block.kind.value,
                text=block.text,
                items=block.items,
                pairs=block.pairs,
            )
        )
    return tuple(rendered)


def render_images(message: OutboundMessage) -> tuple[RenderedImage, ...]:
    return tuple(RenderedImage.from_image(image) for image in message.images)


def render_links(message: OutboundMessage) -> tuple[Button, ...]:
    """Only the URL buttons.

    Callback buttons are a Telegram conversation mechanism — "tap to answer question
    3 of 6". On the web the same job is done by a form, so rendering them as dead
    links would give the user something to click that does nothing.
    """
    if message.keyboard is None:
        return ()
    return tuple(button for button in message.keyboard.buttons if button.url)


class WebAdapter(ChannelAdapter):
    """Implements the channel contract. `send` is a no-op — the browser pulls."""

    channel = Channel.WEB

    def render(self, message: OutboundMessage) -> dict:
        return {
            "blocks": render_blocks(message),
            "images": render_images(message),
            "links": render_links(message),
        }

    async def send(self, user_id: str, message: OutboundMessage) -> None:
        """Not applicable: HTTP is request/response, so there is nothing to push."""
        raise NotImplementedError("the web channel returns responses, it does not send")


def plain_text(message: OutboundMessage) -> str:
    """The message as text, for the "copy this" affordance and for tests."""
    from app.channels.base import render_plain

    return render_plain(message)
