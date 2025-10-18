from __future__ import annotations

from io import BytesIO
import json
import urllib.parse
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from .config_store import AppConfig, cache_path
from PIL import Image


USER_AGENT = "JellyfinGTK/0.1 Python"
DEVICE = "JellyfinGTK"
CLIENT = "JellyfinGTK"
VERSION = "0.1.0"


@dataclass
class MediaItem:
    id: str
    name: str
    num_tracks: int
    album: Optional[str] = None
    artist: Optional[str] = None
    type: Optional[str] = None
    image_tag: Optional[str] = None
    album_id: Optional[str] = None
    runtime_ticks: Optional[int] = None
    year: Optional[int] = None
    tracks: Optional[List[TrackItem]] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "num_tracks": self.num_tracks,
            "album": self.album,
            "artist": self.artist,
            "type": self.type,
            "image_tag": self.image_tag,
            "album_id": self.album_id,
            "runtime_ticks": self.runtime_ticks,
            "year": self.year,
            "tracks": [track.to_dict() for track in self.tracks] if self.tracks else None,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "MediaItem":
        # Reconstruct tracks if present
        tracks_data = data.get("tracks")
        tracks: Optional[List[TrackItem]] = None
        if tracks_data:
            tracks = [TrackItem.from_dict(t) for t in tracks_data]
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            num_tracks=data.get("num_tracks", 0),
            album=data.get("album"),
            artist=data.get("artist"),
            type=data.get("type"),
            image_tag=data.get("image_tag"),
            album_id=data.get("album_id"),
            runtime_ticks=data.get("runtime_ticks"),
            year=data.get("year"),
            tracks=tracks,
        )


@dataclass
class TrackItem:
    id: str
    name: str
    track_number: int
    disc_number: Optional[int] = None
    album: Optional[str] = None
    artist: Optional[str] = None
    type: Optional[str] = None
    image_tag: Optional[str] = None
    album_id: Optional[str] = None
    runtime_ticks: Optional[int] = None
    year: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "track_number": self.track_number,
            "disc_number": self.disc_number,
            "album": self.album,
            "artist": self.artist,
            "type": self.type,
            "image_tag": self.image_tag,
            "album_id": self.album_id,
            "runtime_ticks": self.runtime_ticks,
            "year": self.year,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "TrackItem":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            track_number=data.get("track_number", 0),
            disc_number=data.get("disc_number"),
            album=data.get("album"),
            artist=data.get("artist"),
            type=data.get("type"),
            image_tag=data.get("image_tag"),
            album_id=data.get("album_id"),
            runtime_ticks=data.get("runtime_ticks"),
            year=data.get("year"),
        )
    



