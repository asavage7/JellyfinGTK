#!/usr/bin/env python3

import sys
import threading
import json
from pathlib import Path
from typing import Dict, List, Optional, Any, Callable
import gi
import random
from Pylette import extract_colors

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
gi.require_version('Gst', '1.0')

from gi.repository import Gtk, Gio, Adw, GLib, Pango, Gdk, Gst
from .config_store import load_config, save_config, clear_config, cache_dir, clear_cache_excluding_images
from .jellyfin_client import JellyfinClient, MediaItem, TrackItem
from PIL import Image, ImageFilter

APP_ID = "com.example.JellyfinGTK"

class MusicApp(Adw.Application):
    def __init__(self):
        super().__init__(application_id=APP_ID, flags=Gio.ApplicationFlags.FLAGS_NONE)
        self.config = load_config()
        self.jellyfin_client: Optional[JellyfinClient] = JellyfinClient(self.config.server_url, self.config.access_token, self.config.user_id, self.config.username) if self.config else None
        self.builder = Gtk.Builder.new_from_file(str(Path(__file__).resolve().parent.parent / "ui" / "window.ui"))
        self.ui_manager: UIManager = UIManager(self.builder, self)
        self.library_manager: LibraryManager = LibraryManager(self.jellyfin_client, cache_dir())
        self.login_manager: LoginManager = LoginManager(self)
        self.media_player: MediaPlayer = MediaPlayer(self)
        self.queue_manager: QueueManager = QueueManager(self)


    def do_activate(self):
        Adw.StyleManager.get_default().set_color_scheme(Adw.ColorScheme.PREFER_DARK)
        self.window = self.ui_manager.window
        self.window.set_application(self)

        if self.login_manager.login_with_token():
            # load_library performs network/cache work and should run off the
            # main thread. Keep that behavior, but ensure any UI updates it
            # triggers are scheduled on the main loop (see below).
            threading.Thread(target=self.load_library, daemon=True).start()

        else:
            self.ui_manager.show_login_dialog()

        print("reached present")
        self.window.present()
        self.ui_manager.update_sheet_height()

    def load_library(self):
        if not self.jellyfin_client:
            print("No Jellyfin client available")
            return
        if self.library_manager.load_library_cache():
            print("Loaded library from cache")
            threading.Thread(target=self.ui_manager.populate_album_grid_view, args=(self.library_manager.albums,), daemon=True).start()
            threading.Thread(target=self.ui_manager.populate_tracks_list_view, args=(self.library_manager.albums,), daemon=True).start()
            return
        if self.library_manager.load_library_jellyfin():
            print("Loaded library from Jellyfin")
            GLib.idle_add(self.ui_manager.populate_album_grid_view, self.library_manager.albums)
            GLib.idle_add(self.ui_manager.populate_tracks_list_view, self.library_manager.albums)
            return
        print("Failed to load library")



