# Limbo file sharing (https://github.com/kolomenkin/limbo)
# Copyright 2018 Sergey Kolomenkin
# Licensed under MIT (https://github.com/kolomenkin/limbo/blob/master/LICENSE)

from lib_common import log, get_file_modified_unixtime

from io import open as io_open
from logging import error as logging_error
from os import listdir as os_listdir, \
               makedirs as os_makedirs, \
               path as os_path, \
               remove as os_remove, \
               rename as os_rename
import re
import threading
from time import time as time_time, sleep as time_sleep
from traceback import format_exc as traceback_format_exc
from uuid import uuid4


# ==========================================
# There are 4 types of file names:
# 1) Original file name provided by user
# 2) URL file name for use in URLs
# 3) Disk file name (on server)
# 4) Display name. Used on web page and to save file on end user's computer
# ==========================================


def clean_filename(filename):
    s = filename
    # https://en.wikipedia.org/wiki/Filename#Reserved_characters_and_words
    # limit max length:
    s = s[:250]
    # dots and space at end of file name are ignored:
    s = s.rstrip('. ')
    # replace forbidden symbols:
    s = re.sub('[\\\\:\'\\[\\]/",<>&^$+*?;|\x00-\x1F]', '_', s)
    # Deny special file names:
    # This is important not to make string longer here!
    s = re.sub('^(CON|PRN|AUX|NUL|COM\\d|LPT\\d)($|\..*)', 'DEV\\2',
               s, flags=re.IGNORECASE)
    s = 'EMPTY' if s == '' else s
    return s


class AtomicFile:
    def __init__(self, temp_filename, final_filename):
        if os_path.isfile(final_filename):
            raise Exception('Destination file already exists')
        self._temp_filename = temp_filename
        self._final_filename = final_filename
        self._fd = io_open(self._temp_filename, 'wb')

    def write(self, data):
        self._fd.write(data)

    def close(self):
        self._fd.close()
        os_rename(self._temp_filename, self._final_filename)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self._fd.close()
        if exc_tb is None:
            # No exception, so rename
            os_rename(self._temp_filename, self._final_filename)


