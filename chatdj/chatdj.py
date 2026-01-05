import json
import re
import time
import threading
from typing import Any, Dict, List, Optional, Tuple

import openai
from openai import OpenAI
import requests
from pydantic import BaseModel
from rapidfuzz import fuzz, process
from spotipy import Spotify, SpotifyException

from utils.structured_logging import get_structured_logger

logger = get_structured_logger('tiptune.chatdj.chatdj')


class SongRequest(BaseModel):
    """Pydantic model for structured song request output.
    The spotify_uri is optional so it can be filled in later."""
    artist: str
    song: str
    spotify_uri: Optional[str] = None

class SongExtractor:
    """
    Extracts song and artist information from a message.

    If a Spotify URI is found and a spotify_client is provided, it uses the Spotify API.
    Otherwise, it uses the OpenAI Responses API to extract song requests.
    If an extracted song request has no artist, it uses a hybrid approach (Google Custom Search + OpenAI Responses)
    to look up the artist name.
    """
    def __init__(
        self,
        openai_api_key: str,
        spotify_client: Optional[object] = None,
        google_api_key: Optional[str] = None,
        google_cx: Optional[str] = None,
        model: str = "gpt-5"
    ):
        # Set up the OpenAI API key.
        openai.api_key = openai_api_key
        self.openai_client = OpenAI(api_key=openai_api_key)
        self.spotify_client = spotify_client
        self.google_api_key = google_api_key
        self.google_cx = google_cx
        self.model = model
        logger.debug("song_extractor.init",
                    message="Initialized SongExtractor",
                    data={"model": self.model})

    def extract_songs(self, message: str, song_count: int = 1) -> List[SongRequest]:
        try:
            logger.debug("song.extract.start",
                        message="Starting song extraction",
                        data={
                            "message": message,
                            "requested_count": song_count
                        })
            # Look for Spotify track URIs
            spotify_uri_pattern = r"(spotify:track:[a-zA-Z0-9]+|https?://open\.spotify\.com/track/[a-zA-Z0-9]+)"
            found_uris = re.findall(spotify_uri_pattern, message)
            logger.debug("song.extract.spotify.uris",
                        message="Searched for Spotify URIs in message",
                        data={
                            "found_uris": found_uris,
                            "uri_count": len(found_uris)
                        })

            songs = []
            if found_uris and self.spotify_client:
                unique_uris = list(dict.fromkeys(found_uris))[:song_count]
                logger.debug("song.extract.spotify.process",
                           message="Found Spotify URIs in message",
                           data={
                               "unique_uris": unique_uris,
                               "processing_count": len(unique_uris)
                           })
                for uri in unique_uris:
                    try:
                        track_info = self.spotify_client.track(uri)
                        song_name = track_info.get('name', '')
                        artist_name = track_info.get('artists', [{}])[0].get('name', '')
                        logger.debug("song.extract.spotify.track",
                                   message="Found Spotify URIs in message",
                                   data={
                                       "uri": uri,
                                       "song": song_name,
                                       "artist": artist_name,
                                       "track_info": track_info
                                   })
                        songs.append(SongRequest(song=song_name, artist=artist_name, spotify_uri=uri))
                    except Exception as exc:
                        logger.exception("spotify.track.error",
                                       message="Error retrieving track info",
                                       exc=exc,
                                       data={
                                           "uri": uri,
                                           "error_type": type(exc).__name__
                                       })
                if songs:
                    logger.debug("song.extract.spotify.complete",
                               message="Found songs from Spotify URIs in message",
                               data={"extracted_songs": [s.dict() for s in songs]})
                    return songs
            
            original_message = message
            message = re.sub(r'(\S)-(\S)', r'\1 - \2', message)
            if original_message != message:
                logger.debug("song.extract.preprocess",
                           message="Added spaces around dash symbol",
                           data={
                               "original_message": original_message,
                               "processed_message": message
                           })

            # Fallback to OpenAI Responses
            logger.debug("song.extract.chat.start",
                        message="Starting OpenAI Responses extraction",
                        data={"message": message})
            prompt_text = (
                "You are a music bot that processes song requests. "
                f"Extract exactly {song_count} song request(s) from the following message. "
                "Return a JSON array of objects with exactly two keys: 'song' and 'artist'. "
                "If you can identify a song name but no artist is specified, include the song "
                "with an empty artist field. Treat single terms or phrases as potential song "
                "titles if they could be song names. For example, 'mucka blucka' would be "
                "extracted as {'song': 'Mucka Blucka', 'artist': ''}. If you cannot identify "
                "a song name, return the original message as the song name and an empty artist field. "
                "If the message starts with 'The song name might be', remove that phrase and only return "
                "the song name.\n\n"
                f"Message: '{message}'\n\n"
                "Return ONLY the JSON array with no extra text."
            )
            try:
                logger.debug("song.extract.chat.request",
                           message="Sending request to OpenAI Responses",
                           data={"prompt": prompt_text})
                response = self.openai_client.responses.create(
                    model=self.model,
                    input=prompt_text
                )
                content = response.output_text.strip()
                logger.debug("song.extract.chat.response",
                           message="Received OpenAI Responses response",
                           data={"raw_content": content})

                # Remove markdown code fences if present
                if content.startswith("```"):
                    lines = content.splitlines()
                    lines = [line for line in lines if not line.strip().startswith("```")]
                    content = "\n".join(lines).strip()
                    logger.debug("song.extract.chat.clean",
                               message="Cleaned markdown from response",
                               data={"cleaned_content": content})

                data = json.loads(content)
                songs = [SongRequest(**item) for item in data]
                logger.debug("song.extract.chat.success",
                           message="Successfully parsed OpenAI Responses response",
                           data={"parsed_songs": [s.model_dump() for s in songs]})
            except Exception as exc:
                logger.exception("song.extract.chat.error",
                               message="Failed to extract songs via OpenAI Responses",
                               exc=exc,
                               data={
                                   "message": message,
                                   "error_type": type(exc).__name__
                               })
                return []

            # Artist lookup for missing artists
            for song_request in songs:
                if not song_request.artist.strip():
                    logger.debug("song.extract.artist.lookup",
                               message="Looking up missing artist",
                               data={"song": song_request.song})
                    found_artist = self.lookup_artist_hybrid(song_request.song)
                    song_request.artist = found_artist if found_artist else ""
                    logger.debug("song.extract.artist.result",
                               message="Artist lookup complete",
                               data={
                                   "song": song_request.song,
                                   "found_artist": song_request.artist
                               })

            logger.debug("song.extract.complete",
                        message="Song extraction complete",
                        data={"final_songs": [s.model_dump() for s in songs]})
            return songs
        except Exception as exc:
            logger.exception("song.extract.error",
                           message="Failed to extract songs",
                           exc=exc,
                           data={
                               "message": message,
                               "error_type": type(exc).__name__
                           })
            return []

    def lookup_artist_hybrid(self, song_name: str) -> str:
        """
        Hybrid approach: first uses Google Custom Search to fetch a snippet,
        then uses the OpenAI Responses API to confirm the artist.
        """
        logger.debug("artist.lookup.start",
                    message="Starting hybrid artist lookup",
                    data={"song": song_name})

        snippet = self.lookup_artist_by_song_via_google(song_name)
        if snippet:
            logger.debug("artist.lookup.google.success",
                        message="Found Google search snippet",
                        data={
                            "song": song_name,
                            "snippet": snippet
                        })
            artist = self.lookup_artist_with_chat(song_name, snippet)
            if artist:
                logger.debug("artist.lookup.chat.success",
                           message="Successfully extracted artist from snippet",
                           data={
                               "song": song_name,
                               "artist": artist,
                               "snippet": snippet
                           })
                return artist
            else:
                logger.warning("artist.lookup.chat.failed",
                             message="Failed to extract artist from snippet",
                             data={
                                 "song": song_name,
                                 "snippet": snippet
                             })
        else:
            logger.warning("artist.lookup.google.failed",
                         message="No Google search results found",
                         data={"song": song_name})
        return ""

    def lookup_artist_by_song_via_google(self, song_name: str) -> str:
        """
        Uses the Google Custom Search API to fetch a snippet regarding the song.
        """
        if not self.google_api_key or not self.google_cx:
            logger.error("artist.lookup.google.config",
                        message="Google API key or custom search engine ID (CX) not provided")
            return ""

        query = f"Who is the song '{song_name}' by?"
        endpoint = "https://www.googleapis.com/customsearch/v1"
        params = {
            "key": self.google_api_key,
            "cx": self.google_cx,
            "q": query,
        }

        logger.debug("artist.lookup.google.request",
                    message="Sending Google search request",
                    data={
                        "song": song_name,
                        "query": query
                    })

        try:
            response = requests.get(endpoint, params=params, timeout=5)
            response.raise_for_status()
            data = response.json()

            # Use the first item's snippet if available.
            if "items" in data and data["items"]:
                snippet = data["items"][0].get("snippet", "")
                logger.debug("artist.lookup.google.response",
                           message="Received Google search response",
                           data={
                               "song": song_name,
                               "snippet": snippet,
                               "total_results": len(data["items"])
                           })
                return snippet

            logger.warning("artist.lookup.google.empty",
                         message="No search results found",
                         data={"song": song_name})
            return ""

        except Exception as exc:
            logger.exception("artist.lookup.google.error",
                           message="Error during Google search",
                           exc=exc,
                           data={
                               "song": song_name,
                               "error_type": type(exc).__name__
                           })
            return ""

    def lookup_artist_with_chat(self, song_name: str, snippet: str) -> str:
        """
        Uses the OpenAI Responses API with the search snippet to confirm the artist.
        """
        prompt_text = (
            "You are a helpful assistant specialized in music information. "
            "Your task is to extract the artist name from the provided information. "
            "Return ONLY the artist name, nothing else. If you cannot determine "
            "the artist with certainty, return an empty string.\n\n"
            f"Information: '{snippet}'\n"
            f"Song: '{song_name}'\n\n"
            "Answer with only the artist's name."
        )

        logger.debug("artist.lookup.chat.request",
                    message="Sending artist extraction request via OpenAI Responses",
                    data={"song": song_name, "snippet": snippet, "prompt": prompt_text})

        try:
            response = self.openai_client.responses.create(
                model=self.model,
                input=prompt_text
            )
            content = response.output_text.strip()

            # Assume the artist's name is on the first line.
            artist_name = content.splitlines()[0].strip()

            logger.debug("artist.lookup.chat.response",
                        message="Received artist extraction response",
                        data={
                            "song": song_name,
                            "artist": artist_name,
                            "raw_response": content
                        })
            return artist_name

        except Exception as exc:
            logger.exception("artist.lookup.chat.error",
                           message="Error during artist extraction",
                           exc=exc,
                           data={
                               "song": song_name,
                               "error_type": type(exc).__name__
                           })
            return ""

