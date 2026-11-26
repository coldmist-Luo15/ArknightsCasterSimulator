"""Fetch immutable V079 assets from their publishers and cache complete files locally."""
from hashlib import sha256
from os import replace
from pathlib import Path
from tempfile import NamedTemporaryFile
from time import sleep
from urllib.error import URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


RELEASE = "26-11-13-06-37-23-89286612"
CDN = f"https://ak.hycdn.cn/assetbundle/official/Android/assets/{RELEASE}/"


def read_url(url, headers=None):
    request = Request(url, headers={"User-Agent": "ArknightsCasterSimulator/0.1",
                                    "Accept-Encoding": "identity", **(headers or {})})
    for attempt in range(3):
        try:
            with urlopen(request, timeout=90) as response:
                if headers and "Range" in headers and response.status != 206:
                    raise OSError("服务器不支持按字节读取；可使用 --apk 指定同版官包")
                return response.read(), response.headers
        except (URLError, TimeoutError, ConnectionError):
            if attempt == 2:
                raise
            sleep(attempt + 1)


def install_file(path, content):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    writer = NamedTemporaryFile(dir=path.parent, delete=False)
    temp = Path(writer.name)
    try:
        with writer:
            writer.write(content)
        replace(temp, path)
    finally:
        temp.unlink(missing_ok=True)


def download_pinned(url, destination, digest):
    if destination.is_file() and sha256(destination.read_bytes()).hexdigest() == digest:
        return
    content, _ = read_url(url)
    if sha256(content).hexdigest() != digest:
        raise ValueError(f"下载的文件与 V079 摘要不符：{url}")
    install_file(destination, content)


def fetch_bundle(member, entry, destination):
    from hashlib import md5
    from io import BytesIO
    from zipfile import ZipFile
    data, _ = read_url(CDN + quote(destination.name))
    with ZipFile(BytesIO(data)) as archive:
        raw = archive.read(member)
    if len(raw) != entry["abSize"] or md5(raw).hexdigest() != entry["md5"]:
        raise ValueError(f"官方单包与版本目录不符：{member}")
    install_file(destination, data)
