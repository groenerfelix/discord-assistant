from __future__ import annotations
from datetime import datetime
from html.parser import HTMLParser
from html import unescape
import logging
import pytz
import re
import unicodedata
from typing import List

logger = logging.getLogger(__name__)

def get_datetime_string(timezone:str = "UTC", date_format:str = "%A, %Y-%m-%d %H:%M:%S") -> str:
    """
    Returns the current date and time as a formatted string.
    
    Args:
        timezone: IANA timezone string (e.g., "UTC", "US/Eastern", "Europe/London")
        date_format: strftime format string for output formatting
    
    Returns:
        Formatted datetime string in the specified timezone
    
    Format customization:
    - Date/time format: Use standard strftime directives
      %Y = 4-digit year, %m = month, %d = day
      %H = hour (24h), %M = minute, %S = second
      Example: "%d/%m/%Y %I:%M %p" produces "25/12/2024 03:30 PM"
    
    - Timezone: Use IANA timezone database names
      Common examples: "UTC", "US/Eastern", "US/Pacific", "Europe/London", "Asia/Tokyo"
      Full list: https://en.wikipedia.org/wiki/List_of_tz_database_time_zones
    """
    tz = pytz.timezone(timezone)
    now = datetime.now(tz)
    time_string = now.strftime(date_format)
    logger.debug("Generated datetime string for timezone %s: %s", timezone, time_string)
    return time_string

# def extract_text_from_html_mail_content(html_content:str, retain_links:bool = False) -> str:
#     """
#     Extracts readable text from HTML mail content by removing tags and decoding entities.

#     Args:
#         html_content: Raw HTML content from an email body
#         retain_links: Whether anchor tags should be replaced by their href values before stripping HTML

#     Returns:
#         Plain text content with HTML tags removed
#     """
#     logger.debug("Extracting text from HTML mail content")
#     text_content = html_content
#     if retain_links:
#         logger.debug("Retaining links while extracting HTML mail content")

#         def replace_anchor_with_href(match:re.Match[str]) -> str:
#             href_match = re.search(
#                 pattern = r'href\s*=\s*([\'\"])(.*?)\1',
#                 string = match.group(0),
#                 flags = re.IGNORECASE | re.DOTALL
#             )
#             if href_match is None:
#                 return ""
#             return " " + href_match.group(2) + " "

#         text_content = re.sub(
#             pattern = r"<a\b[^>]*>.*?</a>",
#             repl = replace_anchor_with_href,
#             string = text_content,
#             flags = re.IGNORECASE | re.DOTALL
#         )

#     text_content = re.sub(r"<[^<>]*>", "", text_content)
#     text_content = unescape(text_content)
#     text_content = re.sub(r"\s+", " ", text_content).strip()

#     # Filter weird encodings (mostly found in LinkedIn emails)
#     words = text_content.split()
#     # words = [word for word in words if not "\\\\" in word]
#     words = [word for word in words if not "\\" in word] # NOTE: experimental; might be too strict
#     text_content = " ".join(words)
    
#     logger.debug("Extracted text content with length %s", len(text_content))
#     return text_content

# region HTML parsing and cleaning

ANSI_RE = re.compile(r"\x1B[@-_][0-?]*[ -/]*[@-~]")

PRE_START = "\uFFF0PRE_START\uFFF0"
PRE_END = "\uFFF0PRE_END\uFFF0"

SKIP_TAGS = {"script", "style", "noscript", "template"}
BLOCK_TAGS = {
    "address", "article", "aside", "blockquote", "div", "dl", "dt", "dd",
    "fieldset", "figcaption", "figure", "footer", "form", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "hr", "li", "main", "nav", "ol", "p", "pre",
    "section", "table", "tbody", "thead", "tfoot", "tr", "ul", "td", "th", "br",
}


def remove_noise_chars(text: str) -> str:
    out: List[str] = []

    for ch in text:
        if ch in {"\n", "\t"}:
            out.append(ch)
            continue

        cat = unicodedata.category(ch)

        if cat in {"Cc", "Cf"}:
            if ch in {"\u200c", "\u200d"}:
                out.append(ch)
            continue

        out.append(ch)

    return "".join(out)


