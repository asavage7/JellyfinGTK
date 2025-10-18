#!/usr/bin/env python3

import sys
import threading
from pathlib import Path
from typing import Dict, List, Optional, Any
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")

from gi.repository import Gtk, Gio, Adw, GLib, Pango
from .config_store import load_config, save_config, clear_config, cache_dir, clear_cache_excluding_images
from .jellyfin_client import JellyfinClient, MediaItem


APP_ID = "com.example.JellyfinGtkMusic"


class LibraryManager:
    """Manages the music library data and caching"""
    
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
            albums = self.client.get_albums()
            
            print("[DEBUG] Fetching tracks from server...")
            tracks = self.client.get_tracks()
            
            print("[DEBUG] Fetching artists from server...")
            artists = self.client.get_artists()
            
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
                    print(f"[ERROR] Failed to get tracks for album {a.name}: {e}")
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
    
    def __init__(self, builder: Gtk.Builder, library_manager: LibraryManager):
        self.builder = builder
        self.library_manager = library_manager
        
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
        
        # Connect signals
        self._setup_ui_signals()
        
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
        """Create an album tile widget"""
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_size_request(160, -1)
        
        # Album art
        art = Gtk.Image()
        art.set_size_request(160, 160)
        
        art_path = album.get("art_path")
        if art_path and Path(art_path).exists():
            try:
                texture = Gdk.Texture.new_from_filename(art_path)
                art.set_from_paintable(texture)
            except Exception:
                art.set_from_icon_name("media-optical-dvd")
        else:
            art.set_from_icon_name("media-optical-dvd")
            
        art.add_css_class("album-art")
        box.append(art)
        
        # Album title
        title = Gtk.Label()
        title.set_text(album.get("name", "Unknown Album"))
        title.set_ellipsize(Pango.EllipsizeMode.END)
        title.set_lines(2)
        title.set_wrap(True)
        title.set_justify(Gtk.Justification.CENTER)
        title.add_css_class("album-title")
        box.append(title)
        
        # Artist
        artist = Gtk.Label()
        artist.set_text(album.get("artist", "Unknown Artist"))
        artist.set_ellipsize(Pango.EllipsizeMode.END)
        artist.add_css_class("dim-label")
        artist.add_css_class("caption")
        box.append(artist)
        
        return box
        
    def _populate_album_info(self, album: Dict[str, Any]):
        """Populate the album info page"""
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
        """Populate the track listing in album info"""
        # Clear existing tracks in album info
        child = self.album_info_box.get_first_child()
        track_list = None
        
        # Find existing track list or create new one
        while child:
            if isinstance(child, Gtk.ListBox) and child.get_name() == "track_listing":
                track_list = child
                break
            child = child.get_next_sibling()
            
        if not track_list:
            track_list = Gtk.ListBox()
            track_list.set_name("track_listing")
            track_list.add_css_class("boxed-list")
            track_list.set_selection_mode(Gtk.SelectionMode.NONE)
            self.album_info_box.append(track_list)
        else:
            # Clear existing tracks
            child = track_list.get_first_child()
            while child:
                next_child = child.get_next_sibling()
                track_list.remove(child)
                child = next_child
                
        # Add tracks
        for i, track in enumerate(tracks, 1):
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            row.set_margin_top(8)
            row.set_margin_bottom(8)
            row.set_margin_start(12)
            row.set_margin_end(12)
            
            # Track number
            num_label = Gtk.Label()
            num_label.set_text(str(i))
            num_label.add_css_class("dim-label")
            num_label.set_size_request(30, -1)
            row.append(num_label)
            
            # Track name
            name_label = Gtk.Label()
            name_label.set_text(track.get("name", "Unknown Track"))
            name_label.set_ellipsize(Pango.EllipsizeMode.END)
            name_label.set_hexpand(True)
            name_label.set_halign(Gtk.Align.START)
            row.append(name_label)
            
            # Duration
            duration = self._format_duration(track.get("runtime_ticks"))
            if duration:
                duration_label = Gtk.Label()
                duration_label.set_text(duration)
                duration_label.add_css_class("dim-label")
                row.append(duration_label)
                
            track_list.append(row)
            
    def _format_duration(self, ticks: Optional[int]) -> str:
        """Format duration from ticks to MM:SS"""
        if not ticks:
            return ""
        try:
            seconds = ticks // 10000000  # Convert from 100ns ticks to seconds
            minutes = seconds // 60
            seconds = seconds % 60
            return f"{minutes}:{seconds:02d}"
        except (TypeError, ValueError):
            return ""


