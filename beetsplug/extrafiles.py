"""beets-extrafiles plugin for beets."""

from __future__ import annotations

import itertools
import os
import shutil
import traceback
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Any

import beets.dbcore.db
import beets.library
import beets.plugins
import beets.ui
import beets.util.functemplate
import mediafile
from beets.util import FilesystemError
from beets.util import MoveOperation

if TYPE_CHECKING:
    from collections.abc import Callable
    from collections.abc import Iterable
    from collections.abc import Iterator
    from collections.abc import Mapping

    from beets.library import Album
    from beets.library import Item
    from beets.library import Library


class FormattedExtraFileMapping(beets.dbcore.db.FormattedMapping):
    """Formatted Mapping that allows path separators for certain keys."""

    def __getitem__(self, key: str) -> str:
        """Get the formatted version of model[key] as string."""
        if key == "albumpath":
            # ruff: noqa: SLF001
            value = self.model._type(key).format(self.model.get(key))
            if isinstance(value, bytes):
                value = value.decode("utf-8", "ignore")
            return value

        return super().__getitem__(key)


class ExtraFileModel(beets.dbcore.db.Model):
    """Model for a  FormattedExtraFileMapping instance."""

    _fields: dict[str, beets.dbcore.types.Type] = {  # noqa: RUF012
        "artist": beets.dbcore.types.STRING,
        "albumartist": beets.dbcore.types.STRING,
        "album": beets.dbcore.types.STRING,
        "albumpath": beets.dbcore.types.STRING,
        "filename": beets.dbcore.types.STRING,
    }

    @classmethod
    def _getters(cls) -> Mapping[str, Callable[[ExtraFileModel], Any]]:
        """Return a mapping from field names to getter functions."""
        return {}


