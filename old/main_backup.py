#!/usr/bin/env python3

print('[DEBUG] Top of src/main.py reached')

import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version('Gst', '1.0')

from gi.repository import Gtk, Gio, Adw, GLib, Pango, Gdk, Gst
from .config_store import load_config, save_config, clear_config, cache_dir, clear_cache_excluding_images
from .jellyfin_client import JellyfinClient, MediaItem

APP_ID = "com.example.JellyfinGtkMusic"


class QueueManager:
    """Manages the music playback queue"""
    
    def __init__(self):
        self.queue: List[Dict[str, Any]] = []  # List of track info dictionaries
        self.current_index: int = -1  # Index of currently playing track
        self.repeat_mode: str = "off"  # "off", "one", "all"
        self.shuffle_enabled: bool = False
        self.on_track_changed: Optional[Callable[[Dict[str, Any]], None]] = None
        self.on_queue_finished: Optional[Callable[[], None]] = None
        
    def add_track(self, track_info: Dict[str, Any], play_immediately: bool = False):
        """Add a track to the queue"""
        self.queue.append(track_info)
        print(f"[DEBUG] Added track to queue: {track_info.get('name', 'Unknown')} (queue size: {len(self.queue)})")
        
        if play_immediately:
            self.current_index = len(self.queue) - 1
            self._notify_track_changed()
    
    def add_tracks(self, tracks: List[Dict[str, Any]], play_first: bool = False):
        """Add multiple tracks to the queue"""
        self.queue.extend(tracks)
        print(f"[DEBUG] Added {len(tracks)} tracks to queue (total size: {len(self.queue)})")
        
        if play_first and tracks:
            # Set current index to the first newly added track
            self.current_index = len(self.queue) - len(tracks)
            self._notify_track_changed()
    
    def play_track_at_index(self, index: int):
        """Play a specific track in the queue by index"""
        if 0 <= index < len(self.queue):
            self.current_index = index
            self._notify_track_changed()
            return True
        return False
    
    def next_track(self) -> bool:
        """Move to the next track in the queue"""
        if self.repeat_mode == "one":
            # Repeat current track
            self._notify_track_changed()
            return True
        elif self.current_index < len(self.queue) - 1:
            # Normal next track
            self.current_index += 1
            self._notify_track_changed()
            return True
        elif self.repeat_mode == "all" and self.queue:
            # Loop back to beginning
            self.current_index = 0
            self._notify_track_changed()
            return True
        else:
            # End of queue
            print("[DEBUG] End of queue reached")
            if self.on_queue_finished:
                self.on_queue_finished()
            return False
    
    def previous_track(self) -> bool:
        """Move to the previous track in the queue"""
        if self.current_index > 0:
            self.current_index -= 1
            self._notify_track_changed()
            return True
        elif self.repeat_mode == "all" and self.queue:
            # Loop to end
            self.current_index = len(self.queue) - 1
            self._notify_track_changed()
            return True
        return False
    
    def get_current_track(self) -> Optional[Dict[str, Any]]:
        """Get the currently playing track"""
        if 0 <= self.current_index < len(self.queue):
            return self.queue[self.current_index]
        return None
    
    def clear_queue(self):
        """Clear the entire queue"""
        self.queue.clear()
        self.current_index = -1
        print("[DEBUG] Queue cleared")
    
    def remove_track(self, index: int):
        """Remove a track from the queue by index"""
        if 0 <= index < len(self.queue):
            removed = self.queue.pop(index)
            print(f"[DEBUG] Removed track: {removed.get('name', 'Unknown')}")
            
            # Adjust current index if necessary
            if index < self.current_index:
                self.current_index -= 1
            elif index == self.current_index:
                # Currently playing track was removed
                if self.current_index >= len(self.queue):
                    self.current_index = len(self.queue) - 1
                # Notify about the new current track (or None if queue is empty)
                self._notify_track_changed()
    
    def get_queue_info(self) -> Dict[str, Any]:
        """Get information about the current queue"""
        return {
            "tracks": self.queue,
            "current_index": self.current_index,
            "total_tracks": len(self.queue),
            "repeat_mode": self.repeat_mode,
            "shuffle_enabled": self.shuffle_enabled
        }
    
    def _notify_track_changed(self):
        """Internal method to notify about track changes"""
        current_track = self.get_current_track()
        if current_track and self.on_track_changed:
            self.on_track_changed(current_track)