class UIManager:
    def __init__(self, builder: Gtk.Builder, app: MusicApp):
        self.builder = builder
        self.app = app
        self.login_window = None
        self.update_counter = 0
        self.init_ui_elements()
        self.connect_signals()
        self.add_style()

    def init_ui_elements(self):
        self.window: Adw.ApplicationWindow = self.builder.get_object("main_window")
        
        self.library_stack: Gtk.Stack = self.builder.get_object("library_stack")
        self.library_sidebar: Gtk.StackSidebar = self.builder.get_object("library_sidebar")
        self.albums_flow: Gtk.FlowBox = self.builder.get_object("albums_flow")
        self.tracks_list: Gtk.ListBox = self.builder.get_object("tracks_list")
        self.artists_list: Gtk.ListBox = self.builder.get_object("artists_list")

        self.album_info: Gtk.Box = self.builder.get_object("album_info")
        self.album_info_art: Gtk.Image = self.builder.get_object("album_view_album_art")
        self.album_info_title: Gtk.Label = self.builder.get_object("album_view_title_label")
        self.album_info_artist: Gtk.Label = self.builder.get_object("album_view_artist_label")
        self.album_info_additional: Gtk.Label = self.builder.get_object("album_view_additional_label")
        self.album_info_box: Gtk.Box = self.builder.get_object("album_info_box")
        self.album_view_play_button: Gtk.Button = self.builder.get_object("album_view_play_button")
        self.album_view_shuffle_button: Gtk.Button = self.builder.get_object("album_view_shuffle_button")

        self.login_dialog: Gtk.Dialog = self.builder.get_object("login_dialog")
        self.server_address: Adw.EntryRow = self.builder.get_object("entry_server")
        self.entry_username: Adw.EntryRow = self.builder.get_object("entry_username")
        self.entry_password: Adw.PasswordEntryRow = self.builder.get_object("entry_password")
        self.login_error: Gtk.Label = self.builder.get_object("login_error")
        self.btn_login: Gtk.Button = self.builder.get_object("btn_login")

        self.now_playing: Adw.BottomSheet = self.builder.get_object("now_playing")
        self.open_now_playing: Gtk.Button = self.builder.get_object("open_now_playing")
        self.now_playing_content_page: Adw.NavigationPage = self.builder.get_object("now_playing_content_page")

        self.play_pause_btn: Gtk.Button = self.builder.get_object("play_pause_btn")
        self.previous_track_btn: Gtk.Button = self.builder.get_object("previous_track_btn")
        self.next_track_btn: Gtk.Button = self.builder.get_object("next_track_btn")
        
        self.now_playing_album_art: Gtk.Image = self.builder.get_object("now_playing_art")
        self.now_playing_title: Gtk.Label = self.builder.get_object("now_playing_song_title")
        self.now_playing_artist: Gtk.Label = self.builder.get_object("now_playing_artist")
        self.now_playing_album: Gtk.Label = self.builder.get_object("now_playing_album_title")
        self.song_duration_label: Gtk.Label = self.builder.get_object("song_duration_label")
        self.time_remaining_label: Gtk.Label = self.builder.get_object("time_remaining_label")
        self.progress_scale: Gtk.Scale = self.builder.get_object("now_playing_progress_bar")
        self.progress_adjustment: Gtk.Adjustment = self.builder.get_object("now_playing_adjustment")

        self.sheet_album_art: Gtk.Image = self.builder.get_object("sheet_album_art")
        self.album_art_overlay: Gtk.Overlay = self.builder.get_object("album_art_overlay")
        self.sheet_song_title: Gtk.Label = self.builder.get_object("sheet_song_title")
        self.sheet_artist: Gtk.Label = self.builder.get_object("sheet_artist")
        self.sheet_album_title: Gtk.Label = self.builder.get_object("sheet_album_title")
        self.sheet_song_duration_label: Gtk.Label = self.builder.get_object("sheet_song_duration_label")
        self.sheet_time_remaining_label: Gtk.Label = self.builder.get_object("sheet_time_remaining_label")
        self.sheet_progress_scale: Gtk.Scale = self.builder.get_object("sheet_now_playing_progress_bar")
        self.sheet_adjustment: Gtk.Adjustment = self.builder.get_object("sheet_adjustment")
        self.queue_list_box: Gtk.ListBox = self.builder.get_object("queue_list_box")
        
        self.sheet_play_pause_btn: Gtk.Button = self.builder.get_object("sheet_play_pause_btn")
        self.sheet_previous_track_btn: Gtk.Button = self.builder.get_object("sheet_previous_track_btn")
        self.sheet_next_track_btn: Gtk.Button = self.builder.get_object("sheet_next_track_btn")
        
        self.album_art_overlay: Gtk.Overlay = self.builder.get_object("album_art_overlay")
        self.back_button: Gtk.Button = self.builder.get_object("back_button")

        css_provider = Gtk.CssProvider()
        css_provider.load_from_path(str(Path(__file__).resolve().parent.parent / "assets" / "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

        # Apply initial test gradient to verify the mechanism works
        GLib.timeout_add(1000, lambda: self._apply_gradient("rgba(255,100,50,0.9)", "rgba(100,50,255,0.8)"))

    def add_style(self):
        css_provider = Gtk.CssProvider()
        css_provider.load_from_path(str(Path(__file__).resolve().parent.parent / "assets" / "style.css"))
        Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def connect_signals(self):
        self.window.connect("notify::default-height", self.update_sheet_height)
        self.albums_flow.connect("child-activated", self.show_album_details)
        self.open_now_playing.connect("clicked", lambda _: self.now_playing.set_open(True))
        self.back_button.connect("clicked", self.on_back_button_clicked)
        self.album_view_play_button.connect("clicked", self.on_album_play_button_clicked)
        self.album_view_shuffle_button.connect("clicked", self.on_album_shuffle_button_clicked)
        self.next_track_btn.connect("clicked", lambda _: self.app.queue_manager.next_track())
        self.previous_track_btn.connect("clicked", self.on_previous_clicked)
        self.sheet_play_pause_btn.connect("clicked", lambda _: self.app.media_player.pause() if self.app.media_player.is_playing() else self.app.media_player.play())
        self.sheet_previous_track_btn.connect("clicked", self.on_previous_clicked)
        self.sheet_next_track_btn.connect("clicked", lambda _: self.app.queue_manager.next_track())
        self.play_pause_btn.connect("clicked", lambda _: self.app.media_player.pause() if self.app.media_player.is_playing() else self.app.media_player.play())
        self.queue_list_box.connect("row-activated", self.on_queue_item_activated)
        
    def load_art(self, item_id: str, image_tag: str, target_image: Gtk.Image, max_width: int = 128):
        """Load album art asynchronously without blocking the UI"""
        if not (self.app.jellyfin_client and item_id and image_tag):
            return
            
        def _background_load():
            """Background thread function to download/process image"""
            try:
                # Do the potentially slow network/file operations in background
                if not self.app.jellyfin_client:
                    return
                    
                art_path = self.app.jellyfin_client.image_path(item_id, image_tag, max_width=max_width)
                
                # Schedule the GTK update on the main thread
                def _set_art():
                    try:
                        if art_path and art_path.exists():
                            texture = Gdk.Texture.new_from_filename(str(art_path))
                            target_image.set_from_paintable(texture)
                    except Exception as e:
                        # Silently fail - widget might be destroyed or path invalid
                        pass
                    return False  # Remove from idle queue
                
                GLib.idle_add(_set_art)
                    
            except Exception as e:
                # Silently handle errors to avoid crashes
                pass
        
        # Start the background loading
        threading.Thread(target=_background_load, daemon=True).start()
    
    def set_gradient_background(self, item_id: str, image_tag: str):
        """Extract colors from album art and set a gradient background"""
        if not (self.app.jellyfin_client and item_id and image_tag):
            # Set default gradient if no image available
            GLib.idle_add(lambda: self._apply_gradient("rgba(50,50,80,1)", "rgba(20,20,40,1)"))
            return
            
        def _extract_colors_and_set_gradient():
            try:
                # Get the album art path
                if self.app.jellyfin_client:
                    art_path = self.app.jellyfin_client.image_path(item_id, image_tag, max_width=256)
                
                    if not art_path or not art_path.exists():
                        return

                    # Extract dominant colors from the image
                    pallete = extract_colors(str(art_path), palette_size=4)
                    #If the color is too dark or too light, adjust it to be better
                    nice_colors = []
                    for color in pallete.colors:
                        tmp_color = color.rgb
                        if sum(tmp_color) / 3 < 40:  # too dark
                            tmp_color = (min(tmp_color[0]+40,255), min(tmp_color[1]+40,255), min(tmp_color[2]+40,255))
                        elif sum(tmp_color) / 3 > 220:  # too light
                            tmp_color = (max(tmp_color[0]-60,0), max(tmp_color[1]-60,0), max(tmp_color[2]-60,0))
                        nice_colors.append(tmp_color)
                    # sort colors by brightness
                    nice_colors.sort(key=lambda c: sum(c) / 3, reverse=True)
                    for i in range(len(nice_colors)):
                        nice_colors[i] = f"rgba({nice_colors[i][0]},{nice_colors[i][1]},{nice_colors[i][2]},1)"
                    GLib.idle_add(lambda: self._apply_gradient(nice_colors[0], nice_colors[1], nice_colors[2], nice_colors[3]))

            except Exception as e:
                print(f"Failed to extract colors for {item_id}: {e}")
                # Set default gradient on error (include alpha channel)
                GLib.idle_add(lambda: self._apply_gradient("rgba(100,50,150,1)", "rgba(50,100,200,1)"))
        
        # Extract colors in a separate thread
        threading.Thread(target=_extract_colors_and_set_gradient, daemon=True).start()
    
    def _apply_gradient(self, color1: str, color2: str, color3: Optional[str] = None, color4: Optional[str] = None):
        """Apply a CSS gradient using CSS variables"""
        try:
            # Normalize color strings to compact rgba(r,g,b,a) format (no spaces)
            def _compact_rgba(s):
                import re
                # Capture optional alpha value. Accept both rgb(...) and rgba(...)
                match = re.search(r'rgba?\((\d+),\s*(\d+),\s*(\d+)(?:,\s*([0-9.]+))?\)', s)
                if match:
                    r, g, b, a = match.groups()
                    if a is None:
                        a = '1'
                    return f"rgba({r},{g},{b},{a})"
                return s

            c1 = _compact_rgba(color1)
            c2 = _compact_rgba(color2)
            c3 = _compact_rgba(color3) if color3 else c2
            c4 = _compact_rgba(color4) if color4 else c1

            print(f"Clean color1: {c1}")
            print(f"Clean color2: {c2}")
            print(f"Clean color3: {c3}")
            print(f"Clean color4: {c4}")

            # Build CSS string using variables; ensure semicolons and compact values
            # Provide four gradient stops so the stylesheet's 4-stop gradient has values
            # Derive colors 3 and 4 from the provided two colors to ensure visible blending
            css = (
                f".album_art_overlay {{ --gradient-color-1: {c1}; --gradient-color-2: {c2}; "
                f"--gradient-color-3: {c3}; --gradient-color-4: {c4}; }}"
            )

            css_provider = Gtk.CssProvider()
            try:
                css_provider.load_from_data(css.encode())
            except Exception as e:
                print(f"Failed to load CSS provider: {e} - CSS was: {css}")
                return

            # Remove old provider if present
            if hasattr(self, '_gradient_css_provider') and self._gradient_css_provider:
                try:
                    Gtk.StyleContext.remove_provider_for_display(Gdk.Display.get_default(), self._gradient_css_provider)
                except Exception:
                    pass
            self._gradient_css_provider = css_provider
            Gtk.StyleContext.add_provider_for_display(Gdk.Display.get_default(), css_provider, Gtk.STYLE_PROVIDER_PRIORITY_USER)

            print(f"Set CSS variables: {c1} -> {c2}")

        except Exception as e:
            print(f"Failed to set CSS variables: {e}")
        
    def populate_album_grid_view(self, albums: List[MediaItem]):
        self.albums_flow.remove_all()

        def create_album_tile(album: MediaItem) -> Gtk.Box:
            album_tile = self.builder.new_from_file(str(Path(__file__).resolve().parent.parent / "ui" / "album_tile.ui"))
            album_box: Gtk.Box = album_tile.get_object("album_tile_box")
            album_art: Gtk.Image = album_tile.get_object("album_tile_art")
            album_title: Gtk.Label = album_tile.get_object("album_tile_title")
            album_artist: Gtk.Label = album_tile.get_object("album_tile_artist")

            album_art.set_from_icon_name("image-missing")
            album_box.set_name(album.id or "unknown_album")
            album_title.set_text(album.name or "Unknown Album")
            album_artist.set_text(album.artist or "Unknown Artist")

            try:
                if album and hasattr(album, 'image_tag') and album.image_tag:
                    self.load_art(album.id, album.image_tag, album_art, 160)
            except Exception as e:
                pass  # Silently handle any errors

            return album_box
        
        for album in albums:
            album_tile = create_album_tile(album)
            GLib.idle_add(self.albums_flow.append, album_tile)
            
        self.library_stack.set_visible_child_name("albums")

    def populate_tracks_list_view(self, albums: List[MediaItem]):

        self.index = 1
        self.tracks_list.remove_all()

        def create_track_row(album: MediaItem, track: TrackItem) -> Gtk.CenterBox:
            # Create a separate builder instance for this row
            track_row_builder = Gtk.Builder()
            track_row_builder.add_from_file(str(Path(__file__).resolve().parent.parent / "ui" / "tracks_view_row.ui"))
            track_row_box: Gtk.CenterBox = track_row_builder.get_object("track_row_box")
            track_number: Gtk.Label = track_row_builder.get_object("track_row_number")
            track_name: Gtk.Label = track_row_builder.get_object("track_row_title")
            track_artist: Gtk.Label = track_row_builder.get_object("track_row_artist")
            track_album: Gtk.Label = track_row_builder.get_object("track_row_album")
            track_row_art: Gtk.Image = track_row_builder.get_object("track_row_art")
            track_number.set_text(f"{self.index}")
            self.index += 1
            track_name.set_text(track.name or "Unknown Track")
            track_artist.set_text(track.artist or "Unknown Artist")
            track_album.set_text(album.name or "Unknown Album")

            try:
                if album and hasattr(album, 'image_tag') and album.image_tag:
                    self.load_art(album.id, album.image_tag, track_row_art, 42)
            except Exception as e:
                pass  # Silently handle any errors
            return track_row_box
        


        for album in albums:
            if album.tracks:
                for track in album.tracks:
                    GLib.idle_add(self.tracks_list.append, create_track_row(album, track))

    def show_album_details(self, flowbox, child):
        album_id = child.get_child().get_name()
        album = self.app.library_manager.get_album_by_id(album_id)
        if album is None:
            print("Album not found in show_album_details")
            return
        self.album_info_art.set_from_icon_name("image-missing")
        self.album_info.set_name(album.id or "unknown_album")
        self.album_info_title.set_text(album.name or "Unknown Album")
        self.album_info_artist.set_text(album.artist or "Unknown Artist")
        additional_info = []
        if album.year:
            additional_info.append(str(album.year))
        if album.runtime_ticks:
            additional_info.append(f"{self.app.library_manager.convert_runtime_ticks_to_readable(album.runtime_ticks)}")
        if album.num_tracks:
            additional_info.append(f"{album.num_tracks} Tracks")
        self.album_info_additional.set_text(" • ".join(additional_info))
        try:
            if album and hasattr(album, 'image_tag') and album.image_tag:
                self.load_art(album.id, album.image_tag, self.album_info_art, 128)
        except Exception as e:
            pass  # Silently handle any errors


        child = self.album_info_box.get_first_child()
        while child:
            next_child = child.get_next_sibling()
            if (isinstance(child, Gtk.ListBox) and child.get_name() == "track_listing") or \
               (isinstance(child, Gtk.Label) and child.get_name() == "disc_label"):
                self.album_info_box.remove(child)
            child = next_child

        if not album.tracks:
            no_tracks_label = Gtk.Label(label="No Tracks Available")
            no_tracks_label.set_name("no_tracks_label")
            no_tracks_label.set_margin_top(8)
            no_tracks_label.set_margin_bottom(12)
            no_tracks_label.add_css_class("heading")
            self.album_info_box.append(no_tracks_label)
        else:
            discs = {}
            for track in album.tracks:
                disc_number = track.disc_number or 1
                if disc_number not in discs:
                    discs[disc_number] = []
                # append tracks sorted by track number within each disc
                discs[disc_number].append(track)
            discs = {k: sorted(v, key=lambda t: t.track_number or 0) for k, v in discs.items()}

            has_multiple_discs = len(discs) > 1
            for disc_number in sorted(discs.keys()):
                if has_multiple_discs:
                    disc_label = Gtk.Label()
                    disc_label.set_name("disc_label")
                    disc_label.set_text(f"Disc {disc_number}")
                    disc_label.set_halign(Gtk.Align.START)
                    disc_label.add_css_class("heading")
                    disc_label.set_margin_top(16 if disc_number > 1 else 8)
                    disc_label.set_margin_bottom(8)
                    self.album_info_box.append(disc_label)

                track_list = Gtk.ListBox()
                track_list.set_name("track_listing")
                track_list.add_css_class("boxed-list")
                track_list.set_selection_mode(Gtk.SelectionMode.NONE)
                track_list.connect("row-activated", self.on_track_activated)

                for track in discs[disc_number]:
                    track_row = self.builder.new_from_file(str(Path(__file__).resolve().parent.parent / "ui" / "track_row.ui"))
                    track_row_box: Gtk.CenterBox = track_row.get_object("track_row_box")
                    track_row_box.set_name(track.id or "unknown_track")
                    track_number: Gtk.Label = track_row.get_object("track_row_number")
                    track_name: Gtk.Label = track_row.get_object("track_row_title")
                    track_duration: Gtk.Label = track_row.get_object("track_row_duration")
                    track_number.set_text(f"{track.track_number or '-'}")
                    track_name.set_text(track.name or "Unknown Track")
                    track_duration.set_text(self.app.library_manager.convert_runtime_ticks_to_readable(track.runtime_ticks))
                    track_list.append(track_row_box)
                    self.add_track_row_context_menu(track_row_box, track)
                    
                self.album_info_box.append(track_list)

        self.library_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT)
        self.library_stack.set_visible_child_name("album_info")
        self.back_button.set_visible(True)

    def on_back_button_clicked(self, button: Gtk.Button):
        self.library_stack.set_transition_type(Gtk.StackTransitionType.SLIDE_RIGHT)
        self.library_stack.set_visible_child_name("albums")
        self.back_button.set_visible(False)
        
    def update_queue_view(self):
        self.queue_list_box.remove_all()
        for track in self.app.queue_manager.queue:
            track_row = self.builder.new_from_file(str(Path(__file__).resolve().parent.parent / "ui" / "queue_row.ui"))
            track_row_box: Gtk.CenterBox = track_row.get_object("queue_row_box")
            track_icon: Gtk.Image = track_row.get_object("queue_row_icon")
            track_icon.set_from_icon_name("image-missing")
            track_name: Gtk.Label = track_row.get_object("queue_row_title")
            track_artist: Gtk.Label = track_row.get_object("queue_row_artist")
            track_duration: Gtk.Label = track_row.get_object("queue_row_duration")
            track_index: Gtk.Label = track_row.get_object("queue_row_index")
            track_duration.set_text(self.app.library_manager.convert_runtime_ticks_to_readable(track.runtime_ticks or 0))
            track_name.set_text(track.name or "Unknown Track")
            track_artist.set_text(track.artist or "Unknown Artist")
            track_index.set_text(f"{self.app.queue_manager.queue.index(track) + 1}")
            self.load_art(track.album_id or "", track.image_tag or "", track_icon, 42)
            self.add_queue_context_menu(track_row_box, self.app.queue_manager.queue.index(track))
            self.queue_list_box.append(track_row_box)
        self.update_highlighted_queue_item()

    def update_highlighted_queue_item(self):
        if self.app.queue_manager.get_current_track():
            self.queue_list_box.select_row(self.queue_list_box.get_row_at_index(self.app.queue_manager.current_index))
            
    def on_queue_item_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow):
        index = row.get_index()
        self.app.queue_manager.start_playback_from_index(index)
            
    def on_album_play_button_clicked(self, button: Gtk.Button):
        album_id = self.album_info.get_name()
        album = self.app.library_manager.get_album_by_id(album_id)
        if album is None:
            print("Album not found in on_album_play_button_clicked")
            return
        self.app.queue_manager.clear_queue()
        self.app.queue_manager.add_album_to_queue(album)
        self.app.queue_manager.start_playback()
        
    def on_album_shuffle_button_clicked(self, button: Gtk.Button):
        album_id = self.album_info.get_name()
        album = self.app.library_manager.get_album_by_id(album_id)
        if album is None:
            print("Album not found in on_album_shuffle_button_clicked")
            return
        self.app.queue_manager.clear_queue()
        self.app.queue_manager.add_album_to_queue(album)
        self.app.queue_manager.shuffle_queue()
        self.app.queue_manager.start_playback()

    def on_track_activated(self, listbox: Gtk.ListBox, row: Gtk.ListBoxRow):
        row_index = row.get_index()
        album_id = self.album_info.get_name()
        album = self.app.library_manager.get_album_by_id(album_id)
        track_id = row.get_child().get_name()
        track = self.app.library_manager.get_track_by_id(album_id, track_id) if track_id else None
        if album and track:
            self.app.queue_manager.clear_queue()
            self.app.queue_manager.add_album_to_queue(album)
            self.app.queue_manager.start_playback_from_track(track)
            
    def on_previous_clicked(self, button: Gtk.Button):
        if self.app.media_player.get_position() > 30000000:  # 3 seconds in ticks
            self.app.media_player.set_position(0)
        else:
            self.app.queue_manager.previous_track()

    def set_now_playing_info(self, track: Optional[TrackItem]):
        if track is None:
            self.now_playing_title.set_text("No Track Playing")
            self.now_playing_artist.set_text("")
            self.now_playing_album.set_text("")
            self.now_playing_album_art.set_from_icon_name("image-missing")
            self.sheet_song_title.set_text("No Track Playing")
            self.sheet_artist.set_text("")
            self.sheet_album_title.set_text("")
            self.sheet_album_art.set_from_icon_name("image-missing")
            return
        self.now_playing_title.set_text(track.name or "Unknown Track")
        self.now_playing_artist.set_text(track.artist or "Unknown Artist")
        self.now_playing_album.set_text(track.album or "Unknown Album")
        self.sheet_song_title.set_text(track.name or "Unknown Track")
        self.sheet_artist.set_text(track.artist or "Unknown Artist")
        self.sheet_album_title.set_text(track.album or "Unknown Album")
        
        self.progress_adjustment.set_lower(0)
        self.progress_adjustment.set_upper(track.runtime_ticks or 0)
        self.progress_adjustment.set_value(0)
        self.sheet_adjustment.set_lower(0)
        self.sheet_adjustment.set_upper(track.runtime_ticks or 0)
        self.sheet_adjustment.set_value(0)

        # Load album art for both now playing views
        album: Optional[MediaItem] = self.app.library_manager.get_album_by_id(track.album_id) if track.album_id else None
        try:
            if album and hasattr(album, 'image_tag') and album.image_tag:
                # Load art for both the main now playing view and the sheet view
                self.load_art(album.id, album.image_tag, self.now_playing_album_art, 128)
                self.load_art(album.id, album.image_tag, self.sheet_album_art, 256)
                # Set gradient background for the now playing sheet
                self.set_gradient_background(album.id, album.image_tag)
        except Exception as e:
            # Set fallback icons on error
            try:
                self.now_playing_album_art.set_from_icon_name("image-missing")
                self.sheet_album_art.set_from_icon_name("image-missing")
                # Set default gradient on error
                self._apply_gradient("rgba(100,50,150,0.8)", "rgba(50,100,200,0.6)")
            except:
                pass
        else:
            # Set fallback icons if no album art available
            self.now_playing_album_art.set_from_icon_name("image-missing")
            self.sheet_album_art.set_from_icon_name("image-missing")
            # Set default gradient when no album art
            self._apply_gradient("rgba(50,50,80,0.8)", "rgba(20,20,40,0.9)")


        self.update_now_playing_info(track)

    def update_now_playing_info(self, track: TrackItem):
        self.progress_adjustment.set_value(self.app.media_player.get_position() if track.runtime_ticks else 0)
        self.sheet_adjustment.set_value(self.app.media_player.get_position() if track.runtime_ticks else 0)
        self.song_duration_label.set_text(self.app.library_manager.convert_runtime_ticks_to_readable(track.runtime_ticks or 0))
        remaining_ticks = (track.runtime_ticks or 0) - (self.app.media_player.get_position() if track.runtime_ticks else 0)
        self.time_remaining_label.set_text(f"-{self.app.library_manager.convert_runtime_ticks_to_readable(int(remaining_ticks))}")
        
    def add_queue_context_menu(self, row: Gtk.ListBoxRow, index: int):
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
        remove_action.connect("activate", lambda action, param: self.app.queue_manager.remove_track_from_queue(index))
        action_group.add_action(remove_action)
        
        # Play next action (move track to be next in queue)
        play_next_action = Gio.SimpleAction.new(f"play_next.{index}", None)
        play_next_action.connect("activate", lambda action, param: self.app.queue_manager.move_track_to_next(index))
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
        
    def add_track_row_context_menu(self, row, track: TrackItem):
        menu_model = Gio.Menu()
        menu_model.append("Play Next", f"track.play_next.{track.id}")
        menu_model.append("Add to Queue", f"track.add_to_queue.{track.id}")
        
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
        
        # Create action group for this specific track item
        action_group = Gio.SimpleActionGroup()
        
        add_to_queue_action = Gio.SimpleAction.new(f"add_to_queue.{track.id}", None)
        add_to_queue_action.connect("activate", lambda action, param: self.app.queue_manager.add_track_to_queue(track))
        action_group.add_action(add_to_queue_action)
        
        play_next_action = Gio.SimpleAction.new(f"play_next.{track.id}", None)
        play_next_action.connect("activate", lambda action, param: self.app.queue_manager.insert_track_next(track))
        action_group.add_action(play_next_action)
        
        # Insert the action group with "track" prefix to match menu items
        row.insert_action_group("track", action_group)
        
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
        
    def update_sheet_height(self, *args):
        def set_height():
            window_height = self.window.get_height()
            # Ensure height is positive and reasonable
            overlay_height = max(200, window_height - 46) if window_height > 46 else 200
            self.now_playing_content_page.set_property("height-request", overlay_height)
            return False  # Only run once
        GLib.timeout_add(0, set_height)

    def get_ui_element(self, name: str) -> Optional[Gtk.Widget]:
        return self.builder.get_object(name)
    
    def show_login_dialog(self):
        self.btn_login.connect("clicked", self.app.login_manager.login(self.server_address.get_text(), self.entry_username.get_text(), self.entry_password.get_text()))
        self.login_dialog.show()