class ExtraFilesPlugin(beets.plugins.BeetsPlugin):
    """Plugin main class."""

    def __init__(self) -> None:
        """Initialize a new plugin instance."""
        super().__init__()
        self.config.add(
            {
                "patterns": {},
                "paths": {},
            }
        )

        self._moved_items: set[tuple[Item, Path, Path]] = set()
        self._copied_items: set[tuple[Item, Path, Path]] = set()
        self._linked_items: set[tuple[Item, Path, Path]] = set()
        self._hardlinked_items: set[tuple[Item, Path, Path]] = set()
        self._reflinked_items: set[tuple[Item, Path, Path]] = set()
        self._scanned_paths: set[Path] = set()
        self.path_formats = beets.ui.get_path_formats(self.config["paths"])

        self.register_listener("album_imported", self.on_album_imported)
        self.register_listener("item_moved", self.on_item_moved)
        self.register_listener("item_copied", self.on_item_copied)
        self.register_listener("item_linked", self.on_item_linked)
        self.register_listener("item_hardlinked", self.on_item_hardlinked)
        self.register_listener("item_reflinked", self.on_item_reflinked)
        self.register_listener("cli_exit", self.on_cli_exit)

    def on_album_imported(self, lib: Library, album: Album) -> None:
        """Run this listener function on album_imported events."""
        self._log.info("[album_imported] lib: {0} album: {1}", lib, album)

    def on_item_moved(self, item: Item, source: bytes, destination: bytes) -> None:
        """Run this listener function on item_moved events."""
        src_path = Path(os.fsdecode(source))
        dest_path = Path(os.fsdecode(destination))
        self._moved_items.add((item, src_path, dest_path))

    def on_item_copied(self, item: Item, source: bytes, destination: bytes) -> None:
        """Run this listener function on item_copied events."""
        src_path = Path(os.fsdecode(source))
        dest_path = Path(os.fsdecode(destination))
        self._copied_items.add((item, src_path, dest_path))

    def on_item_linked(self, item: Item, source: bytes, destination: bytes) -> None:
        """Run this listener function on item_linked events."""
        src_path = Path(os.fsdecode(source))
        dest_path = Path(os.fsdecode(destination))
        self._linked_items.add((item, src_path, dest_path))

    def on_item_hardlinked(self, item: Item, source: bytes, destination: bytes) -> None:
        """Run this listener function on item_hardlinked events."""
        src_path = Path(os.fsdecode(source))
        dest_path = Path(os.fsdecode(destination))
        self._hardlinked_items.add((item, src_path, dest_path))

    def on_item_reflinked(self, item: Item, source: bytes, destination: bytes) -> None:
        """Run this listener function on item_reflinked events."""
        src_path = Path(os.fsdecode(source))
        dest_path = Path(os.fsdecode(destination))
        self._reflinked_items.add((item, src_path, dest_path))

    def on_cli_exit(self, lib: Library | None) -> None:
        """Run this listener function when the CLI exits."""
        del lib
        files = self.gather_files(self._copied_items)
        self.process_items(files, action=self._copy_file)

        files = self.gather_files(self._linked_items)
        self.process_items(files, action=self._link_file)

        files = self.gather_files(self._hardlinked_items)
        self.process_items(files, action=self._hardlink_file)

        files = self.gather_files(self._reflinked_items)
        self.process_items(files, action=self._reflink_file)

        files = self.gather_files(self._moved_items)
        self.process_items(files, action=self._move_file)

    def _handle_file(
        self,
        path: Path,
        dest: Path,
        operation: MoveOperation = MoveOperation.MOVE,
    ) -> None:
        """copy, link or hardlink path to dest."""
        self._log.info("[{0}] {1} -> {2}", operation.name, path, dest)

        if dest.exists():
            if path.samefile(dest):
                self._log.info("Source {0} same as destination {1}", path, dest)
                return
            raise FilesystemError("destination already exists", operation.name, (path, dest))

        if operation == MoveOperation.MOVE:
            copy_function = beets.util.move
        elif operation == MoveOperation.COPY:
            copy_function = beets.util.copy
        elif operation == MoveOperation.LINK:
            copy_function = beets.util.link
        elif operation == MoveOperation.HARDLINK:
            copy_function = beets.util.hardlink
        elif operation == MoveOperation.REFLINK:
            copy_function = beets.util.reflink
        else:
            raise NotImplementedError(f"unknown MoveOperation: {operation}")

        if path.is_dir():
            try:
                if operation == MoveOperation.MOVE:
                    shutil.move(path, dest, copy_function=copy_function)  # type: ignore[arg-type]
                else:
                    shutil.copytree(path, dest, copy_function=copy_function)  # type: ignore[arg-type]

            except OSError as exc:
                raise FilesystemError(exc, operation.name, (path, dest), traceback.format_exc()) from exc
        else:
            _ = copy_function(bytes(path), bytes(dest))

    def _copy_file(self, path: Path, dest: Path) -> None:
        self._handle_file(path, dest, operation=MoveOperation.COPY)

    def _link_file(self, path: Path, dest: Path) -> None:
        """Symlink path to dest."""
        self._handle_file(path, dest, operation=MoveOperation.LINK)

    def _hardlink_file(self, path: Path, dest: Path) -> None:
        """Hardlink path to dest."""
        self._handle_file(path, dest, operation=MoveOperation.HARDLINK)

    def _reflink_file(self, path: Path, dest: Path) -> None:
        """Reflink path to dest."""
        self._handle_file(path, dest, operation=MoveOperation.REFLINK)

    def _move_file(self, path: Path, dest: Path) -> None:
        """Move path to dest."""
        self._handle_file(path, dest, operation=MoveOperation.MOVE)

    def process_items(
        self,
        files: Iterable[tuple[Path, Path]],
        action: Callable[[Path, Path], None],
    ) -> None:
        """Move path to dest."""
        for source, destination in files:
            if not source.exists():
                self._log.warning("Skipping missing source file: {0}", source)
                continue

            if destination.exists():
                self._log.warning(
                    "Skipping already present destination file: {0}",
                    destination,
                )
                continue

            dest_path = Path(os.fsdecode(beets.util.unique_path(bytes(destination))))
            # TODO: remove?
            beets.util.mkdirall(bytes(dest_path))

            try:
                action(source, dest_path)
            except FilesystemError:
                self._log.warning(
                    "Failed to process file: {} -> {}",
                    source,
                    dest_path,
                )

    def gather_files(self, itemops: Iterable[tuple[Item, Path, Path]]) -> Iterator[tuple[Path, Path]]:
        """Generate a sequence of (path, destpath) tuples."""

        def group(itemop: tuple[Item, Path, Path]) -> tuple[str, str]:
            item = itemop[0]
            return (item.albumartist or item.artist), item.album

        sorted_itemops = sorted(itemops, key=group)
        for _, itemopgroup in itertools.groupby(sorted_itemops, key=group):
            items: tuple[Item, ...]
            sources: tuple[Path, ...]
            destinations: tuple[Path, ...]
            items, sources, destinations = zip(*itemopgroup)
            item = items[0]

            sourcedirs: set[Path] = {f.parent for f in sources}
            destdirs: set[Path] = {f.parent for f in destinations}

            source = Path(os.path.commonpath([*sourcedirs]))
            destination = Path(os.path.commonpath([*destdirs]))
            self._log.debug(
                "{0} -> {1} ({2.album} by {2.albumartist}, {3} tracks)",
                source,
                destination,
                item,
                len(items),
            )

            meta = {
                "artist": item.artist or "None",
                "albumartist": item.albumartist or "None",
                "album": item.album or "None",
                "albumpath": str(destination),
            }

            for path, category in self.match_patterns(
                source,
                skip=self._scanned_paths,
            ):
                relpath = os.path.normpath(os.path.relpath(path, start=source))
                destpath = self.get_destination(relpath, category, meta.copy())
                self._log.info("{0} -> {1}", path, destpath)
                yield path, destpath

    def match_patterns(self, source: Path, skip: set[Path] | None = None) -> Iterator[tuple[Path, str]]:
        """Find all files matched by the patterns."""
        if skip is None:
            skip = set()
        elif source in skip:
            return

        for category, patterns in self.config["patterns"].get(dict).items():  # type: ignore[generic]
            for pattern in patterns:
                # handle special case where patterns dictionary is a simple string instead of a list
                if isinstance(patterns, str):
                    patterns = [ patterns ]
                for path in source.glob(pattern):
                    # Skip special dot directories (just in case)
                    if str(path) in (".", ".."):
                        continue

                    # Skip files handled by the beets media importer
                    ext = path.suffix
                    if len(ext) > 1 and ext[1:] in mediafile.TYPES:
                        self._log.info(
                            "beets importer handles type ({0}), skipping: {1}",
                            ext,
                            path,
                        )
                        continue

                    yield path, category

        skip.add(source)

    def get_destination(self, path: str, category: str, meta: Mapping) -> Path:
        """Get the destination path for a source file's relative path."""
        # Sanitize filename
        dest_path = Path(beets.util.sanitize_path(os.fsdecode(path)))

        mapping = FormattedExtraFileMapping(
            ExtraFileModel(
                basename=dest_path.name,
                filename=dest_path.parent / dest_path.stem,
                **meta,
            ),
            for_path=True,
        )

        path_format: beets.util.functemplate.Template
        path_format = next(
            (path_format for query, path_format in self.path_formats if query == category),
            # No query matched; use original filename
            beets.util.functemplate.Template("$albumpath/$filename"),
        )

        # Get template funcs and evaluate against mapping
        funcs = beets.library.models.DefaultTemplateFunctions().functions()
        return Path(path_format.substitute(mapping, funcs) + dest_path.suffix)
