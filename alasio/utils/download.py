import requests
from requests.adapters import HTTPAdapter

from alasio.utils.atomic import atomic_stream_write, atomic_write


class Downloader:
    def new_session(self):
        session = requests.Session()
        session.trust_env = False
        session.mount('http://', HTTPAdapter(max_retries=3))
        session.mount('https://', HTTPAdapter(max_retries=3))

        # proxies = {'http': 'http://127.0.0.1:7890', 'https': 'http://127.0.0.1:7890'}
        # session.proxies = proxies
        # session.headers['User-Agent'] = self.user_agent
        return session

    def atomic_download(self, url: str, file: str):
        """
        Download a file from url then write into file
        """
        session = self.new_session()
        resp = session.get(url)
        resp.raise_for_status()

        content = resp.content
        atomic_write(file, content)

    def atomic_download_stream(self, url: str, file: str, chunk_size=8196):
        """
        Download a file from url then write file in stream.
        Usually to be used to download a large file
        """
        session = self.new_session()
        resp = session.get(url, stream=True)

        content = resp.iter_content(chunk_size=chunk_size)
        atomic_stream_write(file, content)