class QueueUIManager:
    """Manages the queue user interface"""
    
    def __init__(self, builder: Gtk.Builder, queue_manager: QueueManager, ui_manager):
        self.builder = builder
        self.queue_manager = queue_manager
        self.ui_manager = ui_manager  # Reference to main UIManager for callbacks
        
        # Get queue UI elements
        self.queue_list_box: Gtk.ListBox = builder.get_object("queue_list_box")
        
        print(f"[DEBUG] Queue UI Manager initialized, queue list box found: {self.queue_list_box is not None}")
    
    def update_queue_ui(self):
        """Update the queue UI to reflect current queue state"""
        if not self.queue_list_box:
            return
        
        # Clear existing queue items with proper cleanup
        while True:
            row = self.queue_list_box.get_first_child()
            if row is None:
                break
            
            # Manually trigger popover cleanup before removing the row
            self._cleanup_row_popover(row)
            
            # Remove the row from the list box
            self.queue_list_box.remove(row)
        
        # Add current queue tracks
        queue_info = self.queue_manager.get_queue_info()
        for i, track in enumerate(queue_info["tracks"]):
            queue_row = self._create_queue_row(track, i, i == queue_info["current_index"])
            self.queue_list_box.append(queue_row)
        
        print(f"[DEBUG] Updated queue UI with {len(queue_info['tracks'])} tracks")
    
    def scroll_to_current_track(self):
        """Scroll the queue list to show the currently playing track"""
        if not self.queue_list_box:
            return
            
        queue_info = self.queue_manager.get_queue_info()
        current_index = queue_info["current_index"]
        
        if current_index >= 0:
            # Get the row for the current track
            row_count = 0
            child = self.queue_list_box.get_first_child()
            while child and row_count < current_index:
                child = child.get_next_sibling()
                row_count += 1
            
            if child:
                # Scroll to the current track row
                try:
                    # Get the scrolled window that contains the queue list
                    parent = self.queue_list_box.get_parent()
                    while parent and not isinstance(parent, Gtk.ScrolledWindow):
                        parent = parent.get_parent()
                    
                    if parent and isinstance(parent, Gtk.ScrolledWindow):
                        # Calculate the position to scroll to
                        allocation = child.get_allocation()
                        list_allocation = self.queue_list_box.get_allocation()
                        
                        # Scroll to center the current track in view
                        vadj = parent.get_vadjustment()
                        if vadj:
                            # Calculate target position (center the row)
                            target_value = allocation.y - (vadj.get_page_size() / 2) + (allocation.height / 2)
                            # Clamp to valid range
                            target_value = max(vadj.get_lower(), min(target_value, vadj.get_upper() - vadj.get_page_size()))
                            vadj.set_value(target_value)
                            
                        print(f"[DEBUG] Scrolled queue to current track (index {current_index})")
                    else:
                        print("[DEBUG] Could not find scrolled window for queue")
                except Exception as e:
                    print(f"[DEBUG] Error scrolling to current track: {e}")
    
    def _cleanup_row_popover(self, row):
        """Clean up popover associated with a row"""
        if hasattr(row, "_context_menu_popover"):
            popover = getattr(row, "_context_menu_popover")
            if popover:
                try:
                    # Properly destroy the popover
                    popover.unparent()
                    print(f"[DEBUG] Cleaned up popover for row")
                except Exception as e:
                    print(f"[DEBUG] Error cleaning up popover: {e}")
                setattr(row, "_context_menu_popover", None)
    
    def _create_queue_row(self, track_info: Dict[str, Any], index: int, is_current: bool) -> Gtk.ListBoxRow:
        """Create a queue row widget for a track using a UI template"""
        builder = Gtk.Builder()
        builder.add_from_file(str(Path(__file__).parent.parent / "ui" / "queue_row.ui"))
        box = builder.get_object("queue_row_box")
        icon = builder.get_object("queue_row_icon")
        title_label = builder.get_object("queue_row_title")
        artist_label = builder.get_object("queue_row_artist")
        duration_label = builder.get_object("queue_row_duration")

        # Track number/playing indicator
        if is_current:
            icon.set_from_icon_name("media-playback-start")
            icon.set_css_classes(["accent"])
        else:
            icon.set_from_icon_name("audio-x-generic")
            album_art_path = self.ui_manager.current_album.get("art_path") if self.ui_manager.current_album else None
            if album_art_path and Path(album_art_path).exists():
                try:
                    texture = Gdk.Texture.new_from_filename(album_art_path)
                    icon.set_from_paintable(texture)
                except Exception:
                    pass
        icon.set_size_request(32, -1)

        # Track info
        title_label.set_text(track_info.get("name", "Unknown Track"))
        title_label.set_halign(Gtk.Align.START)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        if is_current:
            title_label.set_css_classes(["heading"])
        artist_label.set_text(track_info.get("artist", "Unknown Artist"))
        artist_label.set_halign(Gtk.Align.START)
        artist_label.set_ellipsize(Pango.EllipsizeMode.END)
        artist_label.set_css_classes(["caption", "dim-label"])

        # Duration
        duration_ticks = track_info.get("runtime_ticks")
        if duration_ticks:
            duration_str = self.ui_manager._format_duration(duration_ticks)
            duration_label.set_text(duration_str)
        else:
            duration_label.set_text("")

        row = Gtk.ListBoxRow()
        row.set_child(box)
        setattr(row, "track_index", index)

        # Connect row activation to play track
        row.connect("activate", lambda _: self._play_queue_track(index))

        # Add left-click gesture to ensure reliable track playing
        left_click_gesture = Gtk.GestureClick.new()
        left_click_gesture.set_button(1)  # Left mouse button
        left_click_gesture.connect("pressed", lambda gesture, n_press, x, y: self._play_queue_track(index))
        row.add_controller(left_click_gesture)

        # Add right-click context menu
        self._add_queue_context_menu(row, index, track_info)

        return row
    
    def _play_queue_track(self, index: int):
        """Play a specific track from the queue"""
        print(f"[DEBUG] Left-clicked queue track at index {index}")
        if self.queue_manager.play_track_at_index(index):
            print(f"[DEBUG] Successfully switched to track at index {index}")
            self.update_queue_ui()
        else:
            print(f"[DEBUG] Failed to switch to track at index {index}")
    
    def _remove_track_from_queue(self, index: int):
        """Remove a track from the queue by index"""
        self.queue_manager.remove_track(index)
        self.update_queue_ui()

    def _move_track_to_next(self, index: int):
        """Move a track to be next in the queue"""
        queue_info = self.queue_manager.get_queue_info()
        current_index = queue_info["current_index"]
        
        if current_index >= 0 and index != current_index and index != current_index + 1:
            # Get the track to move
            track_to_move = queue_info["tracks"][index]
            
            # Remove from current position
            self.queue_manager.remove_track(index)
            
            # Adjust target position if we removed from before current track
            next_position = current_index + 1 if index > current_index else current_index
            
            # Insert at next position
            self.queue_manager.queue.insert(next_position, track_to_move)
            
            print(f"[DEBUG] Moved track '{track_to_move.get('name', 'Unknown')}' to play next")
            self.update_queue_ui()
    
    def _add_queue_context_menu(self, row: Gtk.ListBoxRow, index: int, track_info: Dict[str, Any]):
        """Add a right-click context menu to a queue row"""
        # Create the popover menu
        menu_model = Gio.Menu()
        
        # Add menu items
        menu_model.append("Remove from Queue", f"queue.remove.{index}")
        menu_model.append("Play Next", f"queue.play_next.{index}")
        
        # Create the popover
        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        popover.set_parent(row)
        popover.set_has_arrow(False)

        # Store popover reference on the row for cleanup
        setattr(row, "_context_menu_popover", popover)
        
        # Connect row destruction to popover cleanup
        row.connect("destroy", lambda r: self._cleanup_row_popover(r))

        # Create action group for this specific queue item
        action_group = Gio.SimpleActionGroup()
        
        # Remove action
        remove_action = Gio.SimpleAction.new(f"remove.{index}", None)
        remove_action.connect("activate", lambda action, param: self._remove_track_from_queue(index))
        action_group.add_action(remove_action)
        
        # Play next action (move track to be next in queue)
        play_next_action = Gio.SimpleAction.new(f"play_next.{index}", None)
        play_next_action.connect("activate", lambda action, param: self._move_track_to_next(index))
        action_group.add_action(play_next_action)
        
        # Insert the action group
        row.insert_action_group("queue", action_group)
        
        # Create gesture for right-click
        gesture = Gtk.GestureClick.new()
        gesture.set_button(3)  # Right mouse button
        
        def on_right_click(gesture, n_press, x, y):
            # Create a rectangle at the click position for the popover to point to
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            
            # Set the pointing rectangle and popup
            popover.set_pointing_to(rect)
            popover.popup()
        
        gesture.connect("pressed", on_right_click)
        row.add_controller(gesture)


