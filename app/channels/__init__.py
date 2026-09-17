"""Transports. Features return channel-neutral messages; these turn them into pixels.

`base` is the contract every channel implements. Nothing outside `telegram_bot`
may import `telegram`, and nothing outside a channel may build channel markup.
"""

from app.channels.base import (
    Attachment,
    AttachmentKind,
    Block,
    BlockKind,
    Button,
    ChannelAdapter,
    InboundMessage,
    Intent,
    Keyboard,
    OutboundImage,
    OutboundMessage,
    classify,
    render_plain,
)

__all__ = [
    "Attachment",
    "AttachmentKind",
    "Block",
    "BlockKind",
    "Button",
    "ChannelAdapter",
    "InboundMessage",
    "Intent",
    "Keyboard",
    "OutboundImage",
    "OutboundMessage",
    "classify",
    "render_plain",
]
