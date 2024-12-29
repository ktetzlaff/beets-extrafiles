"""Tests for the beets-extrafiles plugin."""

import logging
import os
import shutil
import tempfile
import unittest.mock
from pathlib import Path

import beets
import beets.library
import beets.util
import confuse

import beetsplug.extrafiles

RSRC = Path(__file__).parent / "rsrc"

log = logging.getLogger("beets")
log.propagate = True
log.setLevel(logging.DEBUG)


def debug_print_tree(path: Path, prefix: str = "") -> None:
    if not prefix:
        log.debug(f"{path}")
    for item in path.iterdir():
        if item.is_dir():
            log.debug(f"{prefix}├── {item.name}/")
            debug_print_tree(item, prefix + "│   ")
        else:
            log.debug(f"{prefix}├── {item.name}")


class BaseTestCase(unittest.TestCase):
    """Base testcase class that sets up example files."""

    PLUGIN_CONFIG = {
        "extrafiles": {
            "patterns": {
                "log": ["*.log"],
                "cue": ["*.cue", "*/*.cue"],
                "artwork": ["scans/", "Scans/", "artwork/", "Artwork/"],
            },
            "paths": {
                "artwork": "$albumpath/artwork",
                "log": "$albumpath/audio",
            },
        },
    }

    def _create_example_file(self, path: Path) -> None:
        path.open(mode="w").close()

    def _create_artwork_files(self, path: Path) -> None:
        path.mkdir()
        for filename in ("front.jpg", "back.jpg"):
            self._create_example_file(path / filename)

    def setUp(self) -> None:
        """Set up example files and instanciate the plugin."""
        self.srcdir = tempfile.TemporaryDirectory(suffix="src")
        self.dstdir = tempfile.TemporaryDirectory(suffix="dst")
        self.srcpath = Path(self.srcdir.name)
        self.dstpath = Path(self.dstdir.name)

        # Create example files for single directory album
        (self.dstpath / "single").mkdir(parents=True)
        sourcedir = self.srcpath / "single"
        sourcedir.mkdir(parents=True)
        shutil.copy(
            RSRC / "full.mp3",
            sourcedir / "file.mp3",
        )
        for filename in ("file.cue", "file.txt", "file.log"):
            self._create_example_file(sourcedir / filename)
        self._create_artwork_files(sourcedir / "scans")

        # Create example files for multi-directory album
        (self.dstpath / "multiple").mkdir(parents=True)
        sourcedir = self.srcpath / "multiple"
        (sourcedir / "CD1").mkdir(parents=True)
        shutil.copy(
            RSRC / "full.mp3",
            sourcedir / "CD1" / "file.mp3",
        )
        (sourcedir / "CD2").mkdir(parents=True)
        shutil.copy(
            RSRC / "full.mp3",
            sourcedir / "CD2" / "file.mp3",
        )
        for filename in ("file.txt", "file.log"):
            self._create_example_file(sourcedir / filename)
        for discdir in ("CD1", "CD2"):
            self._create_example_file(sourcedir / discdir / "file.cue")
        self._create_artwork_files(sourcedir / "scans")

        # Set up plugin instance
        config = confuse.RootView(
            sources=[
                confuse.ConfigSource.of(self.PLUGIN_CONFIG),
            ]
        )

        with unittest.mock.patch(
            "beetsplug.extrafiles.beets.plugins.beets.config",
            config,
        ):
            self.plugin = beetsplug.extrafiles.ExtraFilesPlugin()

    def tearDown(self) -> None:
        """Remove the example files."""
        self.srcdir.cleanup()
        self.dstdir.cleanup()


class MatchPatternsTestCase(BaseTestCase):
    """Testcase that checks if all extra files are matched."""

    def test_match_pattern(self) -> None:
        """Test if extra files are matched in the media file's directory."""
        sourcedir = self.srcpath / "single"
        files = set(self.plugin.match_patterns(source=sourcedir))

        expected_files = {
            (sourcedir / "scans", "artwork"),
            (sourcedir / "file.cue", "cue"),
            (sourcedir / "file.log", "log"),
        }

        assert files == expected_files


