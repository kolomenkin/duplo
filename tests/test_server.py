#!/usr/bin/python3

from base64 import b64decode
from numpy import random
from os import path as os_path, environ as os_environ
from requests import get as requests_get, post as requests_post
from subprocess import Popen as subprocess_Popen
from sys import argv as sys_argv
from tempfile import TemporaryDirectory
from time import strftime as time_strftime
from unittest import TestCase
from urllib.parse import urlparse as urllib_urlparse

DEFAULT_LISTEN_HOST = '127.0.0.1'
DEFAULT_LISTEN_PORT = 35080


def log(*args):
    print('TST>', time_strftime('%Y-%m-%d %H:%M:%S:'), *args)


def get_random_bytes(size, seed):
    random.seed(seed)
    return random.bytes(size)


def get_random_text(size, seed):
    random.seed(seed)
    items = []
    for c in range(ord('A'), ord('Z')+1):
        items += chr(c)
    for c in range(ord('a'), ord('z')+1):
        items += chr(c)
    for c in range(ord('0'), ord('9')+1):
        items += chr(c)
    items.extend(['.', ',', ';', ':', '!'])
    items.extend([' ', ' ', ' ', ' '])
    items += '\n'
    return ''.join(random.choice(items, size))


# http://code.activestate.com/recipes/576655-wait-for-network-service-to-appear/
def wait_net_service(host, port, timeout=None):
    import socket
    from time import sleep, time as now
    log('Waiting for web server: ' + host + ':' + str(port))

    s = socket.socket()
    if timeout:
        end = now() + timeout

    while True:
        try:
            if timeout:
                if now() > end:
                    log('ERROR! Network sockets connect waiting timeout!')
                    return False

            s.connect((host, port))

        except socket.timeout:
            sleep(0.1)
            pass
        except socket.error:
            sleep(0.1)
            pass

        else:
            s.close()
            return True


def run_child_server(server_name, host, port):
    script_dir = os_path.dirname(os_path.abspath(__file__))
    root_dir = os_path.join(script_dir, '..')
    server_py = os_path.join(root_dir, 'server.py')

    tmpdir = TemporaryDirectory()
    log('created temporary directory: ' + tmpdir.name)

    subenv = os_environ.copy()
    subenv['LIMBO_WEB_SERVER'] = server_name
    subenv['LIMBO_LISTEN_HOST'] = host
    subenv['LIMBO_LISTEN_PORT'] = str(port)
    subenv['LIMBO_STORAGE_DIRECTORY'] = tmpdir.name

    pid = subprocess_Popen(['python', server_py], cwd=root_dir, env=subenv)
    try:
        wait_net_service(host, port, 10)
    except Exception:
        pid.terminate()
        tmpdir.cleanup()
        raise
    return [tmpdir, pid]