class LoginManager:
    def __init__(self, app: MusicApp):
        self.app = app

    def login_with_token(self) -> bool:
        if self.app.config is None:
            return False
        elif self.app.config.access_token and self.app.config.server_url and self.app.config.user_id:
            self.app.jellyfin_client = JellyfinClient(self.app.config.server_url, self.app.config.access_token, self.app.config.user_id, self.app.config.username)
            print("User is logged in")
            return True
        else:
            return False
    def login(self, server_url: str, username: str, password: str):
        try:
            self.app.jellyfin_client = JellyfinClient(server_url)
            config = self.app.jellyfin_client.login(username, password)
            self.app.config = config
            save_config(config)
            return True
        except Exception as e:
            print(f"Login failed: {e}")
            return False

class LibraryManager:
    def __init__(self, client: Optional[JellyfinClient], cache_dir: Path):
        self.albums: List[MediaItem] = []
        self.client = client
        self.cache_dir = cache_dir
        self.library_cache = self.cache_dir / "library.json"

    def create_cache(self):
        if not self.cache_dir.exists():
            self.cache_dir.mkdir(parents=True)
        with open(self.library_cache, "w") as f:
            json.dump({"albums": []}, f)

    def load_library_cache(self):
        if self.library_cache.exists():
            with open(self.library_cache, "r") as f:
                try:
                    data = json.load(f)
                    for album in data.get("albums", []):
                        self.albums.append(MediaItem.from_dict(album))
                    return True
                except json.JSONDecodeError:
                    print("Failed to decode library cache")
                except Exception as e:
                    print(f"Error loading library cache: {e}")
            return False
        else:
            self.create_cache()
            return False

    def save_library_cache(self):
        with open(self.library_cache, "w") as f:
            json.dump({"albums": [album.to_dict() for album in self.albums]}, f)

    def load_library_jellyfin(self):
        if not self.client:
            print("No Jellyfin client available")
            return
        self.albums = self.client.items(include_types=["MusicAlbum"])
        temp_albums = []
        for album in self.albums:
            if not self.client:
                continue
            album.tracks = self.client.get_album_tracks(album)
            temp_albums.append(album)
        self.albums = temp_albums
        self.save_library_cache()
            
        return True

    def get_album_by_id(self, album_id: str) -> Optional[MediaItem]:
        album = next((a for a in self.albums if a.id == album_id), None)
        if album is None:
            print("Album not found")
            return
        return album
    
    def get_track_by_id(self, album_id: str, track_id: str) -> Optional[TrackItem]:
        album = self.get_album_by_id(album_id)
        if album and album.tracks:
            track = next((t for t in album.tracks if t.id == track_id), None)
            return track
        return None

    def convert_runtime_ticks_to_readable(self, ticks: int) -> str:
        seconds = ticks // 10000000
        minutes = seconds // 60
        hours = minutes // 60
        if hours > 0:
            return f"{hours}:{minutes % 60:02d}:{seconds % 60:02d}"
        else:
            return f"{minutes}:{seconds % 60:02d}"
        