class MoveFilesTestCase(BaseTestCase):
    """Testcase that moves files."""

    def test_move_files_single(self) -> None:
        """Test if extra files are moved for single directory imports."""
        sourcedir = self.srcpath / "single"
        destdir = self.dstpath / "single"

        # Move file
        source = sourcedir / "file.mp3"
        destination = destdir / "moved_file.mp3"
        item = beets.library.Item.from_path(source)
        shutil.move(source, destination)  # type: ignore[arg-type]
        self.plugin.on_item_moved(
            item,
            beets.util.bytestring_path(bytes(source)),
            beets.util.bytestring_path(bytes(destination)),
        )

        self.plugin.on_cli_exit(None)

        # Check source directory
        assert (sourcedir / "file.txt").exists()
        assert not (sourcedir / "file.cue").exists()
        assert not (sourcedir / "file.log").exists()
        assert not (sourcedir / "audio.log").exists()

        assert not (sourcedir / "artwork").exists()
        assert not (sourcedir / "scans").exists()

        # Check destination directory
        assert not (destdir / "file.txt").exists()
        assert (destdir / "file.cue").exists()
        assert not (destdir / "file.log").exists()
        assert (destdir / "audio.log").exists()

        assert not (destdir / "scans").is_dir()
        assert (destdir / "artwork").is_dir()
        assert set(os.listdir(destdir / "artwork")) == {"front.jpg", "back.jpg"}

    def test_move_files_multiple(self) -> None:
        """Test if extra files are moved for multi-directory imports."""
        sourcedir = self.srcpath / "multiple"
        destdir = self.dstpath / "multiple"

        # Move first file
        source = sourcedir / "CD1" / "file.mp3"
        destination = destdir / "01 - moved_file.mp3"
        item = beets.library.Item.from_path(source)
        shutil.move(source, destination)  # type: ignore[arg-type]
        self.plugin.on_item_moved(
            item,
            beets.util.bytestring_path(bytes(source)),
            beets.util.bytestring_path(bytes(destination)),
        )

        # Move second file
        source = sourcedir / "CD2" / "file.mp3"
        destination = destdir / "02 - moved_file.mp3"
        item = beets.library.Item.from_path(source)
        shutil.move(source, destination)  # type: ignore[arg-type]
        self.plugin.on_item_moved(
            item,
            beets.util.bytestring_path(bytes(source)),
            beets.util.bytestring_path(bytes(destination)),
        )

        self.plugin.on_cli_exit(None)

        # Check source directory
        assert (sourcedir / "file.txt").exists()
        assert not (sourcedir / "CD1" / "file.cue").exists()
        assert not (sourcedir / "CD2" / "file.cue").exists()
        assert not (sourcedir / "file.log").exists()
        assert not (sourcedir / "audio.log").exists()

        assert not (sourcedir / "artwork").exists()
        assert not (sourcedir / "scans").exists()

        # Check destination directory
        assert not (destdir / "file.txt").exists()
        assert not (sourcedir / "CD1_file.cue").exists()
        assert not (sourcedir / "CD2_file.cue").exists()
        assert not (destdir / "file.log").exists()
        assert (destdir / "audio.log").exists()

        assert not (destdir / "scans").is_dir()
        assert (destdir / "artwork").is_dir()
        assert set(os.listdir(destdir / "artwork")) == {"front.jpg", "back.jpg"}


