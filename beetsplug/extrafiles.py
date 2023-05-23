# -*- coding: utf-8 -*-
"""beets-extrafiles plugin for beets."""
import glob
import itertools
import os
import shutil
import sys
import traceback
from pathlib import Path

import mediafile
import beets.dbcore.db
import beets.library
import beets.plugins
import beets.ui
import beets.util.functemplate


class FormattedExtraFileMapping(beets.dbcore.db.FormattedMapping):
    """Formatted Mapping that allows path separators for certain keys."""

    def __getitem__(self, key):
        """Get the formatted version of model[key] as string."""
        if key == 'albumpath':
            value = self.model._type(key).format(self.model.get(key))
            if isinstance(value, bytes):
                value = value.decode('utf-8', 'ignore')
            return value
        else:
            return super(FormattedExtraFileMapping, self).__getitem__(key)


class ExtraFileModel(beets.dbcore.db.Model):
    """Model for a  FormattedExtraFileMapping instance."""

    _fields = {
        'artist':      beets.dbcore.types.STRING,
        'albumartist': beets.dbcore.types.STRING,
        'album':       beets.dbcore.types.STRING,
        'albumpath':   beets.dbcore.types.STRING,
        'filename':    beets.dbcore.types.STRING,
    }

    @classmethod
    def _getters(cls):
        """Return a mapping from field names to getter functions."""
        return {}


