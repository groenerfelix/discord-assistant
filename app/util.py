from datetime import datetime
from html import unescape
import logging
import pytz
import re

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

def extract_text_from_html_mail_content(html_content:str, retain_links:bool = False) -> str:
    """
    Extracts readable text from HTML mail content by removing tags and decoding entities.

    Args:
        html_content: Raw HTML content from an email body
        retain_links: Whether anchor tags should be replaced by their href values before stripping HTML

    Returns:
        Plain text content with HTML tags removed
    """
    logger.debug("Extracting text from HTML mail content")
    text_content = html_content
    if retain_links:
        logger.debug("Retaining links while extracting HTML mail content")

        def replace_anchor_with_href(match:re.Match[str]) -> str:
            href_match = re.search(
                pattern = r'href\s*=\s*([\'\"])(.*?)\1',
                string = match.group(0),
                flags = re.IGNORECASE | re.DOTALL
            )
            if href_match is None:
                return ""
            return " " + href_match.group(2) + " "

        text_content = re.sub(
            pattern = r"<a\b[^>]*>.*?</a>",
            repl = replace_anchor_with_href,
            string = text_content,
            flags = re.IGNORECASE | re.DOTALL
        )

    text_content = re.sub(r"<[^<>]*>", "", text_content)
    text_content = unescape(text_content)
    text_content = re.sub(r"\s+", " ", text_content).strip()
    logger.debug("Extracted text content with length %s", len(text_content))
    return text_content

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
    logger.info("%s", extract_text_from_html_mail_content(html_content = example_html_mail_content))
    logger.info("HTML extraction test with links:")
    logger.info(
        "%s",
        extract_text_from_html_mail_content(
            html_content = example_html_mail_content,
            retain_links = True
        )
    )