class MusicApp(Adw.Application):
    """Main application class"""
    
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.NON_UNIQUE)
        self.window: Optional[Adw.ApplicationWindow] = None
        self.library_manager = LibraryManager()
        self.ui_manager: Optional[UIManager] = None
        
    def do_activate(self):
        """Activate the application"""
        if self.window is not None:
            self.window.present()
            return
            
        print("[DEBUG] Starting application")
        
        # Set up theme
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)
        
        # Load UI
        ui_path = Path(__file__).resolve().parent.parent / "ui" / "window.ui"
        builder = Gtk.Builder.new_from_file(str(ui_path))
        
        # Create window
        self.window = builder.get_object("main_window")
        if not isinstance(self.window, Adw.ApplicationWindow):
            self.window = Adw.ApplicationWindow(application=self)
            self.window.set_title("Jellyfin GTK Music")
            self.window.set_default_size(900, 600)
        else:
            self.window.set_application(self)
            
        self.add_window(self.window)
        
        # Initialize UI manager
        self.ui_manager = UIManager(builder, self.library_manager)
        
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


def main():
    """Main entry point"""
    print("[DEBUG] Initializing Adwaita...")
    Adw.init()
    
    print("[DEBUG] Creating MusicApp...")
    app = MusicApp()
    
    print("[DEBUG] Running app...")
    exit_code = app.run(sys.argv)
    
    print(f"[DEBUG] App exited with code: {exit_code}")
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
        if self.window is not None:
            self.window.present()
            return
        
        print("[DEBUG] Starting do_activate")
        # Theme
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.DEFAULT)

        # Load UI
        ui_path = Path(__file__).resolve().parent.parent / "ui" / "window.ui"
        builder = Gtk.Builder.new_from_file(str(ui_path))
        window = builder.get_object("main_window")
        if not isinstance(window, Adw.ApplicationWindow):
            window = Adw.ApplicationWindow(application=self)
            window.set_title("Jellyfin GTK Music")
            window.set_default_size(900, 600)
        else:
            window.set_application(self)
        # Keep a strong reference and add to app to avoid early GC
        self.window = window
        window.set_application(self)
        self.add_window(window)
        
        print("[DEBUG] Window created and added to application")
        
        # Present window immediately for fast startup
        window.present()
        print("[DEBUG] Window presented early for fast startup")
        
        # Connect close-request to keep app alive
        window.connect("close-request", lambda w: False)

        # Widgets
        library_sidebar: Gtk.ListBox = builder.get_object("library_sidebar")
        library_stack: Gtk.Stack = builder.get_object("library_stack")
        albums_flow: Gtk.FlowBox = builder.get_object("albums_flow")
        
        # Show the existing loading page
        library_stack.set_visible_child_name("loading")
        album_info_art: Gtk.Image = builder.get_object("album_view_album_art")
        album_info_title: Gtk.Label = builder.get_object("album_view_title_label")
        album_info_artist: Gtk.Label = builder.get_object("album_view_artist_label")
        album_info_additional: Gtk.Label = builder.get_object("album_view_additional_label")
        album_info_box: Gtk.Box = builder.get_object("album_info_box")
        tracks_list: Gtk.ListBox = builder.get_object("tracks_list")
        artists_list: Gtk.ListBox = builder.get_object("artists_list")
        sort_menu_btn: Gtk.MenuButton = builder.get_object("sort_menu_btn")
        now_playing: Adw.BottomSheet = builder.get_object("now_playing")
        now_playing_bar: Gtk.Box = builder.get_object("now_playing_bar")
        open_now_playing: Gtk.Button = builder.get_object("open_now_playing")
        btn_play: Gtk.Button = builder.get_object("btn_play")
        back_button: Gtk.Button = builder.get_object("back_button")
        refresh_button: Gtk.Button = builder.get_object("refresh_button")
        user_avatar: Adw.Avatar = builder.get_object("user_avatar")
        account_menu_btn: Gtk.MenuButton = builder.get_object("account_menu_btn")
        about_dialog: Adw.AboutDialog = builder.get_object("about_dialog")

        library_sidebar.set_stack(library_stack)
        library_stack.set_visible_child_name("albums")
        sort_menu_btn.set_visible(True)
        

        # connect about dialog to menu item
        #account_menu_btn.set_menu_model(builder.get_object("account_menu"))
        #about_menu_item: Gio.MenuItem = builder.get_object("menu_item_about")
        #about_menu_item.connect("activate", lambda _itm: about_dialog.set_visible(True))
        #about_dialog.set_transient_for(window)

        open_now_playing.connect("clicked", lambda _btn: builder.get_object("now_playing").set_open(True))
        # Helpers
        def make_album_tile(album: dict) -> Gtk.Widget:
            title = album.get("name", "")
            artist = album.get("artist", "")
            art_path = album.get("art_path")
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
            for m in ("set_margin_start", "set_margin_end", "set_margin_top", "set_margin_bottom"):
                getattr(box, m)(6)
            if art_path:
                pic = Gtk.Picture.new_for_filename(art_path)
                pic.set_content_fit(Gtk.ContentFit.COVER)
            else:
                pic = Gtk.Image.new_from_file("./assets/missing album.png")
                pic.set_pixel_size(128)
            pic.set_halign(Gtk.Align.CENTER)
            pic.set_size_request(128, 128)
            l_title = Gtk.Label(label=title or "")
            l_title.set_wrap(False)
            l_title.set_ellipsize(Pango.EllipsizeMode.END)
            l_title.set_max_width_chars(32)
            l_title.set_width_chars(32)
            l_title.set_halign(Gtk.Align.CENTER)
            l_title.set_xalign(0.5)
            l_title.add_css_class("heading")
            l_artist = Gtk.Label(label=artist or "")
            l_artist.add_css_class("dim-label")
            l_artist.set_halign(Gtk.Align.CENTER)
            l_artist.set_xalign(0.5)
            box.append(pic)
            box.append(l_title)
            box.append(l_artist)
            fb_child = Gtk.FlowBoxChild()
            fb_child.set_size_request(148, 208)
            fb_child.set_child(box)
            fb_child.album_data = album  # Attach album data for click handler
            return fb_child

        def format_duration(ticks: int | None) -> str:
            if not ticks or ticks <= 0:
                return ""
            total_seconds = int(ticks // 10_000_000)
            m, s = divmod(total_seconds, 60)
            h, m = divmod(m, 60)
            if h:
                return f"{h:d}:{m:02d}:{s:02d}"
            return f"{m:d}:{s:02d}"

        def add_row_two_col(listbox: Gtk.ListBox, left: str, right: str) -> None:
            row = Gtk.ListBoxRow()
            h = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            h.set_margin_start(12)
            h.set_margin_end(12)
            h.set_margin_top(6)
            h.set_margin_bottom(6)
            l_left = Gtk.Label(label=left or "")
            l_left.set_hexpand(True)
            l_left.set_xalign(0)
            l_right = Gtk.Label(label=right or "")
            l_right.add_css_class("dim-label")
            l_right.set_xalign(1)
            h.append(l_left)
            h.append(l_right)
            row.set_child(h)
            listbox.append(row)

        def create_track_listing(album: dict, tracks: list[dict]) -> None:
            # Clear any existing track listings
            children = list(album_info_box)
            for child in children:
                if isinstance(child, Gtk.ListBox) or isinstance(child, Gtk.Label):
                    album_info_box.remove(child)
            
            print(f"Creating track listing for album '{album.get('name')}' with {len(tracks)} tracks")
            
            # Group tracks by disc number
            discs = {}
            for t in tracks:
                disc_num = int(t.get("disc_number", 1))
                discs.setdefault(disc_num, []).append(t)

            for disc_num in sorted(discs):
                disc_tracks = sorted(discs[disc_num], key=lambda t: int(t.get("track_number", 0)))
                if len(discs) > 1:
                    disc_label = Gtk.Label(label=f"Disc {disc_num}")
                    disc_label.set_halign(Gtk.Align.START)
                    disc_label.set_margin_top(12)
                    disc_label.set_margin_bottom(4)
                    disc_label.add_css_class("heading")
                    album_info_box.append(disc_label)

                track_listbox = Gtk.ListBox()
                track_listbox.set_selection_mode(Gtk.SelectionMode.NONE)
                track_listbox.set_margin_bottom(12)
                track_listbox.add_css_class("boxed-list")
                for t in disc_tracks:
                    row = Gtk.ListBoxRow()
                    hbox = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
                    hbox.set_margin_start(8)
                    hbox.set_margin_end(8)
                    hbox.set_margin_top(2)
                    hbox.set_margin_bottom(2)

                    # Track number
                    lbl_num = Gtk.Label(label=str(t.get("track_number", "")))
                    lbl_num.set_width_chars(3)
                    lbl_num.set_xalign(0)
                    lbl_num.add_css_class("dimmed")
                    hbox.append(lbl_num)

                    # Track name
                    lbl_name = Gtk.Label(label=t.get("name", ""))
                    lbl_name.set_ellipsize(Pango.EllipsizeMode.END)
                    lbl_name.set_max_width_chars(32)
                    lbl_name.set_hexpand(True)
                    lbl_name.set_xalign(0)
                    hbox.append(lbl_name)

                    # Artist (if different from album artist)
                    artist = t.get("artist", "")
                    if artist and artist != album.get("artist", ""):
                        lbl_artist = Gtk.Label(label=artist)
                        lbl_artist.add_css_class("dim-label")
                        lbl_artist.set_xalign(0)
                        hbox.append(lbl_artist)

                    # Duration
                    lbl_duration = Gtk.Label(label=format_duration(int(t.get("runtime_ticks", 0))))
                    lbl_duration.set_xalign(1)
                    lbl_duration.set_margin_end(8)
                    hbox.append(lbl_duration)

                    # Favorite button
                    btn_fav = Gtk.Button.new_from_icon_name("favorite-symbolic")
                    btn_fav.set_tooltip_text("Favorite")
                    btn_fav.add_css_class("flat")
                    hbox.append(btn_fav)

                    # Menu button
                    btn_menu = Gtk.MenuButton()
                    btn_menu.set_icon_name("open-menu-symbolic")
                    btn_menu.set_tooltip_text("More options")
                    btn_menu.add_css_class("flat")
                    hbox.append(btn_menu)

                    row.set_child(hbox)
                    track_listbox.append(row)

                album_info_box.append(track_listbox)
                print(f"Added track listbox with {len(disc_tracks)} tracks to album_info_box")


        # In-memory data
        self._albums_data = []
        self._tracks_data = []
        self._artists_data = []
        self._album_tracks_cache = {}
        self._client = None

        # Sorting state and rendering
        self.sort_by = "Name"
        self.sort_order_desc = False

        def refresh_library():
            clear_cache_excluding_images()
            self._albums_data = []
            self._tracks_data = []
            self._artists_data = []
            while True:
                child = albums_flow.get_child_at_index(0)
                if child is None:
                    break
                albums_flow.remove(child)
            while True:
                row = tracks_list.get_row_at_index(0)
                if row is None:
                    break
                tracks_list.remove(row)
            while True:
                row = artists_list.get_row_at_index(0)
                if row is None:
                    break
                artists_list.remove(row)
            cfg = load_config()
            if not cfg or not cfg.access_token or not cfg.server_url or not cfg.user_id:
                return
            client = JellyfinClient(cfg.server_url, cfg.access_token, cfg.user_id)
            self.load_library_async(client, library_stack)

        refresh_button.connect("clicked", refresh_library)

        def apply_sort_to_ui():
            # clear
            while True:
                child = albums_flow.get_child_at_index(0)
                if child is None:
                    break
                albums_flow.remove(child)
            while True:
                row = tracks_list.get_row_at_index(0)
                if row is None:
                    break
                tracks_list.remove(row)
            while True:
                row = artists_list.get_row_at_index(0)
                if row is None:
                    break
                artists_list.remove(row)

            desc = self.sort_order_desc
            def norm(s: str | None) -> str:
                return (s or "").lower()

            # albums
            albums_sorted = list(self._albums_data)
            if self.sort_by == "Artist":
                albums_sorted.sort(key=lambda a: (norm(a.get("artist")), norm(a.get("name"))), reverse=desc)
            else:
                albums_sorted.sort(key=lambda a: norm(a.get("name")), reverse=desc)
            for a in albums_sorted:
                albums_flow.insert(make_album_tile(a), -1)

            # Album tile click handler
            def on_album_activated(flowbox, child):
                album = getattr(child, "album_data", None)
                if not album:
                    return
                album_id = album.get("id", "")
                tracks = []
                if album_id in self._album_tracks_cache:
                    tracks = self._album_tracks_cache[album_id]
                else:
                    album_item = MediaItem(
                        id=album.get("id", ""),
                        name=album.get("name", ""),
                        artist=album.get("artist", ""),
                        image_tag=None,
                        num_tracks=album.get("num_tracks", 0),
                    )
                    if self._client:
                        try:
                            print(f"[DEBUG] Fetching tracks for album: {album.get('name', 'Unknown')}")
                            album_tracks_result = self._client.get_album_tracks(album_item)
                            if album_tracks_result and isinstance(album_tracks_result, list):
                                tracks = [t.__dict__ for t in album_tracks_result]
                                print(f"[DEBUG] Found {len(tracks)} tracks for album {album.get('name', 'Unknown')}")
                            else:
                                print(f"[DEBUG] No tracks returned for album {album.get('name', 'Unknown')}")
                                tracks = []
                        except Exception as e:
                            print(f"[ERROR] Error fetching tracks for album {album.get('name', 'Unknown')}: {e}")
                            tracks = []
                    else:
                        tracks = []
                    self._album_tracks_cache[album_id] = tracks
                    # Write back to cache file
                    cache_file = cache_dir() / "library.json"
                    try:
                        import json
                        data = json.loads(cache_file.read_text())
                        data.setdefault("album_tracks", {})[album_id] = tracks
                        cache_file.write_text(json.dumps(data))
                    except Exception:
                        pass
                # Set album info widgets
                if album.get("art_path"):
                    album_info_art.set_from_file(album["art_path"])
                else:
                    album_info_art.set_from_icon_name("media-optical-dvd")
                album_info_title.set_label(album.get("name", ""))
                album_info_title.set_ellipsize(Pango.EllipsizeMode.END)
                album_info_title.set_max_width_chars(40)
                album_info_artist.set_label(album.get("artist", ""))
                # Additional info: fill with placeholders or real data if available
                additional_parts = []
                if album.get("year"):
                    additional_parts.append(str(album["year"]))
                if album.get("num_tracks"):
                    additional_parts.append(f"{album['num_tracks']} tracks")
                if album.get("runtime_ticks"):
                    additional_parts.append(format_duration(album["runtime_ticks"]))
                if additional_parts:
                    album_info_additional.set_label(" · ".join(additional_parts))
                    album_info_additional.set_visible(True)
                else:
                    album_info_additional.set_visible(False)
                # Switch to album_info page
                library_stack.set_visible_child_name("album_info")
                back_button.set_visible(True)
                back_button.connect("clicked", lambda _btn: (
                    library_stack.set_visible_child_name("albums"),
                    library_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT),
                    back_button.set_visible(False),
                ))
                # Create track listing for this album
                create_track_listing(album, tracks)
                # scroll album view to top
                album_info_scrolled_window = library_stack.get_visible_child().get_child()
                album_info_scrolled_window.get_vadjustment().set_value(0)
                library_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)

            albums_flow.connect("child-activated", on_album_activated)

            # tracks
            tracks_sorted = list(self._tracks_data)
            if self.sort_by == "Artist":
                tracks_sorted.sort(key=lambda t: (norm(t.get("artist")), norm(t.get("name"))), reverse=desc)
            elif self.sort_by == "Album":
                tracks_sorted.sort(key=lambda t: (norm(t.get("album")), norm(t.get("name"))), reverse=desc)
            elif self.sort_by == "Duration":
                tracks_sorted.sort(key=lambda t: int(t.get("runtime_ticks") or 0), reverse=desc)
            else:
                tracks_sorted.sort(key=lambda t: norm(t.get("name")), reverse=desc)
            for t in tracks_sorted:
                left = f"{t.get('name','')} — {t.get('artist','')} · {t.get('album','')}".strip(" · ")
                add_row_two_col(tracks_list, left, format_duration(int(t.get("runtime_ticks") or 0)))

            # artists
            for ar in sorted(self._artists_data, key=lambda a: norm(a.get("name")), reverse=desc):
                row = Gtk.ListBoxRow()
                l = Gtk.Label(label=str(ar.get("name", "")))
                l.set_xalign(0)
                l.set_margin_start(12)
                l.set_margin_end(12)
                l.set_margin_top(6)
                l.set_margin_bottom(6)
                row.set_child(l)
                artists_list.append(row)

        # Sort actions
        def on_sort_by(action, value):
            action.set_state(value)
            self.sort_by = value.get_string()
            apply_sort_to_ui()

        def on_sort_order(action, value):
            action.set_state(value)
            self.sort_order_desc = value.get_string() == "Descending"
            apply_sort_to_ui()

        action_by = Gio.SimpleAction.new_stateful("sort.by", GLib.VariantType.new("s"), GLib.Variant("s", self.sort_by))
        action_by.connect("change-state", on_sort_by)
        self.add_action(action_by)
        action_order = Gio.SimpleAction.new_stateful("sort.order", GLib.VariantType.new("s"), GLib.Variant("s", "Ascending"))
        action_order.connect("change-state", on_sort_order)
        self.add_action(action_order)

        # Play/pause
        #playing = {"state": False}
        #def on_play_clicked(_btn: Gtk.Button):
        #    playing["state"] = not playing["state"]
        #    _btn.set_icon_name("media-playback-pause-symbolic" if playing["state"] else "media-playback-start-symbolic")
        #btn_play.connect("clicked", on_play_clicked)

        # Make load_library_sync a method reference
        def load_library_sync_func(jclient: JellyfinClient):
            import json, time
            self._client = jclient
            self._albums_data = []
            self._tracks_data = []
            self._artists_data = []
            self._album_tracks_cache = {}

            def getv(obj, key: str, default: str | None = None):
                if isinstance(obj, dict):
                    return obj.get(key, default)
                return getattr(obj, key, default)

            def add_albums(albums):
                for a in albums:
                    aid = (getv(a, "id") or getv(a, "Id") or "") or ""
                    tag = getv(a, "image_tag") or None
                    try:
                        img = jclient.image_path(str(aid), tag, max_width=512) if aid else None
                        img_path = str(img) if img else None
                    except Exception:
                        img_path = None
                    self._albums_data.append({
                        "id": str(aid),
                        "name": str(getv(a, "name") or ""),
                        "artist": str(getv(a, "artist") or ""),
                        "art_path": img_path,
                        "year": int(getv(a, "year") or 0),
                        "num_tracks": int(getv(a, "num_tracks") or 0),
                        "runtime_ticks": int(getv(a, "runtime_ticks") or 0),
                    })

            def add_tracks(tracks):
                for t in tracks:
                    self._tracks_data.append({
                        "id": str(getv(t, "id") or ""),
                        "name": str(getv(t, "name") or ""),
                        "artist": str(getv(t, "artist") or ""),
                        "album": str(getv(t, "album") or ""),
                        "album_id": str(getv(t, "album_id") or ""),
                        "runtime_ticks": int(getv(t, "runtime_ticks") or 0),
                        "year": int(getv(t, "year") or 0),
                    })

            def add_artists(artists):
                for ar in artists:
                    self._artists_data.append({
                        "id": str(getv(ar, "id") or ""),
                        "name": str(getv(ar, "name") or ""),
                    })

            cache_file = cache_dir() / "library.json"
            ttl_seconds = 600
            used_cache = False
            if cache_file.exists():
                try:
                    data = json.loads(cache_file.read_text())
                    ts = int(data.get("ts", 0))
                    if time.time() - ts <= ttl_seconds:
                        add_albums(data.get("albums", []))
                        add_tracks(data.get("tracks", []))
                        add_artists(data.get("artists", []))
                        self._album_tracks_cache = data.get("album_tracks", {})
                        used_cache = True
                except Exception:
                    used_cache = False

            if not used_cache:
                try:
                    albums = jclient.items(["MusicAlbum"], sort_by="AlbumArtist,Album,SortName")
                    tracks = jclient.items(["Audio"], sort_by="AlbumArtist,Album,SortName")
                    artists = jclient.items(["MusicArtist"], sort_by="SortName")
                except Exception as e:
                    add_row_two_col(tracks_list, "Error loading library", str(e))
                    return
                add_albums(albums)
                add_tracks(tracks)
                add_artists(artists)
                try:
                    album_tracks = {}
                    for a in albums:
                        try:
                            tracks_result = jclient.get_album_tracks(a)
                            if tracks_result and isinstance(tracks_result, list):
                                album_tracks[a.id] = [t.__dict__ for t in tracks_result]
                            else:
                                album_tracks[a.id] = []
                        except Exception as e:
                            print(f"[ERROR] Failed to get tracks for album {a.name}: {e}")
                            album_tracks[a.id] = []
                    data = {
                        "albums": [
                            {
                                "id": a.id,
                                "name": a.name,
                                "artist": a.artist,
                                "art_path": str(jclient.image_path(a.id, a.image_tag)) if hasattr(a, "image_tag") and jclient.image_path(a.id, a.image_tag) else None,
                                "year": getattr(a, "year", 0),
                                "num_tracks": getattr(a, "num_tracks", 0),
                                "runtime_ticks": getattr(a, "runtime_ticks", 0),
                            }
                            for a in albums
                        ],
                        "tracks": [
                            {"id": t.id, "name": t.name, "artist": t.artist, "album": t.album, "album_id": t.album_id, "runtime_ticks": t.runtime_ticks, "year": getattr(t, "year", 0)}
                            for t in tracks
                        ],
                        "artists": [
                            {"id": ar.id, "name": ar.name}
                            for ar in artists
                        ],
                        "album_tracks": album_tracks,
                        "ts": int(time.time()),
                    }
                    cache_file.write_text(json.dumps(data))
                    self._album_tracks_cache = album_tracks
                except Exception:
                    pass
            apply_sort_to_ui()
        
        # Store function reference as method
        self.load_library_sync = load_library_sync_func

        # Config/login
        cfg = load_config()
        if not cfg or not cfg.access_token or not cfg.server_url or not cfg.user_id:
            # Use login dialog from main window UI
            login_dialog: Adw.Dialog = builder.get_object("login_dialog")
            entry_server: Gtk.Entry = builder.get_object("entry_server")
            entry_username: Gtk.Entry = builder.get_object("entry_username")
            entry_password: Gtk.PasswordEntry = builder.get_object("entry_password")
            btn_login: Gtk.Button = builder.get_object("btn_login")
            login_error: Gtk.Label = builder.get_object("login_error")

            def do_login(_btn: Gtk.Button):
                server = entry_server.get_text().strip()
                username = entry_username.get_text().strip()
                password = entry_password.get_text().strip()
                if not server or not username:
                    login_error.set_label("Server URL and Username are required")
                    login_error.set_visible(True)
                    return
                try:
                    jl = JellyfinClient(server)
                    new_cfg = jl.login(username, password)
                    save_config(new_cfg)
                    login_dialog.close()
                    # Start async loading
                    self.load_library_async(jl, library_stack)
                except Exception as e:
                    login_error.set_label(f"Login failed: {e}")
                    login_error.set_visible(True)

            btn_login.connect("clicked", do_login)
            print("[jellyfin-gtk] presenting main window and login dialog…")
            window.present()
            login_dialog.present()
        else:
            client = JellyfinClient(cfg.server_url, cfg.access_token, cfg.user_id)
            user_avatar.set_text(cfg.username if cfg.username else "User")
            print("[jellyfin-gtk] presenting main window…")
            window.present()
            # Start async loading after window is presented
            self.load_library_async(client, library_stack)
        
        print("[DEBUG] do_activate completed successfully")


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv
    try:
        print("[DEBUG] Initializing Adwaita...")
        Adw.init()
        print("[DEBUG] Creating MusicApp...")
        app = MusicApp()
        print("[DEBUG] Running app...")
        result = app.run(argv)
        print(f"[DEBUG] App exited with code: {result}")
        return result
    except Exception as e:
        print(f"[ERROR] Exception in main: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
