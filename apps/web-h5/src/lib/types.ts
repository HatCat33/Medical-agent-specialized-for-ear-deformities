export interface Conversation {
  id: string;
  title: string;
  status: string;
  created_at: string;
  updated_at: string;
  last_message_at: string | null;
}

export interface MediaAttachment {
  id: string;
  kind: "image" | "audio";
  original_name: string;
  media_type: string;
  size_bytes: number;
  status: string;
  created_at: string;
}

export interface SelectedImage {
  id: string;
  file: File;
  previewUrl: string;
}

export interface Message {
  id: string;
  conversation_id: string;
  sequence_no: number;
  role: "user" | "assistant";
  content: string;
  status: string;
  safety_code: string | null;
  created_at: string;
  attachments: MediaAttachment[];
}

export interface ConversationDetail extends Conversation {
  messages: Message[];
}

export interface SendMessageResponse {
  conversation: Conversation;
  user_message: Message;
  assistant_message: Message;
}

export interface StreamMessageHandlers {
  onUserMessage: (message: Message) => void;
  onDelta: (content: string) => void;
  onDone: (message: Message) => void;
}

export interface TranscriptionResult {
  text: string;
}

export type InputMode = "text" | "voice" | "multimodal";
