"""Channel-neutral verified event, inbound message, and outbound message types."""

from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True)
class EncryptedRemoteImage:
    url: str
    aes_key: str
    filename: str


@dataclass(frozen=True)
class InboundMessage:
    channel: Literal["wecom", "feishu", "wechat_kf"]
    event_id: str
    request_id: str
    bot_id: str
    user_id: str
    chat_id: str
    chat_type: Literal["single", "group"]
    content: str
    input_mode: Literal["text", "voice", "multimodal"]
    images: tuple[EncryptedRemoteImage, ...] = ()