class ExtraFilesPlugin(beets.plugins.BeetsPlugin):
    """Plugin main class."""

    def __init__(self, *args, **kwargs):
        """Initialize a new plugin instance."""
        super(ExtraFilesPlugin, self).__init__(*args, **kwargs)
        self.config.add({
            'patterns': {},
            'paths': {},
        })

        self._moved_items = set()
        self._copied_items = set()
        self._linked_items = set()
        self._hardlinked_items = set()
        self._reflinked_items = set()
        self._scanned_paths = set()
        self.path_formats = beets.ui.get_path_formats(self.config['paths'])

        self.register_listener('album_imported', self.on_album_imported)
        self.register_listener('item_moved', self.on_item_moved)
        self.register_listener('item_copied', self.on_item_copied)
        self.register_listener('item_linked', self.on_item_linked)
        self.register_listener('item_hardlinked', self.on_item_hardlinked)
        self.register_listener('item_reflinked', self.on_item_reflinked)
        self.register_listener('cli_exit', self.on_cli_exit)

    def on_album_imported(self, lib, album):
        """Run this listener function on album_imported events."""
        self._log.info("[album_imported] lib: {0} album: {1}", lib, album)

    def on_item_moved(self, item, source, destination):
        """Run this listener function on item_moved events."""
        self._moved_items.add((item, source, destination))

    def on_item_copied(self, item, source, destination):
        """Run this listener function on item_copied events."""
        self._copied_items.add((item, source, destination))

    def on_item_linked(self, item, source, destination):
        """Run this listener function on item_linked events."""
        self._linked_items.add((item, source, destination))

    def on_item_hardlinked(self, item, source, destination):
        """Run this listener function on item_hardlinked events."""
        self._hardlinked_items.add((item, source, destination))

    def on_item_reflinked(self, item, source, destination):
        """Run this listener function on item_reflinked events."""
        self._reflinked_items.add((item, source, destination))

    def on_cli_exit(self, lib):
        """Run this listener function when the CLI exits."""
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

    def _handle_file(self, path, dest, operation=beets.util.MoveOperation.MOVE):
        """copy, link or hardlink path to dest."""
        self._log.info("[{0}] {1} -> {2}", operation.name, path, dest)
        if os.path.samefile(path, dest):
            self._log.info('Source {0} same as destination {1}, skipping', path, dest)
            return
        if os.path.exists(dest):
            raise beets.util.FilesystemError('destination already exists', operation.name, (path, dest))

        if operation == beets.util.MoveOperation.MOVE:
            # beets.util.move doesn't support moving directories
            def copy_function(src, dst):
                try:
                    return os.replace(src, dst)
                except (OSError, IOError) as exc_info:
                    raise beets.util.FilesystemError(exc_info, operation.name, (path, dest), traceback.format_exc())
        elif operation == beets.util.MoveOperation.COPY:
            copy_function = beets.util.copy
        elif operation == beets.util.MoveOperation.LINK:
            copy_function = beets.util.link
        elif operation == beets.util.MoveOperation.HARDLINK:
            copy_function = beets.util.hardlink
        elif operation == beets.util.MoveOperation.REFLINK:
            copy_function = beets.util.reflink
        else:
            assert False, 'unknown MoveOperation'

        if os.path.isdir(path):
            try:
                shutil.copytree(path, dest, copy_function=copy_function)
            except (OSError, IOError) as exc:
                raise beets.util.FilesystemError(
                    exc, operation.name, (path, dest),
                    traceback.format_exc(),
                )
        else:
            copy_function(path, dest)

    def _copy_file(self, path, dest):
        self._handle_file(path, dest, operation=beets.util.MoveOperation.COPY)

    def _link_file(self, path, dest):
        """Symlink path to dest."""
        self._handle_file(path, dest, operation=beets.util.MoveOperation.LINK)

    def _hardlink_file(self, path, dest):
        """Hardlink path to dest."""
        self._handle_file(path, dest, operation=beets.util.MoveOperation.HARDLINK)

    def _reflink_file(self, path, dest):
        """Reflink path to dest."""
        self._handle_file(path, dest, operation=beets.util.MoveOperation.REFLINK)

    def _move_file(self, path, dest):
        """Move path to dest."""
        self._handle_file(path, dest, operation=beets.util.MoveOperation.MOVE)

    def process_items(self, files, action):
        """Move path to dest."""
        for source, destination in files:
            if not os.path.exists(source):
                self._log.warning('Skipping missing source file: {0}', source)
                continue

            if os.path.exists(destination):
                self._log.warning(
                    'Skipping already present destination file: {0}',
                    destination,
                )
                continue

            sourcepath = beets.util.bytestring_path(source)
            destpath = beets.util.bytestring_path(destination)
            destpath = beets.util.unique_path(destpath)
            beets.util.mkdirall(destpath)

            try:
                action(sourcepath, destpath)
            except beets.util.FilesystemError:
                self._log.warning(
                    'Failed to process file: {} -> {}', source, destpath,
                )

    def gather_files(self, itemops):
        """Generate a sequence of (path, destpath) tuples."""
        def group(itemop):
            item = itemop[0]
            return (item.albumartist or item.artist, item.album)

        sorted_itemops = sorted(itemops, key=group)
        for _, itemopgroup in itertools.groupby(sorted_itemops, key=group):
            items, sources, destinations = zip(*itemopgroup)
            item = items[0]

            sourcedirs = set(os.path.dirname(f) for f in sources)
            destdirs = set(os.path.dirname(f) for f in destinations)

            source = os.path.commonpath(sourcedirs)
            destination = os.path.commonpath(destdirs)
            self._log.debug(
                '{0} -> {1} ({2.album} by {2.albumartist}, {3} tracks)',
                source, destination, item, len(items),
            )

            meta = {
                'artist': item.artist or u'None',
                'albumartist': item.albumartist or u'None',
                'album': item.album or u'None',
                'albumpath': beets.util.displayable_path(destination),
            }

            for path, category in self.match_patterns(
                    source, skip=self._scanned_paths,
            ):
                path = beets.util.bytestring_path(path)
                relpath = os.path.normpath(os.path.relpath(path, start=source))
                destpath = self.get_destination(relpath, category, meta.copy())
                self._log.info("{0} -> {1}", path, destpath)
                yield path, destpath

    def match_patterns(self, source, skip=set()):
        """Find all files matched by the patterns."""
        source_path = Path(os.fsdecode(source))

        if source_path in skip:
            return

        for category, patterns in self.config['patterns'].get(dict).items():
            for pattern in patterns:
                for path in source_path.glob(pattern):
                    # Skip special dot directories (just in case)
                    if str(path) in ('.', '..'):
                        continue

                    # Skip files handled by the beets media importer
                    ext = path.suffix
                    if len(ext) > 1 and ext[1:] in mediafile.TYPES.keys():
                        self._log.info("file type handled by beets media importer: {0} skipping file: {1}", path.suffix, path)
                        continue

                    yield bytes(path), category

        skip.add(source_path)

    def get_destination(self, path, category, meta):
        """Get the destination path for a source file's relative path."""
        # Sanitize filename
        dest_path = Path(beets.util.sanitize_path(os.fsdecode(path)))

        mapping = FormattedExtraFileMapping(
            ExtraFileModel(
                basename=dest_path.name,
                filename=dest_path.parent / dest_path.stem,
                **meta
            ), for_path=True,
        )

        for query, path_format in self.path_formats:
            if query == category:
                break
        else:
            # No query matched; use original filename
            path_format = beets.util.functemplate.Template(
                '$albumpath/$filename',
            )

        # Get template funcs and evaluate against mapping
        funcs = beets.library.DefaultTemplateFunctions().functions()
        return path_format.substitute(mapping, funcs) + dest_path.suffix
