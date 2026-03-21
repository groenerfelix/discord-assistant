from datetime import datetime
import pytz

def get_datetime_string(timezone:str = "UTC", date_format:str = "%Y-%m-%d %H:%M:%S") -> str:
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
    print(f"[util] Generated datetime string for timezone {timezone}: {time_string}")
    return time_string

if __name__ == "__main__":
    # Example usage
    print(get_datetime_string())  # Default UTC
    print(get_datetime_string(timezone="US/Arizona"))
    print(get_datetime_string(timezone="Asia/Tokyo", date_format="%Y-%m-%d %H:%M:%S %Z%z"))