class MediaPlayer:
    """Handles media playback using GStreamer"""

    def __init__(self, app: MusicApp):
        Gst.init(None)
        self.player = Gst.ElementFactory.make("playbin", "player")
        if not self.player:
            raise RuntimeError("Failed to create GStreamer playbin element")
        self.app = app
        self.client = app.jellyfin_client
        self.playback_monitor_id = None
        self.manual_skip = False  # Flag to indicate manual track skip
        self.expecting_gapless = False  # Flag to indicate we're expecting a gapless transition
        # Attach bus watch so we can detect EOS/Error and advance the queue
        try:
            self.bus = self.player.get_bus()
            self.bus.add_signal_watch()
            self.bus.connect("message", self._on_bus_message)
            # Connect about-to-finish signal for gapless playback
            self.player.connect("about-to-finish", self._on_about_to_finish)
        except Exception as e:
            print(f"[DEBUG] Failed to attach GStreamer bus watch: {e}")

    def set_uri(self, uri: str):
        """Set the media URI to play"""
        self.player.set_property("uri", uri)
        
    def play_id(self, media_id: str):
        """Set the media ID to play (assuming a valid URI scheme)"""
        uri = self.client.get_track_stream_url(media_id) if self.client else ""
        
        # Set new URI and start playback - playbin handles the transition
        self.set_uri(uri)
        self.play()
    
    def play(self):
        """Start playback"""
        self.app.ui_manager.set_now_playing_info(self.app.queue_manager.get_current_track())
        self.start_playback_monitor()
        self.player.set_state(Gst.State.PLAYING)
        
        
    def pause(self):
        """Pause playback"""
        self.player.set_state(Gst.State.PAUSED)
        self.stop_playback_monitor()

    def stop(self):
        """Stop playback"""
        self.player.set_state(Gst.State.NULL)
        self.app.ui_manager.set_now_playing_info(None)
        self.stop_playback_monitor()

    def is_playing(self) -> bool:
        """Check if currently playing"""
        state = self.player.get_state(0).state
        return state == Gst.State.PLAYING
    
    def get_position(self) -> float:
        """Get current position in ticks"""
        try:
            success, position = self.player.query_position(Gst.Format.TIME)
            if success:
                return position / 100  # convert nanoseconds to ticks
            else:
                return 0.0
        except Exception as e:
            print(f"[DEBUG] GStreamer position query exception: {e}")
            return 0.0
    
    def get_duration(self) -> float:
        """Get total duration in ticks (with 0.1s precision)"""
        try:
            success, duration = self.player.query_duration(Gst.Format.TIME)
            if success:
                return duration / 100  # convert nanoseconds to ticks
            else:
                return 0.0
        except Exception as e:
            print(f"[DEBUG] GStreamer duration query exception: {e}")
            return 0.0
    
    def set_position(self, ticks: float):
        """Seek to position in ticks (accepts fractional ticks)"""
        try:
            nanoseconds = int(ticks * 10000000)
            self.player.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, nanoseconds)
        except Exception:
            pass
        
    def start_playback_monitor(self, interval_ms: int = 1000):
        """Start a periodic timer to update playback position"""
        if hasattr(self, 'playback_monitor_id') and self.playback_monitor_id:
            GLib.source_remove(self.playback_monitor_id)
        def _tick():
            try:
                track = self.app.queue_manager.get_current_track()
                # update_now_playing_info returns None; GLib expects the callback
                # to return True to keep the timeout active.
                if track:
                    self.app.ui_manager.update_now_playing_info(track)
            except Exception as e:
                print(f"Playback monitor tick error: {e}")
            return True

        self.playback_monitor_id = GLib.timeout_add(interval_ms, _tick)


    def stop_playback_monitor(self):
        """Stop the periodic timer"""
        if self.playback_monitor_id:
            GLib.source_remove(self.playback_monitor_id)
        self.playback_monitor_id = None

    def _on_bus_message(self, bus, message):
        """Handle GStreamer bus messages (runs in the GLib main context).

        We only care about EOS (end-of-stream) and ERROR here.
        """
        try:
            t = message.type
            if t == Gst.MessageType.EOS:
                # Schedule handling on the main loop
                GLib.idle_add(self._handle_eos)
            elif t == Gst.MessageType.STREAM_START:
                # Handle stream start for gapless transitions
                GLib.idle_add(self._handle_stream_start)
            elif t == Gst.MessageType.ERROR:
                try:
                    err, dbg = message.parse_error()
                    print(f"[GStreamer] Error: {err.message} ({dbg})")
                except Exception:
                    print("[GStreamer] Unknown error message")
                GLib.idle_add(self._handle_error)
        except Exception as e:
            print(f"[DEBUG] Exception in bus message handler: {e}")

    def _on_about_to_finish(self, playbin):
        """Handle about-to-finish signal for gapless playback"""
        try:
            # Skip gapless setup if manual skip is in progress
            if self.manual_skip:
                return
            
            # Get the next track without advancing the queue yet
            if self.app.queue_manager.current_index + 1 < len(self.app.queue_manager.queue):
                next_track = self.app.queue_manager.queue[self.app.queue_manager.current_index + 1]
                next_uri = self.client.get_track_stream_url(next_track.id) if self.client else ""
                # Set flag that we're expecting a gapless transition
                self.expecting_gapless = True
                # Set the next URI for gapless playback
                playbin.set_property("uri", next_uri)
        except Exception as e:
            print(f"Error in about-to-finish handler: {e}")

    def _handle_stream_start(self):
        """Handle stream start for gapless transitions - this fires when a new track starts"""
        try:
            # Only advance if we're expecting a gapless transition
            if self.expecting_gapless and not self.manual_skip:
                self.expecting_gapless = False  # Clear the flag
                self.app.queue_manager.current_index += 1
                current_track = self.app.queue_manager.get_current_track()
                if current_track:
                    # Schedule UI updates on main thread and restart playback monitor
                    def update_ui():
                        self.app.ui_manager.set_now_playing_info(current_track)
                        self.app.ui_manager.update_queue_view()
                        # Restart playback monitor for the new track
                        self.start_playback_monitor()
                        return False
                    GLib.idle_add(update_ui)
        except Exception as e:
            print(f"Error handling stream start: {e}")
        return False

    def _handle_eos(self):
        """Handle end-of-stream - fallback for non-gapless transitions"""
        try:
            # For non-gapless EOS (end of queue or manual handling)
            if self.app.queue_manager.current_index + 1 < len(self.app.queue_manager.queue):
                # This shouldn't happen with gapless, but handle it anyway
                self.app.queue_manager.current_index += 1
                current_track = self.app.queue_manager.get_current_track()
                if current_track:
                    self.play_id(current_track.id)
            else:
                # No more tracks, stop playback
                self.stop()
        except Exception as e:
            print(f"Error handling EOS: {e}")
        return False

    def _handle_error(self):
        try:
            # stop playback and clear now playing
            self.stop()
            self.app.ui_manager.update_now_playing_info(None)  # type: ignore[arg-type]
        except Exception:
            pass
        return False
    