class CopyFilesTestCase(BaseTestCase):
    """Testcase that copies files."""

    def test_copy_files_single(self) -> None:
        """Test if extra files are copied for single directory imports."""
        sourcedir = self.srcpath / "single"
        destdir = self.dstpath / "single"

        # Copy file
        source = sourcedir / "file.mp3"
        destination = destdir / "copied_file.mp3"
        item = beets.library.Item.from_path(source)
        shutil.copy(source, destination)
        self.plugin.on_item_copied(
            item,
            beets.util.bytestring_path(bytes(source)),
            beets.util.bytestring_path(bytes(destination)),
        )

        self.plugin.on_cli_exit(None)

        # Check source directory
        assert (sourcedir / "file.txt").exists()
        assert (sourcedir / "file.cue").exists()
        assert (sourcedir / "file.log").exists()
        assert not (sourcedir / "audio.log").exists()

        assert not (sourcedir / "artwork").exists()
        assert (sourcedir / "scans").is_dir()
        assert set(os.listdir(sourcedir / "scans")) == {"front.jpg", "back.jpg"}

        # Check destination directory
        assert not (destdir / "file.txt").exists()
        assert (destdir / "file.cue").exists()
        assert not (destdir / "file.log").exists()
        assert (destdir / "audio.log").exists()

        assert not (destdir / "scans").exists()
        assert (destdir / "artwork").is_dir()
        assert set(os.listdir(destdir / "artwork")) == {"front.jpg", "back.jpg"}

    def test_copy_files_multiple(self) -> None:
        """Test if extra files are copied for multi-directory imports."""
        sourcedir = self.srcpath / "multiple"
        destdir = self.dstpath / "multiple"

        # Copy first file
        source = sourcedir / "CD1" / "file.mp3"
        destination = destdir / "01 - copied_file.mp3"
        item = beets.library.Item.from_path(source)
        shutil.copy(source, destination)
        self.plugin.on_item_copied(
            item,
            beets.util.bytestring_path(bytes(source)),
            beets.util.bytestring_path(bytes(destination)),
        )

        # Copy second file
        source = sourcedir / "CD2" / "file.mp3"
        destination = destdir / "02 - copied_file.mp3"
        item = beets.library.Item.from_path(source)
        shutil.copy(source, destination)
        self.plugin.on_item_copied(
            item,
            beets.util.bytestring_path(bytes(source)),
            beets.util.bytestring_path(bytes(destination)),
        )

        self.plugin.on_cli_exit(None)

        # Check source directory
        assert (sourcedir / "file.txt").exists()
        assert (sourcedir / "CD1" / "file.cue").exists()
        assert (sourcedir / "CD2" / "file.cue").exists()
        assert (sourcedir / "file.log").exists()
        assert not (sourcedir / "audio.log").exists()

        assert not (sourcedir / "artwork").exists()
        assert (sourcedir / "scans").is_dir()
        assert set(os.listdir(sourcedir / "scans")) == {"front.jpg", "back.jpg"}

        # Check destination directory
        assert not (destdir / "file.txt").exists()
        assert (destdir / "CD1_file.cue").exists()
        assert (destdir / "CD2_file.cue").exists()
        assert not (destdir / "file.log").exists()
        assert (destdir / "audio.log").exists()

        assert not (destdir / "scans").exists()
        assert (destdir / "artwork").is_dir()
        assert set(os.listdir(destdir / "artwork")) == {"front.jpg", "back.jpg"}


class MultiAlbumTestCase(unittest.TestCase):
    """Testcase class that checks if multiple albums are grouped correctly."""

    PLUGIN_CONFIG = {
        "extrafiles": {
            "patterns": {
                "log": ["*.log"],
            },
        },
    }

    def setUp(self) -> None:
        """Set up example files and instanciate the plugin."""
        self.srcdir = tempfile.TemporaryDirectory(suffix="src")
        self.dstdir = tempfile.TemporaryDirectory(suffix="dst")
        self.srcpath = Path(self.srcdir.name)
        self.dstpath = Path(self.dstdir.name)

        for album in ("album1", "album2"):
            (self.dstpath / album).mkdir(parents=True)
            sourcedir = self.srcpath / album
            sourcedir.mkdir(parents=True)
            shutil.copy(
                RSRC / "full.mp3",
                sourcedir / "track01.mp3",
            )
            shutil.copy(
                RSRC / "full.mp3",
                sourcedir / "track02.mp3",
            )
            logfile = sourcedir / f"{album}.log"
            logfile.open(mode="w").close()

        # Set up plugin instance
        config = confuse.RootView(
            sources=[
                confuse.ConfigSource.of(self.PLUGIN_CONFIG),
            ]
        )

        with unittest.mock.patch(
            "beetsplug.extrafiles.beets.plugins.beets.config",
            config,
        ):
            self.plugin = beetsplug.extrafiles.ExtraFilesPlugin()

    def tearDown(self) -> None:
        """Remove the example files."""
        self.srcdir.cleanup()
        self.dstdir.cleanup()

    def test_album_grouping(self) -> None:
        """Test if albums are grouped correctly."""
        for album in ("album1", "album2"):
            sourcedir = self.srcpath / album
            destdir = self.dstpath / album

            for i in range(1, 3):
                source = sourcedir / f"track{i:02d}.mp3"
                destination = destdir / f"{i:02d} - {album} - untitled.mp3"
                item = beets.library.Item.from_path(source)
                item.album = album
                item.track = i
                item.tracktotal = 2
                shutil.copy(source, destination)
                self.plugin.on_item_copied(
                    item,
                    beets.util.bytestring_path(bytes(source)),
                    beets.util.bytestring_path(bytes(destination)),
                )

        self.plugin.on_cli_exit(None)

        for album in ("album1", "album2"):
            destdir = self.dstpath / album
            for i in range(1, 3):
                destination = destdir / f"{i:02d} - {album} - untitled.mp3"
                assert destination.exists()
            assert (destdir / f"{album}.log").exists()
