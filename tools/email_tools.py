"""Zoho Mail tool implementations."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import app.util as util
import json
import os
from typing import Any

import requests

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("[EmailTools] python-dotenv is not installed, skipping .env loading")

from tools.base import ToolDefinition, ToolExecutionResult


ACCOUNTS_BASE_URL = "https://accounts.zoho.com"
MAIL_API_BASE_URL = "https://mail.zoho.com/api"
PAGE_SIZE = 200
FETCH_RECENT_EMAILS_PARAMETERS:dict[str, Any] = {
    "type": "object",
    "properties": {},
    # "required": [],
    "additionalProperties": False
}


class AccessTokenManager:
    """Resolve and refresh the Zoho message access token when needed.

    Args:
        None

    Returns:
        None
    """

    def __init__(self):
        self.client_id = os.getenv("ZOHO_ID", "").strip()
        self.client_secret = os.getenv("ZOHO_TOKEN", "").strip()
        self.access_token = os.getenv("ZOHO_MAIL_ACCESS", "").strip()
        self.refresh_token = os.getenv("ZOHO_MAIL_REFRESH", "").strip()
        self.grant_token = os.getenv("ZOHO_MAIL_GRANT", "").strip()

    def get_token(self) -> str:
        """Return the current access token, resolving it if needed.

        Args:
            None

        Returns:
            str: Access token for the message API.
        """

        if self.access_token:
            return self.access_token

        if self.refresh_token:
            print("[EmailTools] Access token missing, refreshing from ZOHO_MAIL_REFRESH")
            self.refresh_or_raise(reason = "No access token was configured.")
            return self.access_token

        if self.grant_token:
            print("[EmailTools] Access token missing, exchanging ZOHO_MAIL_GRANT")
            token_payload = exchange_grant_code(
                client_id = self.client_id,
                client_secret = self.client_secret,
                grant_code = self.grant_token
            )
            self.access_token = str(token_payload.get("access_token", "")).strip()
            new_refresh_token = str(token_payload.get("refresh_token", "")).strip()
            if new_refresh_token:
                self.refresh_token = new_refresh_token
                print("[EmailTools] New refresh token received from grant exchange")
            if not self.access_token:
                raise RuntimeError(
                    f"Zoho grant exchange did not return an access token: {token_payload}"
                )
            return self.access_token

        raise RuntimeError(
            "Missing message credentials. Set one of ZOHO_MAIL_ACCESS, "
            "ZOHO_MAIL_REFRESH, or ZOHO_MAIL_GRANT."
        )

    def refresh_or_raise(self, reason:str) -> str:
        """Refresh the access token or raise a detailed exception.

        Args:
            reason: Context for why the refresh is being attempted.

        Returns:
            str: Newly refreshed access token.
        """

        if not self.refresh_token:
            raise RuntimeError(
                f"{reason} Automatic token refresh is unavailable because "
                "ZOHO_MAIL_REFRESH is not set."
            )

        if not self.client_id:
            raise RuntimeError(f"{reason} Missing ZOHO_ID environment variable.")
        if not self.client_secret:
            raise RuntimeError(f"{reason} Missing ZOHO_TOKEN environment variable.")

        try:
            token_payload = refresh_access_token(
                client_id = self.client_id,
                client_secret = self.client_secret,
                refresh_token = self.refresh_token
            )
        except Exception as exc:
            raise RuntimeError(
                f"{reason} Refreshing the access token with ZOHO_MAIL_REFRESH failed: {exc}"
            ) from exc

        access_token = str(token_payload.get("access_token", "")).strip()
        if not access_token:
            raise RuntimeError(
                f"{reason} Zoho refresh-token response did not include an access token: "
                f"{json.dumps(token_payload, indent = 2)}"
            )

        self.access_token = access_token
        print("[EmailTools] Successfully refreshed access token after authorization failure")
        return self.access_token


def build_email_tool_definitions() -> list[ToolDefinition]:
    """Build email-oriented tool definitions.

    Args:
        None

    Returns:
        list[ToolDefinition]: Email tool definitions.
    """

    def fetch_recent_emails(arguments:dict[str, Any]) -> ToolExecutionResult:
        print(f"[EmailTools] Running fetch_recent_emails with args: {arguments}")
        sender_content_pairs = fetch_sender_content_tuples()
        output = {
            "messageCount": len(sender_content_pairs),
            "messages": [
                {
                    "sender": sender,
                    "content": content
                }
                for sender, content in sender_content_pairs
            ]
        }
        return ToolExecutionResult(output = json.dumps(output, indent = 2))

    return [
        ToolDefinition(
            name = "fetch_recent_emails",
            description = (
                "Fetch recent emails and return a list of sender plus cleaned plain-text content."
            ),
            parameters = FETCH_RECENT_EMAILS_PARAMETERS,
            handler = fetch_recent_emails
        )
    ]


def post_form(url:str, data:dict[str, str]) -> dict[str, Any]:
    """Send a form-encoded POST request and return the JSON response.

    Args:
        url: Target API URL.
        data: Form payload.

    Returns:
        dict[str, Any]: Parsed JSON response body.
    """

    print(f"[EmailTools] POST {url}")
    response = requests.post(
        url = url,
        data = data,
        timeout = 30
    )

    try:
        payload:dict[str, Any] = response.json()
    except ValueError:
        payload = {"raw_text": response.text}

    print(f"[EmailTools] POST status={response.status_code}")
    print(f"[EmailTools] POST payload={json.dumps(payload, indent = 2)}")
    response.raise_for_status()

    if "error" in payload:
        raise RuntimeError(f"Zoho OAuth error: {payload}")

    return payload


def get_json(
    url:str,
    headers:dict[str, str],
    params:dict[str, str] | None = None
) -> dict[str, Any]:
    """Send a GET request and return the JSON response.

    Args:
        url: Target API URL.
        headers: Request headers.
        params: Optional query parameters.

    Returns:
        dict[str, Any]: Parsed JSON response body.
    """

    print(f"[EmailTools] GET {url} params={params}")
    response = requests.get(
        url = url,
        headers = headers,
        params = params,
        timeout = 30
    )

    try:
        payload:dict[str, Any] = response.json()
    except ValueError:
        payload = {"raw_text": response.text}

    print(f"[EmailTools] GET status={response.status_code}")

    if "error" in payload:
        raise RuntimeError(f"Zoho Mail API error: {payload}")

    status = payload.get("status", {})
    code = status.get("code")
    if response.status_code >= 400:
        raise requests.HTTPError(
            f"HTTP {response.status_code}: {json.dumps(payload, indent = 2)}",
            response = response
        )
    if code not in (None, 200):
        description = status.get("description", "Unknown API error")
        raise RuntimeError(f"Zoho Mail API error {code}: {description}")

    return payload


def exchange_grant_code(
    client_id:str,
    client_secret:str,
    grant_code:str
) -> dict[str, Any]:
    """Exchange a one-time grant code for tokens.

    Args:
        client_id: Zoho OAuth client ID.
        client_secret: Zoho OAuth client secret.
        grant_code: One-time Zoho self-client grant code.

    Returns:
        dict[str, Any]: Token response from Zoho.
    """

    return post_form(
        url = f"{ACCOUNTS_BASE_URL}/oauth/v2/token",
        data = {
            "grant_type": "authorization_code",
            "client_id": client_id,
            "client_secret": client_secret,
            "code": grant_code
        }
    )


def refresh_access_token(
    client_id:str,
    client_secret:str,
    refresh_token:str
) -> dict[str, Any]:
    """Refresh an access token using a refresh token.

    Args:
        client_id: Zoho OAuth client ID.
        client_secret: Zoho OAuth client secret.
        refresh_token: Zoho OAuth refresh token.

    Returns:
        dict[str, Any]: Token response from Zoho.
    """

    return post_form(
        url = f"{ACCOUNTS_BASE_URL}/oauth/v2/token",
        data = {
            "refresh_token": refresh_token,
            "client_id": client_id,
            "client_secret": client_secret,
            "grant_type": "refresh_token"
        }
    )


def build_headers(access_token:str) -> dict[str, str]:
    """Build Zoho Mail request headers.

    Args:
        access_token: Zoho OAuth access token.

    Returns:
        dict[str, str]: Request headers.
    """

    return {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Authorization": f"Zoho-oauthtoken {access_token}"
    }


def parse_sender_whitelist() -> set[str]:
    """Parse the configured sender whitelist from environment variables.

    Args:
        None

    Returns:
        set[str]: Lowercased sender addresses to keep.
    """

    raw_whitelist = os.getenv("ZOHO_SENDER_WHITELIST", "").strip()
    whitelist:set[str] = set()

    for item in raw_whitelist.split(","):
        normalized_item = item.strip().lower()
        if normalized_item:
            whitelist.add(normalized_item)

    print(f"[EmailTools] Loaded {len(whitelist)} whitelisted sender address(es)")
    return whitelist


def extract_sender_address(message:dict[str, Any]) -> str:
    """Extract the sender email address from a message payload.

    Args:
        message: Raw Zoho message object.

    Returns:
        str: Lowercased sender address, if present.
    """

    from_address = str(message.get("fromAddress", "")).strip()
    if from_address:
        return from_address.lower()

    sender = str(message.get("sender", "")).strip()
    if "<" in sender and ">" in sender:
        return sender.split("<", 1)[1].split(">", 1)[0].strip().lower()

    return sender.lower()

def perform_get_with_auto_refresh(
    url:str,
    params:dict[str, str] | None,
    token_manager:AccessTokenManager
) -> dict[str, Any]:
    """Run a GET request and automatically refresh the token on 401 once.

    Args:
        url: Target API URL.
        params: Optional query parameters.
        token_manager: Access token manager.

    Returns:
        dict[str, Any]: Parsed successful response payload.
    """

    try:
        return get_json(
            url = url,
            headers = build_headers(access_token = token_manager.get_token()),
            params = params
        )
    except requests.HTTPError as exc:
        status_code = exc.response.status_code if exc.response is not None else None
        if status_code != 401:
            raise

        print("[EmailTools] Received 401, attempting to refresh the access token")
        token_manager.refresh_or_raise(reason = "Zoho returned 401 for a message request.")

        try:
            return get_json(
                url = url,
                headers = build_headers(access_token = token_manager.get_token()),
                params = params
            )
        except Exception as retry_exc:
            raise RuntimeError(
                "Zoho returned 401 for a message request, the automatic refresh succeeded or "
                f"was attempted, but retrying the request still failed: {retry_exc}"
            ) from retry_exc


def get_mail_messages_page(
    account_id:str,
    folder_id:str,
    start:int,
    token_manager:AccessTokenManager
) -> list[dict[str, Any]]:
    """Fetch one page of messages for a folder.

    Args:
        account_id: Zoho Mail account ID.
        folder_id: Zoho Mail folder ID.
        start: 1-based pagination offset.
        token_manager: Access token manager.

    Returns:
        list[dict[str, Any]]: Raw message objects.
    """

    payload = perform_get_with_auto_refresh(
        url = f"{MAIL_API_BASE_URL}/accounts/{account_id}/messages/view",
        params = {
            "folderId": folder_id,
            "start": str(start),
            "limit": str(PAGE_SIZE),
            "sortBy": "date",
            "sortorder": "false",
            "includeto": "true"
        },
        token_manager = token_manager
    )
    return payload.get("data", [])


def get_message_content(
    account_id:str,
    folder_id:str,
    message_id:str,
    token_manager:AccessTokenManager
) -> str:
    """Fetch the full content for a single message.

    Args:
        account_id: Zoho Mail account ID.
        folder_id: Zoho Mail folder ID.
        message_id: Zoho Mail message ID.
        token_manager: Access token manager.

    Returns:
        str: Message content.
    """

    payload = perform_get_with_auto_refresh(
        url = (
            f"{MAIL_API_BASE_URL}/accounts/{account_id}/folders/{folder_id}/messages/"
            f"{message_id}/content"
        ),
        params = {"includeBlockContent": "true"},
        token_manager = token_manager
    )
    data = payload.get("data", {})
    return str(data.get("content", ""))


def fetch_recent_messages(
    account_id:str,
    folder_id:str,
    token_manager:AccessTokenManager
) -> list[dict[str, Any]]:
    """Fetch messages from the past 24 hours in descending date order.

    Args:
        account_id: Zoho Mail account ID.
        folder_id: Zoho Mail folder ID.
        token_manager: Access token manager.

    Returns:
        list[dict[str, Any]]: Message objects from the last 24 hours.
    """

    cutoff = datetime.now(timezone.utc) - timedelta(hours = 24)
    cutoff_ms = int(cutoff.timestamp() * 1000)
    start = 1
    recent_messages:list[dict[str, Any]] = []

    print(f"[EmailTools] Fetching messages newer than {cutoff.isoformat()}")

    while True:
        messages = get_mail_messages_page(
            account_id = account_id,
            folder_id = folder_id,
            start = start,
            token_manager = token_manager
        )
        print(
            f"[EmailTools] Folder {folder_id} returned {len(messages)} message(s) "
            f"starting at {start}"
        )

        if not messages:
            break

        reached_old_message = False
        for message in messages:
            received_time = int(
                message.get("receivedTime", message.get("receivedtime", 0)) or 0
            )
            if received_time < cutoff_ms:
                reached_old_message = True
                continue
            recent_messages.append(message)

        if reached_old_message or len(messages) < PAGE_SIZE:
            break

        start += PAGE_SIZE

    print(f"[EmailTools] Messages from the last 24 hours: {len(recent_messages)}")
    return recent_messages


def filter_messages_by_sender(
    messages:list[dict[str, Any]],
    whitelist:set[str]
) -> list[dict[str, Any]]:
    """Filter messages to only those from whitelisted senders.

    Args:
        messages: Candidate message objects.
        whitelist: Lowercased sender addresses to keep.

    Returns:
        list[dict[str, Any]]: Filtered message objects.
    """

    if not whitelist:
        print("[EmailTools] Sender whitelist is empty, keeping all messages")
        return messages

    filtered_messages:list[dict[str, Any]] = []

    for message in messages:
        sender_address = extract_sender_address(message = message)
        if sender_address in whitelist:
            filtered_messages.append(message)

    print(f"[EmailTools] Messages after sender whitelist: {len(filtered_messages)}")
    return filtered_messages


def fetch_sender_content_tuples() -> list[tuple[str, str]]:
    """Fetch sender/content tuples for recent whitelisted messages.

    Args:
        None

    Returns:
        list[tuple[str, str]]: Sender and message-content tuples.
    """

    account_id = os.getenv("ZOHO_ACC_ID", "").strip()
    folder_id = os.getenv("ZOHO_FOLDER_ID", "").strip()
    if not account_id:
        raise RuntimeError("Missing ZOHO_ACC_ID environment variable.")
    if not folder_id:
        raise RuntimeError("Missing ZOHO_FOLDER_ID environment variable.")

    token_manager = AccessTokenManager()
    whitelist = parse_sender_whitelist()
    recent_messages = fetch_recent_messages(
        account_id = account_id,
        folder_id = folder_id,
        token_manager = token_manager
    )
    filtered_messages = filter_messages_by_sender(
        messages = recent_messages,
        whitelist = whitelist
    )

    sender_content_pairs:list[tuple[str, str]] = []

    for message in filtered_messages:
        sender = extract_sender_address(message = message)
        message_id = str(message.get("messageId", "")).strip()
        if not message_id:
            continue

        content = get_message_content(
            account_id = account_id,
            folder_id = folder_id,
            message_id = message_id,
            token_manager = token_manager
        )

        try:
            plain_text_content = util.extract_text_from_html_mail_content(html_content = content)
            sender_content_pairs.append((sender, plain_text_content))
        except Exception as exc:
            print(f"[EmailTools] Error when extracting content: {exc}")
            print(content)

    print(f"[EmailTools] Final sender/content tuple count: {len(sender_content_pairs)}")
    return sender_content_pairs