class AutoDJ:
    """AutoDJ class using Spotify APIs.
    The search_track_uri method applies filtering to only return tracks available in the US market,
    avoids live versions, and returns the most popular match for an exact artist match."""
    def __init__(self, spotify: Spotify, playback_device_id: Optional[str] = None):
        self.spotify = spotify
        self.playback_device: Optional[str] = None
        self.playback_device_name: Optional[str] = None

        if playback_device_id:
            try:
                self.set_playback_device(playback_device_id, silent=True)
            except Exception:
                pass

        if not self.playback_device:
            try:
                device = self._auto_select_playback_device()
                if device and isinstance(device, dict):
                    self.playback_device = device.get('id')
                    self.playback_device_name = device.get('name')
                    if self.playback_device:
                        logger.info(
                            "spotify.device.selected",
                            message="Device auto-selected",
                            data={
                                "name": self.playback_device_name,
                                "id": self.playback_device,
                                "auto": True
                            }
                        )
            except Exception:
                pass

        if not self.playback_device:
            selected_device_id = self._select_playback_device()
            self.playback_device = selected_device_id
            try:
                for d in self.get_available_devices():
                    if isinstance(d, dict) and d.get('id') == selected_device_id:
                        self.playback_device_name = d.get('name')
                        break
            except Exception:
                pass
            self.set_playback_device(selected_device_id, silent=True)

        logger.debug("spotify.playback.init", message="Initializing playback state")
        self.playing_first_track = False

        self._queue_unpaused = threading.Event()
        self._queue_unpaused.set()

        self._queue_lock = threading.Lock()

        self.queued_tracks = []
        self.now_playing_track_uri = None
        self.clear_playback_context()
        self._print_variables()

    def get_queued_tracks_snapshot(self) -> List[Any]:
        try:
            with self._queue_lock:
                return list(self.queued_tracks)
        except Exception:
            return []

    def move_queued_track(self, from_index: int, to_index: int) -> bool:
        try:
            fi = int(from_index)
            ti = int(to_index)
        except Exception:
            return False

        try:
            with self._queue_lock:
                n = len(self.queued_tracks)
                if fi < 0 or fi >= n:
                    return False
                if ti < 0 or ti >= n:
                    return False
                if fi == ti:
                    return True
                item = self.queued_tracks.pop(fi)
                self.queued_tracks.insert(ti, item)
            self._print_variables(True)
            return True
        except Exception as exc:
            logger.exception("queue.move.error", message="Failed to move queued track", exc=exc)
            return False

    def delete_queued_track(self, index: int) -> bool:
        try:
            idx = int(index)
        except Exception:
            return False

        try:
            with self._queue_lock:
                n = len(self.queued_tracks)
                if idx < 0 or idx >= n:
                    return False
                _ = self.queued_tracks.pop(idx)
            self._print_variables(True)
            return True
        except Exception as exc:
            logger.exception("queue.delete.error", message="Failed to delete queued track", exc=exc)
            return False

    def get_available_devices(self) -> List[Dict[str, Any]]:
        try:
            payload = self.spotify.devices()
            devices = payload.get('devices', []) if isinstance(payload, dict) else []
            if isinstance(devices, list):
                return devices
            return []
        except Exception as exc:
            logger.exception("spotify.devices.error", message="Failed to list Spotify devices", exc=exc)
            return []

    def _auto_select_playback_device(self) -> Optional[Dict[str, Any]]:
        devices = self.get_available_devices()
        if not devices:
            return None
        active = [d for d in devices if isinstance(d, dict) and d.get('is_active')]
        if active:
            return active[0]
        return devices[0] if isinstance(devices[0], dict) else None

    def set_playback_device(self, device_id: str, force_play: bool = False, silent: bool = False) -> bool:
        try:
            if not device_id:
                return False

            self.spotify.transfer_playback(device_id=device_id, force_play=force_play)
            self.playback_device = device_id

            try:
                for d in self.get_available_devices():
                    if isinstance(d, dict) and d.get('id') == device_id:
                        self.playback_device_name = d.get('name')
                        break
            except Exception:
                pass

            if not silent:
                logger.info(
                    "spotify.device.selected",
                    message="Device selected",
                    data={
                        "name": self.playback_device_name,
                        "id": self.playback_device,
                        "auto": False
                    }
                )
            return True
        except Exception as exc:
            logger.exception("spotify.device.error", message="Failed to set playback device", exc=exc)
            return False

    def _print_variables(self, return_value=None):
        """Stub function for logging internal state."""

    def _select_playback_device(self) -> str:
        try:
            devices = self.spotify.devices()['devices']
            logger.debug(f"Available devices: {devices}")
            if not devices:
                logger.error("spotify.devices.error",
                           message="No Spotify devices found")
                raise ValueError("No available Spotify devices found.")
            print("\n==[ Available Spotify Devices ]==\n")
            for idx, device in enumerate(devices):
                print(f"{idx+1} - {device['name']}")
            while True:
                try:
                    selection = int(input("\nChoose playback device number: "))
                    device = devices[selection - 1]
                    self.playback_device_name = device.get('name')
                    logger.info("spotify.device.selected",
                              message="Device selected",
                              data={
                                  "name": device['name'],
                                  "id": device['id']
                              })
                    return device['id']
                except KeyboardInterrupt:
                    logger.info("spotify.device.cancel",
                              message="User cancelled device selection")
                    raise
                except (ValueError, IndexError):
                    logger.error("spotify.device.error",
                               message="Invalid device selection")
                    print("Invalid selection. Please try again.")
        except Exception as e:
            logger.exception("spotify.device.error",
                            message="Failed to select playback device",
                            exc=e)

            raise

    def search_track_uri(self, song: str, artist: str) -> Optional[str]:
        """
        Enhanced Spotify track search with multi-stage fallback strategy.
        
        Uses fuzzy matching, multiple query formats, and similarity-based ranking
        to dramatically improve song matching accuracy compared to the original
        strict exact-match approach.
        
        Stages:
        1. Exact search (original behavior)
        2. Fuzzy artist matching
        3. Song-focused search with artist filtering
        4. Broad search with similarity scoring
        5. Cross-market search if needed
        """
        logger.debug("spotify.search.enhanced.start",
                    message="Starting enhanced Spotify search",
                    data={"song": song, "artist": artist})
        
        # Stage 1: Exact search (original behavior)
        result = self._search_exact(song, artist)
        if result:
            logger.debug("spotify.search.stage1.success", message="Found match with exact search")
            return result
            
        # Stage 2: Fuzzy artist matching
        result = self._search_fuzzy_artist(song, artist)
        if result:
            logger.debug("spotify.search.stage2.success", message="Found match with fuzzy artist search")
            return result
            
        # Stage 3: Song-focused search with artist filtering
        result = self._search_song_focused(song, artist)
        if result:
            logger.debug("spotify.search.stage3.success", message="Found match with song-focused search")
            return result
            
        # Stage 4: Broad search with similarity scoring
        result = self._search_similarity_based(song, artist)
        if result:
            logger.debug("spotify.search.stage4.success", message="Found match with similarity-based search")
            return result
            
        # Stage 5: Cross-market search
        result = self._search_cross_market(song, artist)
        if result:
            logger.debug("spotify.search.stage5.success", message="Found match with cross-market search")
            return result
            
        logger.warning("spotify.search.enhanced.no_match",
                      message="No matching track found after all search stages",
                      data={"song": song, "artist": artist})
        return None
    
    def _search_exact(self, song: str, artist: str, market: str = "US") -> Optional[str]:
        """Stage 1: Original exact search logic."""
        try:
            query = f"track:{song} artist:{artist}"
            logger.debug("spotify.search.exact", message=f"Exact search: {query}")
            
            results = self.spotify.search(q=query, type='track', market=market, limit=50)
            tracks = results.get('tracks', {}).get('items', [])
            
            filtered_tracks = []
            for track in tracks:
                track_name = track.get('name', '')
                track_artists = [a.get('name', '').strip().lower() for a in track.get('artists', [])]
                if artist.strip().lower() not in track_artists:
                    continue
                if "live" in track_name.lower():
                    continue
                filtered_tracks.append(track)
                
            # Fallback: try without live filtering
            if not filtered_tracks and tracks:
                for track in tracks:
                    track_artists = [a.get('name', '').strip().lower() for a in track.get('artists', [])]
                    if artist.strip().lower() in track_artists:
                        filtered_tracks.append(track)
                        
            if filtered_tracks:
                best_track = max(filtered_tracks, key=lambda x: x.get('popularity', 0))
                return best_track.get('uri')
                
        except SpotifyException as exc:
            logger.debug("spotify.search.exact.error", message="Exact search failed", data={"error": str(exc)})
            
        return None
    
    def _search_fuzzy_artist(self, song: str, artist: str, market: str = "US") -> Optional[str]:
        """Stage 2: Search with fuzzy artist name matching."""
        try:
            # Try multiple query variations
            queries = [
                f"track:{song} artist:{artist}",
                f'"{song}" "{artist}"',
                f"{song} {artist}",
                f"track:\"{song}\""
            ]
            
            all_tracks = []
            for query in queries:
                try:
                    results = self.spotify.search(q=query, type='track', market=market, limit=50)
                    tracks = results.get('tracks', {}).get('items', [])
                    all_tracks.extend(tracks)
                except SpotifyException:
                    continue
                    
            if not all_tracks:
                return None
                
            # Remove duplicates based on URI
            unique_tracks = {track['uri']: track for track in all_tracks}.values()
            
            # Score tracks based on fuzzy artist matching
            scored_tracks = []
            for track in unique_tracks:
                track_artists = [a.get('name', '') for a in track.get('artists', [])]
                
                # Find best artist match using fuzzy matching
                best_artist_score = 0
                for track_artist in track_artists:
                    score = fuzz.ratio(artist.lower(), track_artist.lower())
                    best_artist_score = max(best_artist_score, score)
                    
                # Also check song title similarity
                song_score = fuzz.ratio(song.lower(), track.get('name', '').lower())
                
                # Combined score (weighted toward artist matching)
                combined_score = (best_artist_score * 0.7) + (song_score * 0.3)
                
                # Only consider tracks with reasonable artist similarity
                if best_artist_score >= 75:  # 75% similarity threshold
                    scored_tracks.append((track, combined_score, best_artist_score, song_score))
                    
            if scored_tracks:
                # Sort by combined score, then popularity
                scored_tracks.sort(key=lambda x: (x[1], x[0].get('popularity', 0)), reverse=True)
                
                # Filter out live tracks if possible
                non_live_tracks = [t for t in scored_tracks if "live" not in t[0].get('name', '').lower()]
                if non_live_tracks:
                    best_track = non_live_tracks[0][0]
                else:
                    best_track = scored_tracks[0][0]
                    
                logger.debug("spotify.search.fuzzy.match",
                           message="Found fuzzy match",
                           data={
                               "track_name": best_track.get('name'),
                               "track_artist": best_track.get('artists', [{}])[0].get('name'),
                               "combined_score": scored_tracks[0][1],
                               "artist_score": scored_tracks[0][2],
                               "song_score": scored_tracks[0][3]
                           })
                return best_track.get('uri')
                
        except Exception as exc:
            logger.debug("spotify.search.fuzzy.error", message="Fuzzy search failed", data={"error": str(exc)})
            
        return None
    
    def _search_song_focused(self, song: str, artist: str, market: str = "US") -> Optional[str]:
        """Stage 3: Song-focused search with loose artist filtering."""
        try:
            # Search primarily by song title
            query = f"track:\"{song}\""
            logger.debug("spotify.search.song_focused", message=f"Song-focused search: {query}")
            
            results = self.spotify.search(q=query, type='track', market=market, limit=50)
            tracks = results.get('tracks', {}).get('items', [])
            
            if not tracks:
                return None
                
            # Score tracks based on artist similarity
            scored_tracks = []
            for track in tracks:
                track_artists = [a.get('name', '') for a in track.get('artists', [])]
                
                # Find best artist match
                best_artist_score = 0
                for track_artist in track_artists:
                    score = fuzz.ratio(artist.lower(), track_artist.lower())
                    best_artist_score = max(best_artist_score, score)
                    
                # Only consider tracks with some artist similarity
                if best_artist_score >= 60:  # Lower threshold for song-focused search
                    scored_tracks.append((track, best_artist_score))
                    
            if scored_tracks:
                # Sort by artist score, then popularity
                scored_tracks.sort(key=lambda x: (x[1], x[0].get('popularity', 0)), reverse=True)
                
                # Prefer non-live tracks
                non_live_tracks = [t for t in scored_tracks if "live" not in t[0].get('name', '').lower()]
                if non_live_tracks:
                    return non_live_tracks[0][0].get('uri')
                else:
                    return scored_tracks[0][0].get('uri')
                    
        except Exception as exc:
            logger.debug("spotify.search.song_focused.error", message="Song-focused search failed", data={"error": str(exc)})
            
        return None
    
    def _search_similarity_based(self, song: str, artist: str, market: str = "US") -> Optional[str]:
        """Stage 4: Broad search with comprehensive similarity scoring."""
        try:
            # Try very broad searches
            queries = [
                f"{song} {artist}",
                f"{song}",
                f"{artist}"
            ]
            
            all_tracks = []
            for query in queries:
                try:
                    results = self.spotify.search(q=query, type='track', market=market, limit=50)
                    tracks = results.get('tracks', {}).get('items', [])
                    all_tracks.extend(tracks)
                except SpotifyException:
                    continue
                    
            if not all_tracks:
                return None
                
            # Remove duplicates
            unique_tracks = {track['uri']: track for track in all_tracks}.values()
            
            # Comprehensive similarity scoring
            scored_tracks = []
            for track in unique_tracks:
                track_name = track.get('name', '')
                track_artists = [a.get('name', '') for a in track.get('artists', [])]
                
                # Song title similarity
                song_score = fuzz.ratio(song.lower(), track_name.lower())
                
                # Best artist similarity
                best_artist_score = 0
                for track_artist in track_artists:
                    score = fuzz.ratio(artist.lower(), track_artist.lower())
                    best_artist_score = max(best_artist_score, score)
                    
                # Combined score with different weighting
                combined_score = (song_score * 0.6) + (best_artist_score * 0.4)
                
                # Only consider tracks with reasonable overall similarity
                if combined_score >= 50:  # Lower threshold for broad search
                    scored_tracks.append((track, combined_score, song_score, best_artist_score))
                    
            if scored_tracks:
                # Sort by combined score, then popularity
                scored_tracks.sort(key=lambda x: (x[1], x[0].get('popularity', 0)), reverse=True)
                
                # Prefer non-live tracks
                non_live_tracks = [t for t in scored_tracks if "live" not in t[0].get('name', '').lower()]
                if non_live_tracks:
                    best_track = non_live_tracks[0][0]
                else:
                    best_track = scored_tracks[0][0]
                    
                logger.debug("spotify.search.similarity.match",
                           message="Found similarity-based match",
                           data={
                               "track_name": best_track.get('name'),
                               "track_artist": best_track.get('artists', [{}])[0].get('name'),
                               "combined_score": scored_tracks[0][1],
                               "song_score": scored_tracks[0][2],
                               "artist_score": scored_tracks[0][3]
                           })
                return best_track.get('uri')
                
        except Exception as exc:
            logger.debug("spotify.search.similarity.error", message="Similarity search failed", data={"error": str(exc)})
            
        return None
    
    def _search_cross_market(self, song: str, artist: str) -> Optional[str]:
        """Stage 5: Try different markets if US search fails."""
        markets = ["GB", "CA", "AU", "DE", "FR", None]  # None = no market restriction
        
        for market in markets:
            logger.debug("spotify.search.cross_market", message=f"Trying market: {market}")
            
            # Try exact search in different market
            result = self._search_exact(song, artist, market)
            if result:
                return result
                
            # Try fuzzy search in different market
            result = self._search_fuzzy_artist(song, artist, market)
            if result:
                return result
                
        return None


    def queue_paused(self) -> bool:
        return not self._queue_unpaused.is_set()

    def pause_queue(self, silent: bool = False) -> bool:
        try:
            self._queue_unpaused.clear()
            if not silent:
                logger.info(
                    "queue.pause",
                    message="Queue paused. Current song will be allowed to finish; next songs will wait until resumed.",
                    data={"queued_tracks": len(self.queued_tracks)}
                )
            return True
        except Exception as exc:
            logger.exception("queue.pause.error", message="Failed to pause queue", exc=exc)
            return False

    def unpause_queue(self, silent: bool = False) -> bool:
        try:
            self._queue_unpaused.set()
            if not silent:
                logger.info(
                    "queue.unpause",
                    message="Queue resumed.",
                    data={"queued_tracks": len(self.queued_tracks)}
                )
            return True
        except Exception as exc:
            logger.exception("queue.unpause.error", message="Failed to resume queue", exc=exc)
            return False


    def add_song_to_queue(self, track_uri: str, silent=False) -> bool:
        try:
            if not silent:
                logger.debug("spotify.queue.add",
                            message="Adding track to queue",
                            data={"track_uri": track_uri})

            with self._queue_lock:
                self.queued_tracks.append(track_uri)
                qlen = len(self.queued_tracks)

            if not self.playback_active() and qlen == 1:
                self.playing_first_track = True
            logger.debug("spotify.queue.status",
                        message="Current queue status",
                        data={"queued_tracks": self.queued_tracks})
            self._print_variables(True)
            return True
        except SpotifyException as e:
            logger.exception("spotify.queue.add.error",
                message="Failed to add song to queue",
                exc=e)
            return False

    def insert_song_to_queue(self, track_uri: str, index: int = 0, silent: bool = False) -> bool:
        try:
            if not silent:
                logger.debug(
                    "spotify.queue.insert",
                    message="Inserting track into queue",
                    data={"track_uri": track_uri, "index": index},
                )

            with self._queue_lock:
                n = len(self.queued_tracks)
                try:
                    idx = int(index)
                except Exception:
                    idx = 0
                if idx < 0:
                    idx = 0
                if idx > n:
                    idx = n
                self.queued_tracks.insert(idx, track_uri)
                qlen = len(self.queued_tracks)

            if not self.playback_active() and qlen == 1:
                self.playing_first_track = True

            logger.debug(
                "spotify.queue.status",
                message="Current queue status",
                data={"queued_tracks": self.queued_tracks},
            )
            self._print_variables(True)
            return True
        except SpotifyException as e:
            logger.exception(
                "spotify.queue.insert.error",
                message="Failed to insert song into queue",
                exc=e,
            )
            return False

    def check_queue_status(self, silent=False) -> bool:
        try:
            if not self.playback_active():
                paused = self.queue_paused()
                with self._queue_lock:
                    qlen = len(self.queued_tracks)

                if qlen == 0:
                    self.now_playing_track_uri = None

                if paused and qlen > 0:
                    self.now_playing_track_uri = None
                    if not silent:
                        logger.info(
                            "queue.check.paused",
                            message="Queue is paused. Waiting to resume before starting the next song.",
                            data={"queued_tracks": qlen}
                        )
                    self._print_variables(False)
                    return False
                if qlen > 0:
                    logger.info("queue.check.start",
                                message="Queue populated but playback is not active. Starting playback.")
                    with self._queue_lock:
                        if not self.queued_tracks:
                            return False
                        popped_track = self.queued_tracks.pop(0)
                        self.now_playing_track_uri = popped_track
                    logger.debug("queue.check.popped",
                                message=f"Popped track: {popped_track}")
                    self.spotify.start_playback(device_id=self.playback_device, uris=[popped_track])
                    logger.debug("queue.check.playing_first_track",
                                message="Clearing playing_first_track flag.")
                    self.playing_first_track = False
                    return True
                self._print_variables(False)

                if qlen == 0 and not self.playing_first_track:
                    logger.info("queue.check.empty",
                                message="Queue is now empty")
                    self.playing_first_track = True

                return False

            with self._queue_lock:
                first_queued = self.queued_tracks[0] if self.queued_tracks else None

            paused = self.queue_paused()
            if paused:
                self._print_variables(True)
                return True

            if first_queued:
                current_track = self.spotify.current_playback()['item']['uri']
                logger.debug(f"Current playing track: {current_track}")
                if current_track == first_queued:
                    logger.info(f"Now playing queued track: {current_track}")
                    with self._queue_lock:
                        if self.queued_tracks and self.queued_tracks[0] == current_track:
                            popped_track = self.queued_tracks.pop(0)
                            self.now_playing_track_uri = popped_track
                        else:
                            popped_track = None
                    logger.debug("queue.check.popped",
                                message=f"Popped track: {popped_track}")
            self._print_variables(True)
            return True
        except SpotifyException as exc:
            logger.exception("queue.check.error",
                            message="Failed to check queue status",
                            exc=exc)
            return False
        except Exception as exc:
            logger.exception("queue.check.error",
                            message="Failed to check queue status",
                            exc=exc)
            return False


    def clear_playback_context(self) -> bool:
        try:
            logger.info("Clearing playback context.")
            if self.playback_active():
                self.spotify.pause_playback(device_id=self.playback_device)
            previous_track = None
            attempts = 0
            max_attempts = 5
            while True:
                queue = self.spotify.queue()
                if len(queue['queue']) == 0:
                    logger.info("queue.clear.empty",
                                message="Queue is now empty")
                    break
                current_track = queue['queue'][0]['uri']
                if current_track == previous_track:

                    attempts += 1
                    if attempts >= max_attempts:
                        logger.info("queue.clear.max_attempts",
                                    message="Unable to clear the last track. Stopping.")
                        break
                else:

                    attempts = 0
                try:
                    self.spotify.next_track()
                    logger.info("queue.clear.skipped",
                                message=f"Skipped track: {queue['queue'][0]['name']}")
                    time.sleep(1)
                except SpotifyException as exc:
                    logger.error("queue.clear.error",
                                message=f"Error skipping track: {exc}")
                    break

                previous_track = current_track

            try:
                self.spotify.pause_playback()
                logger.info("queue.clear.pause",
                            message="Playback paused.")
            except SpotifyException as exc:
                logger.error("queue.clear.error",
                            message=f"Error pausing playback: {exc}")
            self.playing_first_track = False

            self.now_playing_track_uri = None

            with self._queue_lock:
                self.queued_tracks.clear()
            self._print_variables(True)
            return True


        except SpotifyException as exc:
            logger.exception("queue.clear.error",
                            message="Failed to clear playback context.",
                            exc=exc)

            return False


    def get_user_market(self) -> Optional[str]:
        try:
            user_info = self.spotify.me()
            logger.debug("spotify.user.info",
                        message="Retrieved user market information",
                        data={"user_info": user_info})
            return user_info['country']
        except SpotifyException as exc:
            logger.exception("spotify.user.error",
                           message="Failed to get user market",
                           exc=exc)
            return None

    def get_song_markets(self, track_uri: str) -> List[str]:
        try:
            track_info = self.spotify.track(track_uri)
            logger.debug("spotify.track.info",
                        message="Retrieved track market information",
                        data={
                            "track_uri": track_uri,
                            "track_info": track_info
                        })
            return track_info.get('available_markets', []) or []
        except SpotifyException as exc:
            logger.exception("spotify.track.error",
                           message="Failed to get song markets",
                           exc=exc,
                           data={"track_uri": track_uri})
            return []

    def playback_active(self) -> bool:
        try:
            playback_state = self.spotify.current_playback()
            if playback_state and playback_state.get('is_playing'):
                logger.debug("spotify.playback.status",
                           message="Playback is active",
                           data={"is_playing": True})
                return True
            return False
        except SpotifyException as exc:
            logger.exception("spotify.playback.error",
                           message="Error checking playback state",
                           exc=exc)
            return False

    def skip_song(self, silent=False) -> bool:
        try:
            if not silent:
                logger.info("spotify.playback.skip",
                           message="Skipping current track",
                           data={
                               "device_id": self.playback_device
                           })
            self.spotify.next_track(device_id=self.playback_device)
            return True

        except SpotifyException as exc:
            if not silent:
                logger.exception("spotify.playback.error",
                               message="Failed to skip track",
                               exc=exc,
                               data={
                                   "device_id": self.playback_device
                               })
            return False