class QueueManager:
    def __init__(self, app: MusicApp):
        self.app = app
        self.queue: List[TrackItem] = []
        self.current_index: int = -1  # No track is playing initially

    def add_track_to_queue(self, track: TrackItem):
        """Add a single track to the queue"""
        self.queue.append(track)
        # Set current index to first track if queue was empty, but don't auto-play
        if self.current_index == -1:
            self.current_index = 0
        self.app.ui_manager.update_queue_view()
    
    def add_album_to_queue(self, album: MediaItem):
        """Add all tracks from an album to the queue in proper order"""
        if album.tracks:
            self.queue.extend(sorted(album.tracks, key=lambda t: (t.disc_number or 0, t.track_number or 0)))
            # Set current index to first track if queue was empty, but don't auto-play
            if self.current_index == -1:
                self.current_index = 0
            self.app.ui_manager.update_queue_view()
            
    def remove_track_from_queue(self, index: int):
        """Remove a track from the queue by index"""
        if 0 <= index < len(self.queue):
            del self.queue[index]
            # Adjust current index if necessary
            if index < self.current_index:
                self.current_index -= 1
            elif index == self.current_index:
                # If we removed the currently playing track, stop playback
                self.app.media_player.stop()
                if self.queue:
                    # If there are still tracks, play the next one
                    if self.current_index >= len(self.queue):
                        self.current_index = len(self.queue) - 1
                    self.start_playback()
                else:
                    self.current_index = -1  # No tracks left
            self.app.ui_manager.update_queue_view()
            
    def move_track_to_next(self, index: int):
        """Move a track to be the next in the queue"""
        if 0 <= index < len(self.queue) and self.current_index != -1:
            track = self.queue.pop(index)
            next_index = self.current_index + 1
            if index < self.current_index:
                next_index -= 1  # Adjust for removal before current
            self.queue.insert(next_index, track)
            self.app.ui_manager.update_queue_view()
            
    def shuffle_queue(self):
        """Shuffle the current queue"""
        random.shuffle(self.queue)
        self.current_index = 0 if self.queue else -1
        self.app.ui_manager.update_queue_view()
            
    def start_playback(self):
        """Start playback from the beginning of the queue or resume from current position"""
        if self.queue:
            if self.current_index == -1:
                self.current_index = 0
            self.app.media_player.play_id(self.queue[self.current_index].id)
            self.app.ui_manager.update_highlighted_queue_item()
            
    def start_playback_from_index(self, index: int):
        """Start playback from a specific index in the queue"""
        if 0 <= index < len(self.queue):
            self.current_index = index
            track = self.queue[index]
            
            # Set manual skip flag to override gapless behavior (like manual next track)
            self.app.media_player.manual_skip = True
            
            # For manual track selection, reset pipeline to ensure clean playback
            uri = self.app.media_player.client.get_track_stream_url(track.id) if self.app.media_player.client else ""
            self.app.media_player.player.set_state(Gst.State.NULL)
            self.app.media_player.set_uri(uri)
            self.app.media_player.play()
            
            # Update queue highlighting after other UI updates
            GLib.idle_add(self.app.ui_manager.update_highlighted_queue_item)
            
            # Clear the manual skip flag after a short delay
            GLib.timeout_add(1000, lambda: setattr(self.app.media_player, 'manual_skip', False) or False)
            
    def start_playback_from_track(self, track: TrackItem):
        """Start playback from a specific track in the queue"""
        index = next((i for i, t in enumerate(self.queue) if t.id == track.id), -1)
        if index != -1:
            self.start_playback_from_index(index)

    def get_current_track(self) -> Optional[TrackItem]:
        if 0 <= self.current_index < len(self.queue):
            return self.queue[self.current_index]
        return None
    
    def insert_track_next(self, track: TrackItem):
        if self.current_index == -1:
            self.add_track_to_queue(track)
        else:
            self.queue.insert(self.current_index + 1, track)
            self.app.ui_manager.update_queue_view()
            
    def next_track(self) -> Optional[TrackItem]:
        """Manually advance to next track (for user-initiated skips)"""
        if self.current_index + 1 < len(self.queue):
            self.current_index += 1
            next_track = self.queue[self.current_index]
            # Set manual skip flag to override gapless behavior
            self.app.media_player.manual_skip = True
            # For manual skips, we need to force a new URI and restart playback
            uri = self.app.media_player.client.get_track_stream_url(next_track.id) if self.app.media_player.client else ""
            self.app.media_player.player.set_state(Gst.State.NULL)  # Reset pipeline for manual skip
            self.app.media_player.set_uri(uri)
            self.app.media_player.play()
            self.app.ui_manager.update_highlighted_queue_item()
            # Clear the manual skip flag after a short delay
            GLib.timeout_add(1000, lambda: setattr(self.app.media_player, 'manual_skip', False) or False)
            return next_track
        return None
    
    def previous_track(self) -> Optional[TrackItem]:
        """Manually go back to previous track (for user-initiated skips)"""
        if self.current_index > 0:
            self.current_index -= 1
            prev_track = self.queue[self.current_index]
            # Set manual skip flag to override gapless behavior
            self.app.media_player.manual_skip = True
            # For manual skips, we need to force a new URI and restart playback
            uri = self.app.media_player.client.get_track_stream_url(prev_track.id) if self.app.media_player.client else ""
            self.app.media_player.player.set_state(Gst.State.NULL)  # Reset pipeline for manual skip
            self.app.media_player.set_uri(uri)
            self.app.media_player.play()
            self.app.ui_manager.update_highlighted_queue_item()
            # Clear the manual skip flag after a short delay
            GLib.timeout_add(1000, lambda: setattr(self.app.media_player, 'manual_skip', False) or False)
            return prev_track
        return None

    def clear_queue(self):
        self.queue.clear()
        self.app.media_player.stop()
        self.current_index = -1
        self.app.ui_manager.update_queue_view()


def main():
    app = MusicApp()
    app.run()

if __name__ == "__main__":
    main()