def normalize_non_pre(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = remove_noise_chars(text)
    text = re.sub(r"[ \f\v]+", " ", text)
    text = re.sub(r"[ \t]*\n[ \t]*", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def normalize_pre(text: str) -> str:
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = remove_noise_chars(text)
    return text.strip("\n")


class HtmlToText(HTMLParser):
    def __init__(self, retain_links: bool = False) -> None:
        super().__init__(convert_charrefs = True)
        self.parts: List[str] = []
        self.skip_depth = 0
        self.pre_depth = 0
        self.retain_links = retain_links
        self.link_hrefs: List[str | None] = []

    def _append(self, text: str) -> None:
        if text:
            self.parts.append(text)

    def handle_starttag(self, tag: str, attrs: List[tuple[str, str | None]]) -> None:
        tag = tag.lower()

        if tag in SKIP_TAGS:
            self.skip_depth += 1
            return

        if self.skip_depth:
            return

        if tag == "pre":
            self._append(PRE_START)
            self.pre_depth += 1
            return

        if tag == "br":
            self._append("\n")
            return

        if tag == "li":
            self._append("\n- ")
            return

        if tag in {"td", "th"}:
            self._append("\t")
            return

        if tag in BLOCK_TAGS:
            self._append("\n")
            return

        if tag == "img":
            alt = dict(attrs).get("alt")
            if alt:
                self._append(alt)

        if tag == "a":
            href = dict(attrs).get("href")
            self.link_hrefs.append(href)
            return

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()

        if tag in SKIP_TAGS:
            if self.skip_depth:
                self.skip_depth -= 1
            return

        if self.skip_depth:
            return

        if tag == "pre":
            if self.pre_depth:
                self.pre_depth -= 1
            self._append(PRE_END)
            return

        if tag in BLOCK_TAGS:
            self._append("\n")

        if tag == "a":
            if self.retain_links and self.link_hrefs:
                href = self.link_hrefs.pop()
                if href:
                    self._append(f" ({href})")
            elif self.link_hrefs:
                self.link_hrefs.pop()


    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return

        if self.pre_depth:
            self._append(data)
            return

        self._append(re.sub(r"\s+", " ", data))


def extract_text_from_html_mail_content(html_text: str, retain_links: bool = False) -> str:
    html_text = ANSI_RE.sub("", html_text)
    html_text = html_text.replace("\r\n", "\n").replace("\r", "\n")

    parser = HtmlToText(retain_links = retain_links)
    parser.feed(html_text)
    parser.close()

    text = "".join(parser.parts)

    segments = re.split(
        f"({re.escape(PRE_START)}|{re.escape(PRE_END)})",
        text
    )

    out: List[str] = []
    in_pre = False
    buffer: List[str] = []

    def flush_buffer(preserve_ws: bool) -> None:
        nonlocal buffer
        chunk = "".join(buffer)
        buffer = []

        if preserve_ws:
            chunk = normalize_pre(chunk)
        else:
            chunk = normalize_non_pre(chunk)

        if chunk:
            out.append(chunk)

    for part in segments:
        if not part:
            continue

        if part == PRE_START:
            flush_buffer(False)
            in_pre = True
            continue

        if part == PRE_END:
            flush_buffer(True)
            in_pre = False
            continue

        buffer.append(part)

    flush_buffer(in_pre)

    text = "\n\n".join(out)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


# endregion


if __name__ == "__main__":
    logging.basicConfig(
        level = logging.DEBUG,
        format = "[%(name)s] %(message)s"
    )
    # Example usage
    logger.info("%s", get_datetime_string())  # Default UTC
    logger.info("%s", get_datetime_string(timezone = "US/Arizona"))
    logger.info("%s", get_datetime_string(timezone = "Asia/Tokyo", date_format = "%Y-%m-%d %H:%M:%S %Z%z"))
    logger.info("%s", get_datetime_string(date_format = "%A, %Y-%m-%d %H:%M:%S"))  # Day of week included

    example_html_mail_content = """
    <html>
        <body>
            <p>Hello there,</p>
            <p>Please review <a href="https://example.com/reset?token=abc123">your reset link</a>.</p>
        </body>
    </html>
    """
    logger.info("HTML extraction test without links:")
    logger.info("%s", extract_text_from_html_mail_content(html_text = example_html_mail_content))
    logger.info("HTML extraction test with links:")
    logger.info(
        "%s",
        extract_text_from_html_mail_content(
            html_text = example_html_mail_content,
            retain_links = True
        )
    )