class ServerTestCase(TestCase):

    def __init__(self, *args, **kwargs):
        super(ServerTestCase, self).__init__(*args, **kwargs)
        # added to uploaded to server text fragment names:
        self._text_filename_postfix = '.txt'
        self._server_name = None
        self._base_url = None

    def CheckHttpError(self, r):
        if r.status_code != 200:
            raise Exception('Bad server reply code: ' + str(r.status_code))

    def GetStoredFiles(self):
        url = self._base_url + '/cgi/enumerate/'
        log('Request: GET ' + url)
        r = requests_get(url)
        self.CheckHttpError(r)
        files = r.json()
        files = sorted(files, key=lambda item: item['display_filename'])
        return files

    def DownloadFile(self, url_path):
        url = self._base_url + url_path
        log('Request: GET ' + url)
        r = requests_get(url)
        self.CheckHttpError(r)
        return r.content

    def UploadFile(self, original_filename, filedata):
        url = self._base_url + '/cgi/upload/'
        log('Request: POST ' + url)

        # files = {'file': (original_filename, filedata)}
        # r = requests_post(url, files=files)
        # NOTE: requests library has bad support for
        #       upload files with utf-8 names
        # It encodes utf-8 file name in the following form:
        # filename*=utf-8\'\'%D1%80%D1%83%D1%81%D1%81%D0%BA%D0%B8%D0%B9.%D1%84%D0%B0%D0%B9%D0%BB
        # This is why I'm constructing multipart message manually

        boundary = b'Ab522e64be24449aa3131245da23b3yZ'
        encoded_filename = original_filename.encode('utf-8')
        payload = b'--' + boundary + b'\r\nContent-Disposition: form-data' \
            + b'; name="file"; filename="' + encoded_filename \
            + b'"\r\n\r\n' + filedata + b'\r\n--' + boundary + b'--\r\n'

        content_type = 'multipart/form-data; boundary=' \
            + boundary.decode('utf-8')
        headers = {'Content-Type': content_type}

        r = requests_post(url, data=payload, headers=headers)

        self.CheckHttpError(r)

    def UploadText(self, title, text):
        url = self._base_url + '/cgi/addtext/'
        log('Request: POST ' + url)
        formdata = {'title': title, 'body': text}
        r = requests_post(url, data=formdata)
        self.CheckHttpError(r)

    def RemoveFile(self, url_filename):
        url = self._base_url + '/cgi/remove/'
        log('Request: POST ' + url)
        formdata = {'fileName': url_filename}
        r = requests_post(url, data=formdata)
        self.CheckHttpError(r)

    def RemoveAllFiles(self):
        url = self._base_url + '/cgi/remove-all/'
        log('Request: POST ' + url)
        r = requests_post(url, data='')
        self.CheckHttpError(r)

    def OnTestStart(self, test_name):
        log('=============================================')
        log('TEST: ' + self._server_name + ': ' + test_name)
        log('=============================================')

    def DoTestUploadFile(self, name, data):
        self.OnTestStart('FileUpload("' + name + '")')
        self.RemoveAllFiles()
        self.assertEqual(0, len(self.GetStoredFiles()))
        self.UploadFile(name, data)
        files = self.GetStoredFiles()
        self.assertEqual(1, len(files))
        log('File URL: ' + files[0]['url'])
        self.assertEqual(name, files[0]['display_filename'])
        self.assertEqual(len(data), files[0]['size'])
        url_filename = files[0]['url_filename']
        data2 = self.DownloadFile(files[0]['url'])
        self.assertEqual(data2, data)
        self.RemoveFile(url_filename)
        self.assertEqual(0, len(self.GetStoredFiles()))

    def DoTestUploadText(self, name, text):
        self.OnTestStart('TextUpload("' + name + '")')
        self.RemoveAllFiles()
        self.assertEqual(0, len(self.GetStoredFiles()))
        self.UploadText(name, text)
        files = self.GetStoredFiles()
        self.assertEqual(1, len(files))
        log('File URL: ' + files[0]['url'])
        self.assertEqual(name + self._text_filename_postfix,
                         files[0]['display_filename'])
        self.assertEqual(len(text), files[0]['size'])
        data2 = self.DownloadFile(files[0]['url'])
        self.assertEqual(data2, text.encode('utf-8'))
        url_filename = files[0]['url_filename']
        self.RemoveFile(url_filename)
        self.assertEqual(0, len(self.GetStoredFiles()))

    def DoTestFewFiles(self):
        self.OnTestStart('FewFiles')
        self.RemoveAllFiles()
        self.assertEqual(0, len(self.GetStoredFiles()))
        self.UploadText('data_A', 'aaa')
        self.UploadText('data_B', 'bbb')
        self.UploadFile('file1.zip', b'abcd')
        self.UploadFile('file2.txt', b'ABCD')
        files = self.GetStoredFiles()
        self.assertEqual(4, len(files))
        log('File URL: ' + files[0]['url'])
        log('File URL: ' + files[1]['url'])
        log('File URL: ' + files[2]['url'])
        log('File URL: ' + files[3]['url'])
        self.assertEqual('data_A' + self._text_filename_postfix,
                         files[0]['display_filename'])
        self.assertEqual('data_B' + self._text_filename_postfix,
                         files[1]['display_filename'])
        self.assertEqual('file1.zip', files[2]['display_filename'])
        self.assertEqual('file2.txt', files[3]['display_filename'])
        self.RemoveAllFiles()
        self.assertEqual(0, len(self.GetStoredFiles()))

    def DoAllTests(self, server_name, base_url):
        self._server_name = server_name
        self._base_url = base_url.rstrip('/')

        files = self.GetStoredFiles()
        self.assertEqual(0, len(files))

        self.DoTestUploadText('a', '')
        self.DoTestUploadText('file.txt', 'abcdef')
        text = get_random_text(90000, 42)
        self.DoTestUploadText('some_file.dat', text)

        self.DoTestUploadFile('a', b'')
        self.DoTestUploadFile('file.txt', b'abcdef')
        data = get_random_bytes(1234567, 42)
        self.DoTestUploadFile('some_file.dat', data)

        # russian is used in file name
        # filename: русский.файл
        #      hex: D1 80 D1 83 D1 81 D1 81 D0 BA D0 B8
        #           D0 B9 2E D1 84 D0 B0 D0 B9 D0 BB
        filename = b64decode('0YDRg9GB0YHQutC40Lku0YTQsNC50Ls=')
        filename = filename.decode('utf-8')
        # Paste server is known as not supporting utf-8 in file names
        if self._server_name != 'paste':
            self.DoTestUploadFile(filename, b'some text')

        self.DoTestFewFiles()

        self.RemoveAllFiles()
        self.assertEqual(0, len(self.GetStoredFiles()))

    def RunServerAndDoAllTests(self, server_name):
        global DEFAULT_LISTEN_HOST, DEFAULT_LISTEN_PORT
        host = DEFAULT_LISTEN_HOST
        port = DEFAULT_LISTEN_PORT
        base_url = 'http://' + host + ':' + str(port)
        log('RunServerAndDoAllTests("' + server_name + '") start')
        tmpdir, pid = run_child_server(server_name, host, port)

        with tmpdir:
            try:
                self.DoAllTests(server_name, base_url)
            finally:
                pid.terminate()

        log('RunServerAndDoAllTests("' + self._server_name + '") finished')

    def test_cherrypy(self): self.RunServerAndDoAllTests('cherrypy')

    # def test_flup(self): self.RunServerAndDoAllTests('flup')

    # def test_gevent(self): self.RunServerAndDoAllTests('gevent')

    # def test_gunicorn(self): self.RunServerAndDoAllTests('gunicorn')

    # Paste server is known as not supporting utf-8 in file names
    def test_paste(self): self.RunServerAndDoAllTests('paste')

    def test_tornado(self): self.RunServerAndDoAllTests('tornado')

    def test_twisted(self): self.RunServerAndDoAllTests('twisted')

    def test_waitress(self): self.RunServerAndDoAllTests('waitress')

    # def test_wsgiref(self): self.RunServerAndDoAllTests('wsgiref')


if __name__ == '__main__':
    server_name = sys_argv[1] if len(sys_argv) > 1 else 'cherrypy'
    log('Begin testing ' + server_name + '...')
    test = ServerTestCase()
    if server_name.startswith('http://') or server_name.startswith('https://'):
        server_base_url = server_name
        log('Testing external server: ' + server_base_url)
        url = urllib_urlparse(server_base_url)
        wait_net_service(url.hostname, url.port, 10)
        test.DoAllTests('external', server_base_url)
    else:
        test.RunServerAndDoAllTests(server_name)
