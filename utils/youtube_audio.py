"""Choose the English audio track before considering playback format or quality."""

import math


class EnglishAudioUnavailableError(RuntimeError):
    def __init__(self):
        super().__init__('English audio is not available for this YouTube video')


def _language(audio: dict) -> str:
    return str(audio.get('language') or '').strip().lower().replace('_', '-')


def _number(value) -> float:
    try:
        number = float(value)
        return number if math.isfinite(number) else 0.0
    except (TypeError, ValueError):
        return 0.0


def select_english_audio_format(info: dict) -> dict:
    formats = info.get('formats')
    formats = formats if isinstance(formats, list) else []
    # Apply the same checks to the extractor's selected top-level URL. Neither
    # it nor formats[0] may bypass the language policy when a fallback is needed.
    candidates = [
        audio for audio in [*formats, info]
        if isinstance(audio, dict)
        and isinstance(audio.get('url'), str) and audio['url'].strip()
        and audio.get('vcodec') in (None, 'none')
        and audio.get('acodec') not in (None, 'none')
    ]
    if not candidates:
        raise RuntimeError('No audio stream URL found')

    english = [audio for audio in candidates if _language(audio).split('-', 1)[0] in ('en', 'eng')]
    if english:
        candidates = english
    elif any(_language(audio) not in ('', 'und', 'zxx') for audio in candidates):
        # Do not quietly substitute another dub, including an untagged variant
        # of a known non-English track. Completely unlabelled clips still work.
        raise EnglishAudioUnavailableError()

    def playback_rank(audio: dict) -> tuple:
        protocol = str(audio.get('protocol') or '').lower()
        ext = str(audio.get('ext') or '').lower().strip()
        mime = str(audio.get('mime_type') or '').lower().split(';', 1)[0].strip()
        direct = 'm3u8' not in protocol and 'dash' not in protocol and ext not in ('m3u8', 'mpd')
        compatible = ext in ('m4a', 'mp4', 'mp3') or mime in ('audio/mp4', 'audio/mpeg')
        return (direct, compatible, _number(audio.get('language_preference')), _number(audio.get('abr')))

    return max(candidates, key=playback_rank)