class FileStorage:
    def __init__(self, storage_directory, max_store_time_seconds):
        log('FileStorage: create(' + storage_directory + ', max ' +
            str(max_store_time_seconds) + ' sec)')
        self._storage_directory = os_path.abspath(storage_directory)
        self._temp_directory = \
            os_path.join(self._storage_directory, 'incomplete')
        self._max_store_time_seconds = max_store_time_seconds
        self._retension_thread = None
        self._protect_stop = threading.Lock()
        self._condition_stop = threading.Condition(self._protect_stop)
        self._stopping = False

        self._create_dirs()

    def start(self):
        log('FileStorage: start')
        self._retension_thread = \
            threading.Thread(target=self._retension_thread_procedure)
        self._retension_thread.start()

    def stop(self):
        log('FileStorage: stop')
        with self._condition_stop:
            self._stopping = True
            self._condition_stop.notify()
        self._retension_thread.join()

    def enumerate_files(self):
        if not os_path.isdir(self._storage_directory):
            return []
        files = []
        for disk_filename in os_listdir(self._storage_directory):
            fullname = os_path.join(self._storage_directory, disk_filename)
            if os_path.isfile(fullname):
                url_filename = FileStorage._fname_disk_to_url(disk_filename)
                display_filename = \
                    FileStorage._fname_disk_to_display(disk_filename)
                files.append(
                    {
                        'full_disk_filename': fullname,
                        'url_filename': url_filename,
                        'display_filename': display_filename,
                    })
        return files

    def open_file_writer(self, original_filename):
        self._create_dirs()
        disk_filename = FileStorage._fname_original_to_disk(original_filename)
        temp_disk_filename = uuid4().hex + '.' + disk_filename
        temp_fullname = os_path.join(self._temp_directory, temp_disk_filename)
        fullname = os_path.join(self._storage_directory, disk_filename)
        log('FileStorage: Upload file: ' + disk_filename)
        return AtomicFile(temp_fullname, fullname)

    def get_file_info_to_read(self, url_filename):
        disk_filename = FileStorage._fname_url_to_disk(url_filename)
        display_filename = FileStorage._fname_disk_to_display(disk_filename)
        return [self._storage_directory, disk_filename, display_filename]

    def remove_file(self, url_filename):
        disk_filename = FileStorage._fname_url_to_disk(url_filename)
        fullname = os_path.join(self._storage_directory, disk_filename)
        log('FileStorage: Remove file: "' + disk_filename +
            '"; size: ' + str(os_path.getsize(fullname)))
        os_remove(fullname)

    def remove_all_files(self):
        if not os_path.isdir(self._storage_directory):
            return
        for disk_filename in os_listdir(self._storage_directory):
            fullname = os_path.join(self._storage_directory, disk_filename)
            if os_path.isfile(fullname):
                log('FileStorage: Remove file: "' + disk_filename +
                    '"; size: ' + str(os_path.getsize(fullname)))
                os_remove(fullname)
        if not os_path.isdir(self._temp_directory):
            return
        for disk_filename in os_listdir(self._temp_directory):
            fullname = os_path.join(self._temp_directory, disk_filename)
            if os_path.isfile(fullname):
                log('FileStorage: Remove temp file: "' + disk_filename +
                    '"; size: ' + str(os_path.getsize(fullname)))
                os_remove(fullname)

    def _fname_original_to_disk(original_filename):
        return FileStorage._canonize_file(original_filename)

    def _fname_url_to_disk(url_filename):
        return FileStorage._canonize_file(url_filename)

    def _fname_disk_to_url(disk_filename):
        return disk_filename

    def _fname_disk_to_display(disk_filename):
        return disk_filename

    def _canonize_file(filename):
        canonizeed = clean_filename(filename)
        if clean_filename(canonizeed) != canonizeed:
            raise Exception('clean_filename failed to canonize file name',
                            filename)
        return canonizeed

    def _create_dirs(self):
        if not os_path.isdir(self._storage_directory):
            os_makedirs(self._storage_directory, 0o755)

        if not os_path.isdir(self._temp_directory):
            os_makedirs(self._temp_directory, 0o755)

    def _retension_thread_procedure(self):
        log('FileStorage: Retension thread started')
        previous_check_time = 0
        while True:
            try:
                now = time_time()
                if now - previous_check_time > 10 * 60:  # every 10 minutes
                    log('FileStorage: Check for outdated files')
                    previous_check_time = time_time()
                    self._check_retention()

                # Wait for 60 seconds with a possibility
                # to be interrupted through stop() call:
                with self._condition_stop:
                    if not self._stopping:
                        self._condition_stop.wait(60)
                    if self._stopping:
                        log('Retension thread found stop signal')
                        break
            except Exception:
                logging_error(traceback_format_exc())
                time_sleep(60)  # prevent from flooding

    def _check_retention(self):
        now = time_time()
        if not os_path.isdir(self._storage_directory):
            return
        for file in os_listdir(self._storage_directory):
            fullname = os_path.join(self._storage_directory, file)
            if os_path.isfile(fullname):
                modified_unixtime = get_file_modified_unixtime(fullname)
                if now - modified_unixtime > self._max_store_time_seconds:
                    log('FileStorage: Remove outdated file: ' + fullname +
                        '"; size: ' + str(os_path.getsize(fullname)))
                    os_remove(fullname)

        if not os_path.isdir(self._temp_directory):
            return
        for file in os_listdir(self._temp_directory):
            fullname = os_path.join(self._temp_directory, file)
            if os_path.isfile(fullname):
                modified_unixtime = get_file_modified_unixtime(fullname)
                if now - modified_unixtime > 15 * 60:  # every 15 minutes
                    log('FileStorage: Remove outdated temp file: ' + fullname +
                        '"; size: ' + str(os_path.getsize(fullname)))
                    os_remove(fullname)
