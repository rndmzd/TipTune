"""Actionable Spotify errors without provider payloads or credentials."""

from requests.exceptions import ConnectionError, Timeout


def spotify_error_message(exc: Exception) -> str:
    error = getattr(exc, 'error', None)
    status = getattr(exc, 'http_status', None)
    if error == 'invalid_client':
        return ('Spotify rejected API client authorization (invalid_client). Reconnect Spotify '
                'in Settings. If it still fails, check that the app exists and is enabled in '
                'your Spotify Developer Dashboard and that its Client ID matches Settings.')
    if error == 'invalid_grant' or status == 401:
        return 'Spotify authorization expired or was revoked. Reconnect Spotify in Settings.'
    if status == 403:
        return ('Spotify denied API access. Check the app and allowed users in your Spotify '
                'Developer Dashboard and the account requirements, then reconnect Spotify.')
    if status == 429:
        return 'Spotify is rate limiting requests. Wait a moment, then refresh devices.'
    if isinstance(exc, Timeout):
        return 'Spotify did not respond in time. Check your connection, then refresh devices.'
    if isinstance(exc, ConnectionError):
        return 'Unable to reach Spotify. Check your connection, then refresh devices.'
    return 'Unable to connect to the Spotify API. Check your Spotify settings and reconnect.'