class LibraryManager:
    """Manages music library data and caching"""
    
    def __init__(self):
        self.client: Optional[JellyfinClient] = None
        self.albums_data: List[Dict[str, Any]] = []
        self.tracks_data: List[Dict[str, Any]] = []
        self.artists_data: List[Dict[str, Any]] = []
        self.album_tracks_cache: Dict[str, List[Dict[str, Any]]] = {}
        
    def set_client(self, client: JellyfinClient):
        """Set the Jellyfin client"""
        self.client = client
        
    def load_cached_data(self) -> bool:
        """Load cached library data from file"""
        try:
            import json
            cache_file = cache_dir() / "library.json"
            if cache_file.exists():
                with open(cache_file, "r") as f:
                    data = json.load(f)
                    self.albums_data = data.get("albums", [])
                    self.tracks_data = data.get("tracks", [])
                    self.artists_data = data.get("artists", [])
                    self.album_tracks_cache = data.get("album_tracks", {})
                    return True
        except Exception as e:
            print(f"[ERROR] Failed to load cached data: {e}")
        return False
        
    def save_cached_data(self):
        """Save library data to cache file"""
        try:
            import json
            cache_file = cache_dir() / "library.json"
            cache_file.parent.mkdir(parents=True, exist_ok=True)
            
            data = {
                "albums": self.albums_data,
                "tracks": self.tracks_data,
                "artists": self.artists_data,
                "album_tracks": self.album_tracks_cache,
            }
            
            with open(cache_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            print(f"[ERROR] Failed to save cached data: {e}")
            
    def fetch_library_from_server(self) -> bool:
        """Fetch library data from Jellyfin server"""
        if not self.client:
            return False
            
        try:
            print("[DEBUG] Fetching albums from server...")
            albums = self.client.items(["MusicAlbum"], sort_by="AlbumArtist,Album,SortName")
            
            print("[DEBUG] Fetching tracks from server...")  
            tracks = self.client.items(["Audio"], sort_by="AlbumArtist,Album,SortName")
            
            print("[DEBUG] Fetching artists from server...")
            artists = self.client.items(["MusicArtist"], sort_by="SortName")
            
            # Process albums
            self.albums_data = []
            for a in albums:
                album_id = getattr(a, "id", "") or ""
                try:
                    img_path = None
                    if album_id and hasattr(a, "image_tag"):
                        img = self.client.image_path(album_id, a.image_tag, max_width=512)
                        img_path = str(img) if img else None
                except Exception:
                    img_path = None
                    
                self.albums_data.append({
                    "id": str(album_id),
                    "name": str(getattr(a, "name", "") or ""),
                    "artist": str(getattr(a, "artist", "") or ""),
                    "art_path": img_path,
                    "year": int(getattr(a, "year", 0) or 0),
                    "num_tracks": int(getattr(a, "num_tracks", 0) or 0),
                    "runtime_ticks": int(getattr(a, "runtime_ticks", 0) or 0),
                })
            
            # Process tracks
            self.tracks_data = []
            for t in tracks:
                self.tracks_data.append({
                    "id": str(getattr(t, "id", "") or ""),
                    "name": str(getattr(t, "name", "") or ""),
                    "artist": str(getattr(t, "artist", "") or ""),
                    "album": str(getattr(t, "album", "") or ""),
                    "album_id": str(getattr(t, "album_id", "") or ""),
                    "runtime_ticks": int(getattr(t, "runtime_ticks", 0) or 0),
                    "year": int(getattr(t, "year", 0) or 0),
                })
            
            # Process artists
            self.artists_data = []
            for ar in artists:
                self.artists_data.append({
                    "id": str(getattr(ar, "id", "") or ""),
                    "name": str(getattr(ar, "name", "") or ""),
                })
            
            # Cache album tracks
            self.album_tracks_cache = {}
            for a in albums:
                try:
                    tracks_result = self.client.get_album_tracks(a)
                    if tracks_result and isinstance(tracks_result, list):
                        self.album_tracks_cache[a.id] = [t.__dict__ for t in tracks_result]
                    else:
                        self.album_tracks_cache[a.id] = []
                except Exception as e:
                    print(f"[ERROR] Failed to get tracks for album {getattr(a, 'name', 'Unknown')}: {e}")
                    self.album_tracks_cache[a.id] = []
            
            # Save to cache
            self.save_cached_data()
            return True
            
        except Exception as e:
            print(f"[ERROR] Failed to fetch library from server: {e}")
            return False
    
    def get_album_tracks(self, album_id: str) -> List[Dict[str, Any]]:
        """Get tracks for a specific album"""
        return self.album_tracks_cache.get(album_id, [])


class UIManager:
    """Manages UI components and interactions"""
    
    def __init__(self, builder: Gtk.Builder, library_manager: LibraryManager, media_player):
        try:
            self.builder = builder
            self.library_manager = library_manager
            self.media_player = media_player
            self.window: Adw.ApplicationWindow = builder.get_object("main_window")
            
            # Initialize queue manager
            self.queue_manager = QueueManager()
            self.queue_manager.on_track_changed = self._on_queue_track_changed
            self.queue_manager.on_queue_finished = self._on_queue_finished
            
            # Initialize queue UI manager
            self.queue_ui_manager = QueueUIManager(builder, self.queue_manager, self)
            
            # Get UI elements
            self.library_stack: Gtk.Stack = builder.get_object("library_stack")
            self.albums_flow: Gtk.FlowBox = builder.get_object("albums_flow")
            self.tracks_list: Gtk.ListBox = builder.get_object("tracks_list")
            self.artists_list: Gtk.ListBox = builder.get_object("artists_list")
            
            # Album info elements
            self.album_info_art: Gtk.Image = builder.get_object("album_view_album_art")
            self.album_info_title: Gtk.Label = builder.get_object("album_view_title_label")
            self.album_info_artist: Gtk.Label = builder.get_object("album_view_artist_label")
            self.album_info_additional: Gtk.Label = builder.get_object("album_view_additional_label")
            self.album_info_box: Gtk.Box = builder.get_object("album_info_box")
            self.album_view_play_button: Gtk.Button = builder.get_object("album_view_play_button")

            # Login dialog elements
            self.login_dialog: Gtk.Dialog = builder.get_object("login_dialog")
            self.server_address: Adw.EntryRow = builder.get_object("entry_server")
            self.entry_username: Adw.EntryRow = builder.get_object("entry_username")
            self.entry_password: Adw.PasswordEntryRow = builder.get_object("entry_password")
            self.login_error: Gtk.Label = builder.get_object("login_error")
            self.btn_login: Gtk.Button = builder.get_object("btn_login")

            self.now_playing: Adw.BottomSheet = builder.get_object("now_playing")
            self.now_playing_content_page: Adw.NavigationPage = builder.get_object("now_playing_content_page")

            self.play_pause_btn: Gtk.Button = builder.get_object("play_pause_btn")
            self.previous_track_btn: Gtk.Button = builder.get_object("previous_track_btn")
            self.next_track_btn: Gtk.Button = builder.get_object("next_track_btn")
            
            # Now playing bar elements (bottom bar) - using direct IDs
            self.now_playing_album_art: Gtk.Image = builder.get_object("now_playing_art")
            self.now_playing_title: Gtk.Label = builder.get_object("now_playing_song_title")
            self.now_playing_artist: Gtk.Label = builder.get_object("now_playing_artist")
            self.now_playing_album: Gtk.Label = builder.get_object("now_playing_album_title")
            self.song_duration_label: Gtk.Label = builder.get_object("song_duration_label")
            self.time_remaining_label: Gtk.Label = builder.get_object("time_remaining_label")
            self.progress_scale: Gtk.Scale = builder.get_object("now_playing_progress_bar")
            self.progress_adjustment: Gtk.Adjustment = builder.get_object("now_playing_adjustment")

            # Now playing sheet elements (sidebar/expanded view)
            self.sheet_album_art: Gtk.Image = builder.get_object("sheet_album_art")
            self.sheet_song_title: Gtk.Label = builder.get_object("sheet_song_title")
            self.sheet_artist: Gtk.Label = builder.get_object("sheet_artist")
            self.sheet_album_title: Gtk.Label = builder.get_object("sheet_album_title")
            self.sheet_song_duration_label: Gtk.Label = builder.get_object("sheet_song_duration_label")
            self.sheet_time_remaining_label: Gtk.Label = builder.get_object("sheet_time_remaining_label")
            self.sheet_progress_scale: Gtk.Scale = builder.get_object("sheet_now_playing_progress_bar")
            # window.ui uses id "sheet_adjustment" for the sheet's GtkAdjustment
            self.sheet_adjustment: Gtk.Adjustment = builder.get_object("sheet_adjustment")
            # Queue management elements
            self.queue_list_box: Gtk.ListBox = builder.get_object("queue_list_box")
            
            # Debug: Check if elements were found
            print(f"[DEBUG] Duration label found: {self.song_duration_label is not None}")
            print(f"[DEBUG] Time remaining label found: {self.time_remaining_label is not None}")
            print(f"[DEBUG] Progress scale found: {self.progress_scale is not None}")
            print(f"[DEBUG] Progress adjustment found: {self.progress_adjustment is not None}")
            print(f"[DEBUG] Queue list box found: {self.queue_list_box is not None}")
            
            # Configure adjustments for smooth progress (0.1 second increments)
            if self.progress_adjustment:
                self.progress_adjustment.set_step_increment(0.001)
                self.progress_adjustment.set_page_increment(10.0)  # 10 seconds for page up/down
            if self.sheet_adjustment:
                self.sheet_adjustment.set_step_increment(0.001)
                self.sheet_adjustment.set_page_increment(10.0)
            
            # Connect progress bars for scrubbing happens in _setup_ui_signals
            
            # Track current playing info
            self.current_track: Optional[Dict[str, Any]] = None
            self.current_album: Optional[Dict[str, Any]] = None
            self.progress_timer_id: Optional[int] = None
            self.updating_progress: bool = False  # Flag to prevent feedback loops
            self.user_seeking: bool = False  # Flag to prevent conflicts during scrubbing
            self.debug_counter: int = 0  # Counter for reduced debug output
            
            # Set now playing bottom sheet to take up the full height of the window when opened
            self._setup_bottom_sheet_height()
            
            # Connect signals
            self._setup_ui_signals()
            
            print(f"[DEBUG] sheet_album_art: {self.sheet_album_art}")
            print(f"[DEBUG] sheet_song_title: {self.sheet_song_title}")
            print(f"[DEBUG] sheet_artist: {self.sheet_artist}")
            print(f"[DEBUG] sheet_album_title: {self.sheet_album_title}")
            print(f"[DEBUG] sheet_song_duration_label: {self.sheet_song_duration_label}")
            print(f"[DEBUG] sheet_time_remaining_label: {self.sheet_time_remaining_label}")
            print(f"[DEBUG] sheet_progress_scale: {self.sheet_progress_scale}")
            print(f"[DEBUG] sheet_adjustment: {self.sheet_adjustment}")
        except Exception as e:
            import traceback
            print("[ERROR] Exception in UIManager.__init__:", e)
            traceback.print_exc()
            raise

    def _setup_ui_signals(self):
        """Setup UI signal connections"""
        self.albums_flow.connect("child-activated", self._on_album_activated)
        # Back button
        back_button = self.builder.get_object("back_button")
        if back_button:
            back_button.connect("clicked", lambda _: self._show_albums_view())
        # Refresh button
        refresh_button = self.builder.get_object("refresh_button")
        if refresh_button:
            refresh_button.connect("clicked", lambda _: self._on_refresh_clicked())
        open_now_playing: Gtk.Button = self.builder.get_object("open_now_playing")
        if open_now_playing:
            open_now_playing.connect("clicked", lambda _: self.now_playing.set_open(True))

        # Previous/Next track buttons
        if self.previous_track_btn:
            self.previous_track_btn.connect("clicked", lambda _: self._on_previous_track_clicked())
        if self.next_track_btn:
            self.next_track_btn.connect("clicked", lambda _: self._on_next_track_clicked())

        # Album play button
        if self.album_view_play_button:
            self.album_view_play_button.connect("clicked", lambda _: self._on_album_play_button_clicked())

        # Connect both progress bars for scrubbing
        if self.progress_scale:
            self.progress_scale.connect("value-changed", self._on_progress_changed)
        if self.sheet_progress_scale:
            self.sheet_progress_scale.connect("value-changed", self._on_progress_changed)

        # Connect both progress bars for scrubbing
        if self.progress_scale:
            self.progress_scale.connect("value-changed", self._on_progress_changed)
        if self.sheet_progress_scale:
            self.sheet_progress_scale.connect("value-changed", self._on_progress_changed)
    
    def _setup_bottom_sheet_height(self):
        """Configure the bottom sheet to take up full window height"""
        if not self.now_playing or not self.window:
            return
        
        def update_sheet_height():
            # Get the window height
            window_height = self.window.get_height()
            
            # Calculate desired sheet height (full window minus small margin for handle)
            sheet_height = max(400, window_height - 46)  # Minimum 400px, or window height minus handle
            
            # Try different property names based on libadwaita version
            try:
                # Try the newer property name
                # Try setting via property
                if hasattr(self.now_playing_content_page, 'set_property'):
                    self.now_playing_content_page.set_property('height-request', sheet_height)
            except Exception as e:
                print(f"[DEBUG] Error setting sheet height: {e}")
        
        # Set initial height after a delay to ensure window is properly sized
        GLib.timeout_add(100, update_sheet_height)
        
        # Connect to window resize events to update sheet height dynamically
        def on_window_size_change(*args):
            GLib.timeout_add(0, update_sheet_height)
            
        self.window.connect("notify::default-height", on_window_size_change)
        
        def on_sheet_open_changed(widget, param):
            if widget.get_open():
                update_sheet_height()
                # Scroll to current track when opening the sheet
                if hasattr(self, 'queue_ui_manager'):
                    GLib.timeout_add(100, self.queue_ui_manager.scroll_to_current_track)  # Small delay to ensure UI is ready
            else:
                widget.set_property('height-request', 0)
        
        self.now_playing.connect("notify::open", on_sheet_open_changed)

    def update_now_playing(self, track_info: Dict[str, Any], album_info: Optional[Dict[str, Any]] = None):
        try:
            self.current_track = track_info

            # --- Update both bar and sheet ---
            # Song title
            if self.now_playing_title:
                self.now_playing_title.set_text(track_info.get("name", "Unknown Track"))
            if self.sheet_song_title:
                self.sheet_song_title.set_text(track_info.get("name", "Unknown Track"))

            # Artist
            if self.now_playing_artist:
                self.now_playing_artist.set_text(track_info.get("artist", "Unknown Artist"))
            if self.sheet_artist:
                self.sheet_artist.set_text(track_info.get("artist", "Unknown Artist"))

            # Album
            if self.now_playing_album:
                self.now_playing_album.set_text(track_info.get("album", "Unknown Album"))
            if self.sheet_album_title:
                self.sheet_album_title.set_text(track_info.get("album", "Unknown Album"))

            # Album art
            art_path = track_info.get("art_path")
            # Fallback to current album art if track art is missing
            if (not art_path or not Path(art_path).exists()) and self.current_album:
                album_art_path = self.current_album.get("art_path")
                if album_art_path and Path(album_art_path).exists():
                    art_path = album_art_path
            if art_path and Path(art_path).exists():
                try:
                    texture = Gdk.Texture.new_from_filename(art_path)
                    self.now_playing_album_art.set_from_paintable(texture)
                    if self.sheet_album_art:
                        self.sheet_album_art.set_from_paintable(texture)
                except Exception:
                    self.now_playing_album_art.set_from_icon_name("media-optical-dvd")
                    if self.sheet_album_art:
                        self.sheet_album_art.set_from_icon_name("media-optical-dvd")
            else:
                self.now_playing_album_art.set_from_icon_name("media-optical-dvd")
                if self.sheet_album_art:
                    self.sheet_album_art.set_from_icon_name("media-optical-dvd")

            # Duration
            duration_ticks = track_info.get("runtime_ticks")
            duration_str = self._format_duration(duration_ticks)
            if self.song_duration_label:
                self.song_duration_label.set_text(duration_str)
            if self.sheet_song_duration_label:
                self.sheet_song_duration_label.set_text(duration_str)
            if self.time_remaining_label:
                self.time_remaining_label.set_text("-")
            if self.sheet_time_remaining_label:
                self.sheet_time_remaining_label.set_text("-")
            # Progress bar/adjustment sync
            upper = duration_ticks // 10000000 if duration_ticks else 0
            if self.progress_adjustment:
                self.progress_adjustment.set_upper(upper)
            if self.sheet_adjustment:
                self.sheet_adjustment.set_upper(upper)
            if self.progress_scale:
                self.progress_scale.set_value(0)
            if self.sheet_progress_scale:
                self.sheet_progress_scale.set_value(0)
        except Exception as e:
            import traceback
            print("[ERROR] Exception in update_now_playing:", e)
            traceback.print_exc()
            raise
    
    def _start_progress_timer(self):
        """Start a timer to update the progress bar and sheet widgets"""
        if self.progress_timer_id:
            GLib.source_remove(self.progress_timer_id)

        def update_progress():
            if not self.current_track or not self.media_player:
                print("[DEBUG] Progress update stopped: no track or media player")
                return False

            # Get current position and duration from media player
            current_pos = self.media_player.get_position()
            duration = self.media_player.get_duration()
            is_playing = self.media_player.is_playing()

            # Fallback to track metadata if GStreamer duration not available yet
            if duration == 0:
                runtime_ticks = self.current_track.get("runtime_ticks", 0)
                if runtime_ticks > 0:
                    duration = runtime_ticks // 10_000_000
                    print(f"[DEBUG] Using metadata duration: {duration}s")

            if duration > 0:
                # Update progress scale using the adjustment directly (but not while user is seeking)
                if self.progress_adjustment and not self.user_seeking:
                    self.updating_progress = True  # Prevent feedback loop
                    self.progress_adjustment.set_upper(duration)
                    self.progress_adjustment.set_value(current_pos)
                    self.updating_progress = False
                if self.sheet_adjustment and not self.user_seeking:
                    self.updating_progress = True
                    self.sheet_adjustment.set_upper(duration)
                    self.sheet_adjustment.set_value(current_pos)
                    self.updating_progress = False

                # Update time remaining
                remaining = max(0, duration - current_pos)
                remaining_str = self._format_duration(int(remaining * 10_000_000))
                if self.time_remaining_label:
                    self.time_remaining_label.set_text(f"-{remaining_str}")
                if self.sheet_time_remaining_label:
                    self.sheet_time_remaining_label.set_text(f"-{remaining_str}")

                # Update duration label if needed
                duration_str = self._format_duration(int(duration * 10_000_000))
                if self.song_duration_label:
                    self.song_duration_label.set_text(duration_str)
                if self.sheet_song_duration_label:
                    self.sheet_song_duration_label.set_text(duration_str)

            # Continue timer - keep running even if not playing to catch state changes
            # Only stop if we've reached the end or there's an error
            if duration > 0 and current_pos >= duration:
                print("[DEBUG] Track finished, attempting to play next track")
                # Try to play next track in queue
                if not self.queue_manager.next_track():
                    print("[DEBUG] No more tracks in queue, stopping progress timer")
                    return False
                # If we successfully moved to next track, continue timer
                return True

            return True  # Always continue for now to debug

        # Update every 100ms for smooth progress bar animation
        self.progress_timer_id = GLib.timeout_add(100, update_progress)
    
    def _on_progress_changed(self, scale):
        """Handle user scrubbing through either progress bar (bar or sheet)"""
        if not self.media_player or not self.current_track or self.updating_progress:
            return  # Ignore changes when we're updating programmatically

        # Set seeking flag to prevent timer conflicts during user interaction
        self.user_seeking = True

        # Get the new position from the scale
        new_position = scale.get_value()

        print(f"[DEBUG] User scrubbed to position: {new_position:.1f}s")

        # Seek to the new position (MediaPlayer expects position in seconds as int)
        self.media_player.set_position(int(new_position))

        # Sync both progress bars to the new value
        self.updating_progress = True
        try:
            if self.progress_scale and scale != self.progress_scale:
                self.progress_scale.set_value(new_position)
            if self.sheet_progress_scale and scale != self.sheet_progress_scale:
                self.sheet_progress_scale.set_value(new_position)
        finally:
            self.updating_progress = False

        # Clear seeking flag after a short delay to allow position updates
        def clear_seeking_flag():
            self.user_seeking = False
            print("[DEBUG] Cleared user seeking flag")
            return False

        GLib.timeout_add(500, clear_seeking_flag)  # Increased delay

    # (connections setup is handled in _setup_ui_signals)
    
    def show_loading(self):
        """Show the loading page"""
        self.library_stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.library_stack.set_visible_child_name("loading")
        self.library_stack.set_sensitive(False)
        
    def show_albums(self):
        """Show the albums page"""
        self.library_stack.set_visible_child_name("albums")
        self.library_stack.set_sensitive(True)
        self._hide_back_button()
        
    def show_album_info(self, album: Dict[str, Any]):
        """Show album info page"""
        self.library_stack.set_visible_child_name("album_info")
        self._show_back_button()
        self._populate_album_info(album)
        
    def _show_albums_view(self):
        """Switch to albums view"""
        self.show_albums()
        
    def _show_back_button(self):
        """Show the back button"""
        back_button = self.builder.get_object("back_button")
        if back_button:
            back_button.set_visible(True)
            
    def _hide_back_button(self):
        """Hide the back button"""
        back_button = self.builder.get_object("back_button")
        if back_button:
            back_button.set_visible(False)
            
    def _on_refresh_clicked(self):
        """Handle refresh button click"""
        # This would trigger a library refresh
        # For now, just repopulate from cached data
        self.populate_albums()

    def show_login_dialog(self):
        """Show the login dialog"""
        self.login_error.set_text("")
        self.entry_username.set_text("")
        self.entry_password.set_text("")
        self.server_address.set_text("")
        # connect login button
        app = self.window.get_application()
        if app:
            self.btn_login.connect("clicked", lambda btn: app._on_login_clicked())
        self.login_dialog.present()
        
    def _on_album_activated(self, flowbox, child):
        """Handle album selection"""
        try:
            index = child.get_index()
            if 0 <= index < len(self.library_manager.albums_data):
                album = self.library_manager.albums_data[index]
                self.show_album_info(album)
        except Exception as e:
            print(f"[ERROR] Failed to handle album activation: {e}")
    
    def populate_albums(self):
        """Populate the albums flow box"""
        # Clear existing albums
        child = self.albums_flow.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            self.albums_flow.remove(child)
            child = next_child
            
        # Add albums
        for album in self.library_manager.albums_data:
            tile = self._create_album_tile(album)
            self.albums_flow.append(tile)
            
    def _create_album_tile(self, album: Dict[str, Any]) -> Gtk.Widget:
        """Create an album tile widget using a UI template"""
        builder = Gtk.Builder()
        builder.add_from_file(str(Path(__file__).parent.parent / "ui" / "album_tile.ui"))
        box: Gtk.Box = builder.get_object("album_tile_box")
        art: Gtk.Image = builder.get_object("album_tile_art")
        title: Gtk.Label = builder.get_object("album_tile_title")
        artist: Gtk.Label = builder.get_object("album_tile_artist")

        # Populate
        art_path = album.get("art_path")
        if art_path and Path(art_path).exists():
            try:
                texture = Gdk.Texture.new_from_filename(art_path)
                art.set_from_paintable(texture)
            except Exception:
                art.set_from_icon_name("media-optical-dvd")
        else:
            art.set_from_icon_name("media-optical-dvd")

        title.set_text(album.get("name", "Unknown Album"))
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_max_width_chars(24)
        title.set_justify(Gtk.Justification.CENTER)

        artist.set_text(album.get("artist", "Unknown Artist"))
        artist.set_ellipsize(Pango.EllipsizeMode.END)
        artist.add_css_class("dim-label")
        artist.add_css_class("caption")

        return box
        
    def _populate_album_info(self, album: Dict[str, Any]):
        """Populate the album info page"""
        # Store current album context
        self.current_album = album
        
        # Set album art
        art_path = album.get("art_path")
        if art_path and Path(art_path).exists():
            try:
                texture = Gdk.Texture.new_from_filename(art_path)
                self.album_info_art.set_from_paintable(texture)
            except Exception:
                self.album_info_art.set_from_icon_name("media-optical-dvd")
        else:
            self.album_info_art.set_from_icon_name("media-optical-dvd")
            
        # Set labels
        self.album_info_title.set_text(album.get("name", "Unknown Album"))
        self.album_info_artist.set_text(album.get("artist", "Unknown Artist"))
        
        # Additional info
        year = album.get("year", 0)
        num_tracks = album.get("num_tracks", 0)
        runtime = self._format_duration(album.get("runtime_ticks"))
        
        additional_info = f"{year} • {num_tracks} tracks"
        if runtime:
            additional_info += f" • {runtime}"
        self.album_info_additional.set_text(additional_info)
        
        # Get and display tracks
        album_id = album.get("id", "")
        tracks = self.library_manager.get_album_tracks(album_id)
        self._populate_track_listing(tracks)
        
    def _populate_track_listing(self, tracks: List[Dict[str, Any]]):
        """Populate the track listing in album info with multi-disc support"""
        # Clear existing track listings and disc labels
        child = self.album_info_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            # Remove any existing track listings or disc labels
            if (isinstance(child, Gtk.ListBox) and child.get_name() == "track_listing") or \
               (isinstance(child, Gtk.Label) and child.get_name() == "disc_label"):
                self.album_info_box.remove(child)
            child = next_child
        
        if not tracks:
            return
                
        # Sort tracks by disc_number and track_number
        tracks = sorted(tracks, key=lambda t: (
            int(t.get('disc_number') or t.get('ParentIndexNumber') or 1),
            int(t.get('track_number') or t.get('IndexNumber') or 0)
        ))
        
        # Group tracks by disc
        discs = {}
        for track in tracks:
            disc_num = int(track.get('disc_number') or track.get('ParentIndexNumber') or 1)
            if disc_num not in discs:
                discs[disc_num] = []
            discs[disc_num].append(track)
        
        # Store tracks for lookup
        self._current_tracks = tracks
        
        # Check if we have multiple discs
        has_multiple_discs = len(discs) > 1
        
        # Create track listings for each disc
        for disc_num in sorted(discs.keys()):
            disc_tracks = discs[disc_num]
            
            # Add disc label if there are multiple discs
            if has_multiple_discs:
                disc_label = Gtk.Label()
                disc_label.set_name("disc_label")
                disc_label.set_text(f"Disc {disc_num}")
                disc_label.set_halign(Gtk.Align.START)
                disc_label.add_css_class("heading")
                disc_label.set_margin_top(16 if disc_num > 1 else 8)
                disc_label.set_margin_bottom(8)
                disc_label.set_margin_start(12)
                self.album_info_box.append(disc_label)
            
            # Create track list for this disc
            disc_track_list = Gtk.ListBox()
            disc_track_list.set_name("track_listing")
            disc_track_list.add_css_class("boxed-list")
            disc_track_list.set_selection_mode(Gtk.SelectionMode.NONE)
            
            # Add tracks for this disc
            for track in disc_tracks:
                track_num = int(track.get('track_number') or track.get('IndexNumber') or 0)

                # Build row from UI template
                t_builder = Gtk.Builder()
                t_builder.add_from_file(str(Path(__file__).parent.parent / "ui" / "track_row.ui"))
                row_box: Gtk.Box = t_builder.get_object("track_row_box")
                num_label: Gtk.Label = t_builder.get_object("track_row_number")
                name_label: Gtk.Label = t_builder.get_object("track_row_title")
                duration_label: Gtk.Label = t_builder.get_object("track_row_duration")

                num_label.set_text(str(track_num))
                name_label.set_text(track.get("name", "Unknown Track"))
                name_label.set_ellipsize(Pango.EllipsizeMode.END)
                name_label.set_hexpand(True)
                name_label.set_halign(Gtk.Align.START)
                duration = self._format_duration(track.get("runtime_ticks") or track.get("RunTimeTicks"))
                duration_label.set_text(duration)

                # Create ListBoxRow and store track id as data
                row = Gtk.ListBoxRow()
                row.set_child(row_box)
                row.set_selectable(True)
                row.set_activatable(True)
                row.track_id = track.get("id") or track.get("Id")
                disc_track_list.append(row)
            
            # Connect row-activated signal for this disc's track list
            handler_id = disc_track_list.connect("row-activated", self._on_track_row_activated)
            disc_track_list._row_activated_handler_id = handler_id
            
            # Add the track list to the album info box
            self.album_info_box.append(disc_track_list)

    def _on_track_row_activated(self, listbox, row):
        """Handle activating a track row to add entire album to queue and start from clicked track"""
        print("[DEBUG] Track row activated")
        track_id = getattr(row, "track_id", None)
        print(f"[DEBUG] Activated track_id: {track_id}")
        if not track_id:
            print("[DEBUG] No track_id on row")
            return
        
        # Find the track info from the current album
        track_info = None
        if self.current_album:
            album_id = self.current_album.get("id")
            if album_id:
                tracks = self.library_manager.get_album_tracks(album_id)
                # Sort tracks to ensure proper order
                tracks = self._sort_tracks_by_number(tracks)
                
                for track in tracks:
                    if track.get("id") == track_id:
                        track_info = track
                        break
        
        if track_info:
            # Clear current queue and add the entire album
            self.queue_manager.clear_queue()
            
            # Get all tracks from the current album
            if self.current_album:
                album_id = self.current_album.get("id")
                if album_id:
                    all_tracks = self.library_manager.get_album_tracks(album_id)
                    # Sort tracks to ensure proper order
                    all_tracks = self._sort_tracks_by_number(all_tracks)
                    
                    # Add all tracks to the queue without playing
                    self.queue_manager.add_tracks(all_tracks, play_first=False)
                    
                    # Find the index of the clicked track and start playing from there
                    clicked_track_index = -1
                    for i, track in enumerate(all_tracks):
                        if track.get("id") == track_id:
                            clicked_track_index = i
                            break
                    
                    if clicked_track_index >= 0:
                        # Start playing from the clicked track
                        self.queue_manager.play_track_at_index(clicked_track_index)
                        print(f"[DEBUG] Added entire album ({len(all_tracks)} tracks) to queue, starting from track {clicked_track_index + 1}: {track_info.get('name', 'Unknown')}")
                    else:
                        # Fallback: play first track
                        self.queue_manager.play_track_at_index(0)
                        print(f"[DEBUG] Added entire album ({len(all_tracks)} tracks) to queue, starting from first track")
                    
                    self.queue_ui_manager.update_queue_ui()
            else:
                # No current album, just add the single track
                self.queue_manager.add_track(track_info, play_immediately=True)
                print(f"[DEBUG] Added single track to queue: {track_info.get('name', 'Unknown')}")
                self.queue_ui_manager.update_queue_ui()

    def _on_queue_track_changed(self, track_info: Dict[str, Any]):
        """Called when queue manager changes to a new track"""
        print(f"[DEBUG] Queue changed to track: {track_info.get('name', 'Unknown')}")
        
        # Get stream URL and play
        client = self.library_manager.client
        if not client:
            return
            
        track_id = track_info.get("id")
        if not track_id:
            return
            
        url = client.get_track_stream_url(track_id)
        print(f"[DEBUG] Stream URL: {url}")
        
        # Stop current playback before starting new track
        self.media_player.stop()
        self.media_player.set_uri(url)
        self.media_player.play()
        print("[DEBUG] Playback started from queue")
        
        # Update now playing bar
        self.update_now_playing(track_info, self.current_album)
        # Start/schedule progress updates
        self._start_progress_timer()
        
        # Update play/pause button icon
        if self.play_pause_btn:
            self.play_pause_btn.set_icon_name("media-playback-pause")
        
        # Update queue UI to show current track  
        self.queue_ui_manager.update_queue_ui()
    
    def _on_queue_finished(self):
        """Called when the queue has finished playing"""
        print("[DEBUG] Queue finished, stopping playback")
        self.media_player.stop()
        
        # Update play/pause button icon
        if self.play_pause_btn:
            self.play_pause_btn.set_icon_name("media-playback-start")
    
    def _update_queue_ui(self):
        """Update the queue UI to reflect current queue state"""
        if not self.queue_list_box:
            return
        
        # Clear existing queue items
        while True:
            row = self.queue_list_box.get_first_child()
            if row is None:
                break
            self.queue_list_box.remove(row)
        
        # Add current queue tracks
        queue_info = self.queue_manager.get_queue_info()
        for i, track in enumerate(queue_info["tracks"]):
            queue_row = self._create_queue_row(track, i, i == queue_info["current_index"])
            self.queue_list_box.append(queue_row)
        
        print(f"[DEBUG] Updated queue UI with {len(queue_info['tracks'])} tracks")
    
    def _create_queue_row(self, track_info: Dict[str, Any], index: int, is_current: bool) -> Gtk.ListBoxRow:
        """Create a queue row widget for a track"""
        row = Gtk.ListBoxRow()
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        box.set_margin_start(4)
        box.set_margin_end(4)
        box.set_margin_top(8)
        box.set_margin_bottom(8)
        
        # Track number/playing indicator
        if is_current:
            indicator = Gtk.Image.new_from_icon_name("media-playback-start")
            indicator.set_css_classes(["accent"])
        else:
            # set to album art or placeholder
            indicator = Gtk.Image.new_from_icon_name("audio-x-generic")
            album_art_path = self.current_album.get("art_path") if self.current_album else None
            if album_art_path and Path(album_art_path).exists():
                try:
                    texture = Gdk.Texture.new_from_filename(album_art_path)
                    indicator.set_from_paintable(texture)
                except Exception:
                    pass
        
        indicator.set_size_request(32, -1)
        box.append(indicator)
        
        # Track info
        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        
        title_label = Gtk.Label(label=track_info.get("name", "Unknown Track"))
        title_label.set_halign(Gtk.Align.START)
        title_label.set_ellipsize(Pango.EllipsizeMode.END)
        if is_current:
            title_label.set_css_classes(["heading"])
        
        artist_label = Gtk.Label(label=track_info.get("artist", "Unknown Artist"))
        artist_label.set_halign(Gtk.Align.START)
        artist_label.set_ellipsize(Pango.EllipsizeMode.END)
        artist_label.set_css_classes(["caption", "dim-label"])
        
        info_box.append(title_label)
        info_box.append(artist_label)
        info_box.set_hexpand(True)
        box.append(info_box)
        
        # Duration
        duration_ticks = track_info.get("runtime_ticks")
        if duration_ticks:
            duration_str = self._format_duration(duration_ticks)
            duration_label = Gtk.Label(label=duration_str)
            duration_label.set_css_classes(["caption", "dim-label"])
            box.append(duration_label)
        
        row.set_child(box)
        
        # Store track index for reference
        setattr(row, "track_index", index)
        
        # Connect row activation to play track
        row.connect("activate", lambda _: self._play_queue_track(index))
        
        # Add left-click gesture to ensure reliable track playing
        left_click_gesture = Gtk.GestureClick.new()
        left_click_gesture.set_button(1)  # Left mouse button
        left_click_gesture.connect("pressed", lambda gesture, n_press, x, y: self._play_queue_track(index))
        row.add_controller(left_click_gesture)
        
        # Add right-click context menu
        self._add_queue_context_menu(row, index, track_info)
        
        return row
    
    def _remove_track_from_queue(self, index: int):
        """Remove a track from the queue by index"""
        self.queue_manager.remove_track(index)
        self._update_queue_ui()

    def _move_track_to_next(self, index: int):
        """Move a track to be next in the queue"""
        queue_info = self.queue_manager.get_queue_info()
        current_index = queue_info["current_index"]
        
        if current_index >= 0 and index != current_index and index != current_index + 1:
            # Get the track to move
            track_to_move = queue_info["tracks"][index]
            
            # Remove from current position
            self.queue_manager.remove_track(index)
            
            # Adjust target position if we removed from before current track
            next_position = current_index + 1 if index > current_index else current_index
            
            # Insert at next position
            self.queue_manager.queue.insert(next_position, track_to_move)
            
            print(f"[DEBUG] Moved track '{track_to_move.get('name', 'Unknown')}' to play next")
            self._update_queue_ui()
    
    def _play_queue_track(self, index: int):
        """Play a specific track from the queue"""
        print(f"[DEBUG] Left-clicked queue track at index {index}")
        if self.queue_manager.play_track_at_index(index):
            print(f"[DEBUG] Successfully switched to track at index {index}")
            self._update_queue_ui()
        else:
            print(f"[DEBUG] Failed to switch to track at index {index}")

    def _add_queue_context_menu(self, row: Gtk.ListBoxRow, index: int, track_info: Dict[str, Any]):
        """Add a right-click context menu to a queue row"""
        # Create the popover menu
        menu_model = Gio.Menu()
        
        # Add menu items
        menu_model.append("Remove from Queue", f"queue.remove.{index}")
        menu_model.append("Play Next", f"queue.play_next.{index}")
        
        # Create the popover
        popover = Gtk.PopoverMenu.new_from_model(menu_model)
        popover.set_parent(row)
        popover.set_has_arrow(False)
        
        # Store popover reference on the row for cleanup
        setattr(row, "_context_menu_popover", popover)
        
        # Connect to row destruction to clean up popover
        def on_row_destroy(row):
            if hasattr(row, "_context_menu_popover"):
                popover = getattr(row, "_context_menu_popover")
                if popover:
                    popover.unparent()
        
        row.connect("destroy", on_row_destroy)
        
        # Create action group for this specific queue item
        action_group = Gio.SimpleActionGroup()
        
        # Remove action
        remove_action = Gio.SimpleAction.new(f"remove.{index}", None)
        remove_action.connect("activate", lambda action, param: self._remove_track_from_queue(index))
        action_group.add_action(remove_action)
        
        # Play next action (move track to be next in queue)
        play_next_action = Gio.SimpleAction.new(f"play_next.{index}", None)
        play_next_action.connect("activate", lambda action, param: self._move_track_to_next(index))
        action_group.add_action(play_next_action)
        
        # Insert the action group
        row.insert_action_group("queue", action_group)
        
        # Create gesture for right-click
        gesture = Gtk.GestureClick.new()
        gesture.set_button(3)  # Right mouse button
        
        def on_right_click(gesture, n_press, x, y):
            # Create a rectangle at the click position for the popover to point to
            rect = Gdk.Rectangle()
            rect.x = int(x)
            rect.y = int(y)
            rect.width = 1
            rect.height = 1
            
            # Set the pointing rectangle and popup
            popover.set_pointing_to(rect)
            popover.popup()
        
        gesture.connect("pressed", on_right_click)
        row.add_controller(gesture)



    def _on_previous_track_clicked(self):
        """Handle previous track button click"""
        print("[DEBUG] Previous track button clicked")
        self.queue_manager.previous_track()

    def _on_next_track_clicked(self):
        """Handle next track button click"""
        print("[DEBUG] Next track button clicked")
        self.queue_manager.next_track()

    def _on_album_play_button_clicked(self):
        """Handle album play button click - clear queue and play entire album"""
        if not self.current_album:
            print("[DEBUG] No current album to play")
            return
        
        print(f"[DEBUG] Album play button clicked for: {self.current_album.get('name', 'Unknown Album')}")
        
        # Clear current queue and add entire album
        self.queue_manager.clear_queue()
        
        # Get all tracks from the current album
        album_id = self.current_album.get("id")
        if album_id:
            tracks = self.library_manager.get_album_tracks(album_id)
            if tracks:
                # Sort tracks by track number to ensure correct order
                sorted_tracks = self._sort_tracks_by_number(tracks)
                
                # Add all tracks and start playing the first one
                self.queue_manager.add_tracks(sorted_tracks, play_first=True)
                print(f"[DEBUG] Added entire album to queue: {len(sorted_tracks)} tracks (sorted by track number)")
                self._update_queue_ui()
            else:
                print("[DEBUG] No tracks found for album")
        else:
            print("[DEBUG] Album has no ID")

    def add_album_to_queue(self, album_info: Dict[str, Any], play_immediately: bool = False):
        """Add an entire album to the queue"""
        album_id = album_info.get("id")
        if not album_id:
            return
        
        tracks = self.library_manager.get_album_tracks(album_id)
        if tracks:
            # Sort tracks by track number
            sorted_tracks = self._sort_tracks_by_number(tracks)
            
            if play_immediately:
                self.queue_manager.clear_queue()
            
            self.queue_manager.add_tracks(sorted_tracks, play_first=play_immediately)
            print(f"[DEBUG] Added album '{album_info.get('name', 'Unknown')}' to queue ({len(sorted_tracks)} tracks, sorted)")
            self._update_queue_ui()

    def get_current_queue_status(self) -> str:
        """Get a string description of the current queue status"""
        queue_info = self.queue_manager.get_queue_info()
        total_tracks = queue_info["total_tracks"]
        current_index = queue_info["current_index"]
        
        if total_tracks == 0:
            return "Queue is empty"
        elif current_index >= 0:
            return f"Track {current_index + 1} of {total_tracks}"
        else:
            return f"{total_tracks} tracks in queue"

    def _sort_tracks_by_number(self, tracks: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Sort tracks by disc number and track number"""
        def get_disc_number(track: Dict[str, Any]) -> int:
            # Try different possible disc number fields
            disc_num = track.get("ParentIndexNumber") or track.get("disc_number") or track.get("DiscNumber") or 1
            return int(disc_num) if disc_num else 1
        
        def get_track_number(track: Dict[str, Any]) -> int:
            # Try different possible track number fields
            track_num = track.get("IndexNumber") or track.get("track_number") or track.get("TrackNumber") or 0
            return int(track_num) if track_num else 0
        
        # Sort by disc number first, then by track number
        sorted_tracks = sorted(tracks, key=lambda t: (get_disc_number(t), get_track_number(t)))
        
        # Debug: print track order
        print("[DEBUG] Track order after sorting by disc and track:")
        for i, track in enumerate(sorted_tracks):
            disc_num = get_disc_number(track)
            track_num = get_track_number(track)
            print(f"  {i+1}. Disc {disc_num}, Track #{track_num}: {track.get('name', 'Unknown')}")
        
        return sorted_tracks
            
    def _format_duration(self, ticks: Optional[int]) -> str:
        """Format duration from ticks to MM:SS"""
        if not ticks:
            return ""
        try:
            seconds = ticks // 10000000  # Convert from 100ns ticks to seconds
            hours = seconds // 3600
            minutes = (seconds % 3600) // 60
            seconds = seconds % 60
            if hours > 0:
                return f"{hours}:{minutes:02d}:{seconds:02d}"
            else:
                return f"{minutes}:{seconds:02d}"
        except (TypeError, ValueError):
            return ""

class MediaPlayer:
    """Handles media playback using GStreamer"""
    
    def __init__(self):
        Gst.init(None)
        self.player = Gst.ElementFactory.make("playbin", "player")
        if not self.player:
            raise RuntimeError("Failed to create GStreamer playbin element")
        
    def set_uri(self, uri: str):
        """Set the media URI to play"""
        self.player.set_property("uri", uri)
        
    def play(self):
        """Start playback"""
        self.player.set_state(Gst.State.PLAYING)
        
    def pause(self):
        """Pause playback"""
        self.player.set_state(Gst.State.PAUSED)
        
    def stop(self):
        """Stop playback"""
        self.player.set_state(Gst.State.NULL)
        
    def is_playing(self) -> bool:
        """Check if currently playing"""
        state = self.player.get_state(0).state
        return state == Gst.State.PLAYING
    
    def get_position(self) -> float:
        """Get current position in seconds (with 0.1s precision)"""
        try:
            success, position = self.player.query_position(Gst.Format.TIME)
            if success:
                # Convert to seconds with 0.1s precision
                pos_seconds = position / Gst.SECOND
                return round(pos_seconds, 1)
            else:
                return 0.0
        except Exception as e:
            print(f"[DEBUG] GStreamer position query exception: {e}")
            return 0.0
    
    def get_duration(self) -> float:
        """Get total duration in seconds (with 0.1s precision)"""
        try:
            success, duration = self.player.query_duration(Gst.Format.TIME)
            if success:
                # Convert to seconds with 0.1s precision
                dur_seconds = duration / Gst.SECOND
                return round(dur_seconds, 1)
            else:
                return 0.0
        except Exception as e:
            print(f"[DEBUG] GStreamer duration query exception: {e}")
            return 0.0
    
    def set_position(self, seconds: float):
        """Seek to position in seconds (accepts fractional seconds)"""
        try:
            nanoseconds = int(seconds * Gst.SECOND)
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, nanoseconds)
        except Exception:
            pass

class MusicApp(Adw.Application):
    def _connect_play_pause_btn(self):
        self.play_pause_btn = self.builder.get_object("play_pause_btn")
        if self.play_pause_btn:
            self.play_pause_btn.connect("clicked", self._on_play_pause_clicked)

    def _on_play_pause_clicked(self, button):
        """Toggle play/pause on the media player"""
        if not hasattr(self, "media_player"):
            return
        if self.media_player.is_playing():
            self.media_player.pause()
            self.play_pause_btn.set_icon_name("media-playback-start")
        else:
            self.media_player.play()
            self.play_pause_btn.set_icon_name("media-playback-pause")

    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.ui_path = Path(__file__).resolve().parent.parent / "ui" / "window.ui"
        self.builder = Gtk.Builder.new_from_file(str(self.ui_path))
        self.window: Adw.ApplicationWindow = self.builder.get_object("main_window")
        self.library_manager = LibraryManager()
        self.ui_manager: UIManager
        self.media_player = MediaPlayer()
        
    def do_activate(self):
            
        print("[DEBUG] Starting application")
        
        # Set up theme
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        
        # Load UI
        
        # Create window
        if not isinstance(self.window, Adw.ApplicationWindow):
            self.window = Adw.ApplicationWindow(application=self)
            self.window.set_title("Jellyfin GTK Music")
            self.window.set_default_size(900, 600)
        else:
            self.window.set_application(self)

        # Connect play/pause button to handler in MusicApp
        self._connect_play_pause_btn()
            
        self.add_window(self.window)
        
        # Initialize UI manager
        self.ui_manager: UIManager = UIManager(self.builder, self.library_manager, self.media_player)
        
        # Present window immediately for fast startup
        self.window.present()
        print("[DEBUG] Window presented")
        
        # Load library
        self._load_library()
        
    def _load_library(self):
        """Load library data"""
        # Show loading page
        self.ui_manager.show_loading()
        
        # Check for cached data first
        if self.library_manager.load_cached_data():
            print("[DEBUG] Loaded cached library data")
            # Restore Jellyfin client from config if possible
            cfg = load_config()
            if cfg and cfg.access_token and cfg.server_url and cfg.user_id:
                self.library_manager.set_client(JellyfinClient(cfg.server_url, cfg.access_token, cfg.user_id))
                print("[DEBUG] Restored Jellyfin client from config for cached data")
            else:
                print("[DEBUG] No config found to restore Jellyfin client")
            self.ui_manager.populate_albums()
            self.ui_manager.show_albums()
            return
            
        # Load from server
        cfg = load_config()
        if not cfg or not cfg.access_token or not cfg.server_url or not cfg.user_id:
            print("[DEBUG] No configuration found")
            self.ui_manager.show_albums()
            return
            
        client = JellyfinClient(cfg.server_url, cfg.access_token, cfg.user_id)
        self.library_manager.set_client(client)
        
        # Load in background thread
        def load_thread():
            try:
                success = self.library_manager.fetch_library_from_server()
                GLib.idle_add(self._on_library_loaded, success)
            except Exception as e:
                print(f"[ERROR] Failed to load library: {e}")
                GLib.idle_add(self._on_library_loaded, False)
                
        thread = threading.Thread(target=load_thread)
        thread.daemon = True
        thread.start()
        
    def _on_library_loaded(self, success: bool):
        """Handle library loading completion"""
        if success:
            print("[DEBUG] Library loaded successfully")
            self.ui_manager.populate_albums()
        else:
            print("[DEBUG] Failed to load library")
            
        self.ui_manager.show_albums()
        return False  # Remove from GLib idle queue

    def do_startup(self, *args):
        Adw.Application.do_startup(self, *args)
        # Add action for sign out
        action = Gio.SimpleAction.new("user.sign_out", None)
        action.connect("activate", self._on_sign_out)
        self.add_action(action)

    def _on_login_clicked(self):
        """Handle login button click"""
        server = self.ui_manager.server_address.get_text().strip()
        username = self.ui_manager.entry_username.get_text().strip()
        password = self.ui_manager.entry_password.get_text().strip()
        if not server or not username or not password:
            self.ui_manager.login_error.set_text("Please fill in all fields.")
            return
        self.ui_manager.btn_login.set_sensitive(False)
        self.ui_manager.login_error.set_text("Logging in...")
        def login_thread():
            try:
                client = JellyfinClient(server)
                cfg = client.login(username, password)
                save_config(cfg)
                self.library_manager.set_client(client)
                # Fetch library from server and update UI
                success = self.library_manager.fetch_library_from_server()
                GLib.idle_add(self._on_login_successful, success)
            except Exception as e:
                print(f"[ERROR] Login failed: {e}")
                GLib.idle_add(self._on_login_failed, str(e))
        thread = threading.Thread(target=login_thread)
        thread.daemon = True
        thread.start()

    def _on_sign_out(self, action, param):
        print("[DEBUG] Signing out...")
        clear_config()
        # Show login dialog
        self.ui_manager.show_login_dialog()
        # Optionally, reset UI to logged-out state
        self.ui_manager.show_loading()

def main():
    print('[DEBUG] Entered main()')
    app = MusicApp()
    app.run()

if __name__ == "__main__":
    print('[DEBUG] __main__ block reached')
    main()