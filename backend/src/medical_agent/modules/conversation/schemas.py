"""Conversation API contracts."""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from medical_agent.modules.media.schemas import MediaRead


class ConversationCreate(BaseModel):
    title: str | None = Field(default=None, max_length=120)


class ConversationRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    status: str
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class MessageRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    conversation_id: str
    sequence_no: int
    role: str
    content: str
    status: str
    safety_code: str | None
    created_at: datetime
    attachments: list[MediaRead] = Field(default_factory=lambda: list[MediaRead]())


class ConversationDetail(ConversationRead):
    messages: list[MessageRead]


class SendMessageRequest(BaseModel):
    content: str = Field(default="", max_length=4000)
    attachment_ids: list[str] = Field(default_factory=list, max_length=4)
    input_mode: Literal["text", "voice", "multimodal"] = "text"

    @model_validator(mode="after")
    def validate_content_or_attachments(self) -> "SendMessageRequest":
        if not self.content.strip() and not self.attachment_ids:
            raise ValueError("content_or_attachment_required")
        if len(set(self.attachment_ids)) != len(self.attachment_ids):
            raise ValueError("duplicate_attachment_ids")
        return self


class SendMessageResponse(BaseModel):
    conversation: ConversationRead
    user_message: MessageRead
    assistant_message: MessageRead