class JellyfinClient:
    def get_track_stream_url(self, track_id: str) -> str:
        """Return the direct stream URL for a track (audio) item."""
        query = {
            "api_key": self.access_token,
        }
        return self._url(f"/Items/{track_id}/Download", query)
    def __init__(self, server_url: str, access_token: Optional[str] = None, user_id: Optional[str] = None, username: Optional[str] = None):
        self.server_url = server_url.rstrip("/")
        self.access_token = access_token
        self.user_id = user_id
        self.username = username

    # --- HTTP helpers ---
    def _headers(self, with_token: bool = True) -> Dict[str, str]:
        auth = (
            f"MediaBrowser Client={CLIENT}, Device={DEVICE}, DeviceId=local-1, Version={VERSION}"
        )
        headers = {
            "User-Agent": USER_AGENT,
            "X-Emby-Authorization": auth,
            "Accept": "application/json",
        }
        if with_token and self.access_token:
            headers["X-Emby-Token"] = self.access_token
        return headers

    def _url(self, path: str, query: Optional[Dict[str, Any]] = None) -> str:
        base = f"{self.server_url}{path}"
        if query:
            return base + "?" + urllib.parse.urlencode(query)
        return base

    def _request(self, method: str, path: str, *, data: Optional[dict] = None, query: Optional[Dict[str, Any]] = None, with_token: bool = True) -> Any:
        body_bytes = None
        headers = self._headers(with_token=with_token)
        if data is not None:
            body_bytes = json.dumps(data).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(self._url(path, query), data=body_bytes, headers=headers, method=method)
        with urllib.request.urlopen(req) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read()
            if "application/json" in content_type or raw.strip().startswith(b"{"):
                return json.loads(raw.decode("utf-8"))
            return raw

    # --- Auth ---
    def login(self, username: str, password: str) -> AppConfig:
        # POST /Users/AuthenticateByName with { Username, Pw }
        payload = {"Username": username, "Pw": password}
        data = self._request("POST", "/Users/AuthenticateByName", data=payload, with_token=False)
        token = data.get("AccessToken")
        user = data.get("User", {})
        user_id = user.get("Id")
        username = user.get("Name")
        if not token or not user_id:
            raise RuntimeError("Login failed: token or user id missing")
        self.access_token = token
        self.user_id = user_id
        self.username = username
        return AppConfig(server_url=self.server_url, access_token=token, user_id=user_id, username=username)

    # --- Library ---
    def get_album_track_count(self, album_id: str) -> int:
        query = {
            "IncludeItemTypes": "Audio",
            "ParentId": album_id,
            "Recursive": "false",
            "Fields": "Id"
        }
        data = self._request("GET", f"/Users/{self.user_id}/Items", query=query)
        return len(data.get("Items", []))

    def items(self, include_types: List[str], sort_by: str = "SortName") -> List[MediaItem]:
        if not self.user_id:
            raise RuntimeError("Missing user id")
        query = {
            "IncludeItemTypes": ",".join(include_types),
            "SortBy": sort_by,
            "Recursive": "true",
            "Limit": 500,
        }
        data = self._request("GET", f"/Users/{self.user_id}/Items", query=query)
        items = []
        for it in data.get("Items", []):
            album_id = it.get("Id", "")
            items.append(
                MediaItem(
                    id=album_id,
                    name=it.get("Name", ""),
                    num_tracks=self.get_album_track_count(album_id) if "MusicAlbum" in include_types else 0,
                    album=it.get("Album") or it.get("AlbumArtist"),
                    artist=(it.get("AlbumArtist") or (it.get("ArtistItems", [{}])[0].get("Name") if it.get("ArtistItems") else None)),
                    type=it.get("Type"),
                    image_tag=(it.get("ImageTags", {}) or {}).get("Primary"),
                    album_id=it.get("AlbumId"),
                    year=it.get("ProductionYear"),
                    runtime_ticks=it.get("RunTimeTicks"),
                )
            )
        return items
    
    def get_album_tracks(self, album: MediaItem) -> List[TrackItem]:
        if not self.user_id:
            raise RuntimeError("Missing user id")
        if not album.id:
            raise ValueError("Album must have an ID")
        query = {
            "IncludeItemTypes": "Audio",
            "SortBy": "TrackNumber",
            "Recursive": "true",
            "Limit": 500,
            "ParentId": album.id
        }
        data = self._request("GET", f"/Users/{self.user_id}/Items", query=query)
        items = []
        for it in data.get("Items", []):
            # Get track-specific artist information (not album artist)
            # Try multiple fields to find the actual performing artist for this track
            track_artist = None
            
            # Try ArtistItems first (this contains the actual track artists)
            if it.get("ArtistItems") and len(it.get("ArtistItems", [])) > 0:
                track_artist = it.get("ArtistItems")[0].get("Name")
            
            # If no ArtistItems, try Artists field
            if not track_artist and it.get("Artists") and len(it.get("Artists", [])) > 0:
                track_artist = it.get("Artists")[0]
            
            # Fall back to AlbumArtist only if no track-specific artist found
            if not track_artist:
                track_artist = it.get("AlbumArtist")
            
            # Use the track artist we found, or fall back to album artist as last resort
            artist = track_artist or album.artist
            items.append(
                TrackItem(
                    id=it.get("Id", ""),
                    name=it.get("Name", ""),
                    track_number=it.get("IndexNumber", 0),
                    disc_number=it.get("ParentIndexNumber", 0),
                    album=it.get("Album") or it.get("AlbumArtist"),
                    artist=artist,
                    type=it.get("Type"),
                    image_tag=(it.get("ImageTags", {}) or {}).get("Primary"),
                    album_id=it.get("AlbumId"),
                    year=it.get("ProductionYear"),
                    runtime_ticks=it.get("RunTimeTicks"),
                )
            )
        return items

    def image_path(self, item_id: str, image_tag: Optional[str], max_width: int = 256) -> Optional[Path]:
        # Fetch image even without tag; tag is used only to improve cache key stability
        tag_part = image_tag or "notag"
        name = f"{item_id}_{tag_part}_{max_width}.png"
        orig_path = cache_path("images") / name
        # see if an image that size or larger already exists. Do this by checking if the filename has item_id and tag_part
        for p in cache_path("images").glob(f"{item_id}_{tag_part}_*.png"):
            try:
                parts = p.stem.split("_")
                if len(parts) == 3:
                    w = int(parts[2])
                    if w == max_width:
                        return p
                    elif w > max_width:
                        resized_name = f"{item_id}_{tag_part}_{max_width}.png"
                        resized_path = cache_path("images") / resized_name
                        with Image.open(p) as img:
                            img = img.resize((max_width, max_width), Image.Resampling.LANCZOS)
                            img.save(resized_path, "PNG")
                        return resized_path   
            except Exception:
                continue
        q: Dict[str, Any] = {"MaxWidth": str(max_width)}
        if image_tag:
            q["Tag"] = str(image_tag)
            raw = self._request(
            "GET",
            f"/Items/{item_id}/Images/Primary",
            query=q,
            )
            try:
                with Image.open(BytesIO(raw)) as img:
                    img.save(orig_path, "PNG")
                return orig_path
            except Exception:
                return None
