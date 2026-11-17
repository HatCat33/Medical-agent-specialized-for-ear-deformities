"""Signature verification and AES decryption for WeChat Customer Service callbacks."""

import base64
import hashlib
import hmac
import struct
from dataclasses import dataclass
from xml.etree import ElementTree

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


class WeChatKfCryptoError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class CallbackEvent:
    corp_id: str
    event: str
    token: str
    open_kfid: str


class WeChatKfCallbackCrypto:
    def __init__(self, *, token: str, encoding_aes_key: str, corp_id: str) -> None:
        self._token = token.strip()
        self._corp_id = corp_id.strip()
        normalized_key = encoding_aes_key.strip()
        normalized_key += "=" * (-len(normalized_key) % 4)
        try:
            self._aes_key = base64.b64decode(normalized_key, validate=True)
        except (ValueError, TypeError) as exc:
            raise WeChatKfCryptoError("wechat_kf_callback_aes_key_invalid") from exc
        if len(self._aes_key) != 32 or not self._token or not self._corp_id:
            raise WeChatKfCryptoError("wechat_kf_callback_crypto_config_invalid")

    def verify_signature(
        self,
        *,
        signature: str,
        timestamp: str,
        nonce: str,
        encrypted: str,
    ) -> None:
        values = sorted((self._token, timestamp, nonce, encrypted))
        expected = hashlib.sha1("".join(values).encode()).hexdigest()
        if not hmac.compare_digest(expected, signature):
            raise WeChatKfCryptoError("wechat_kf_callback_signature_invalid")

    def decrypt(self, encrypted: str) -> str:
        try:
            ciphertext = base64.b64decode(encrypted, validate=True)
        except (ValueError, TypeError) as exc:
            raise WeChatKfCryptoError("wechat_kf_callback_payload_invalid") from exc
        if not ciphertext or len(ciphertext) % 16:
            raise WeChatKfCryptoError("wechat_kf_callback_payload_invalid")

        decryptor = Cipher(
            algorithms.AES(self._aes_key),
            modes.CBC(self._aes_key[:16]),
        ).decryptor()
        padded = decryptor.update(ciphertext) + decryptor.finalize()
        if not padded:
            raise WeChatKfCryptoError("wechat_kf_callback_payload_invalid")
        padding = padded[-1]
        if padding < 1 or padding > 32 or padded[-padding:] != bytes([padding]) * padding:
            raise WeChatKfCryptoError("wechat_kf_callback_padding_invalid")
        plaintext = padded[:-padding]
        if len(plaintext) < 20:
            raise WeChatKfCryptoError("wechat_kf_callback_payload_invalid")

        message_length = struct.unpack("!I", plaintext[16:20])[0]
        message_end = 20 + message_length
        if message_end > len(plaintext):
            raise WeChatKfCryptoError("wechat_kf_callback_payload_invalid")
        receiver = plaintext[message_end:].decode("utf-8", errors="strict")
        if receiver != self._corp_id:
            raise WeChatKfCryptoError("wechat_kf_callback_receiver_invalid")
        try:
            return plaintext[20:message_end].decode("utf-8")
        except UnicodeDecodeError as exc:
            raise WeChatKfCryptoError("wechat_kf_callback_payload_invalid") from exc

    def verify_and_decrypt(
        self,
        *,
        signature: str,
        timestamp: str,
        nonce: str,
        encrypted: str,
    ) -> str:
        self.verify_signature(
            signature=signature,
            timestamp=timestamp,
            nonce=nonce,
            encrypted=encrypted,
        )
        return self.decrypt(encrypted)


def xml_value(xml_text: str, name: str) -> str:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise WeChatKfCryptoError("wechat_kf_callback_xml_invalid") from exc
    return (root.findtext(name) or "").strip()


def encrypted_value(xml_text: str) -> str:
    value = xml_value(xml_text, "Encrypt")
    if not value:
        raise WeChatKfCryptoError("wechat_kf_callback_encrypt_missing")
    return value


def parse_callback_event(xml_text: str) -> CallbackEvent:
    try:
        root = ElementTree.fromstring(xml_text)
    except ElementTree.ParseError as exc:
        raise WeChatKfCryptoError("wechat_kf_callback_xml_invalid") from exc
    event = CallbackEvent(
        corp_id=(root.findtext("ToUserName") or "").strip(),
        event=(root.findtext("Event") or "").strip(),
        token=(root.findtext("Token") or "").strip(),
        open_kfid=(root.findtext("OpenKfId") or "").strip(),
    )
    if not all((event.corp_id, event.event, event.token, event.open_kfid)):
        raise WeChatKfCryptoError("wechat_kf_callback_event_invalid")
    return event
