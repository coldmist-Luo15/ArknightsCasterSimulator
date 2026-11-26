"""从公开的 V079 数据源准备术师模拟器运行所需的本地缓存。"""
import argparse
from hashlib import file_digest, sha1, sha256
from io import BufferedReader, BytesIO, RawIOBase
import json
from pathlib import Path, PurePosixPath
import tarfile
from zipfile import ZIP_DEFLATED, ZipFile

from arkengine.opening_scene import CATALOG_SHA256, COMMIT, INDEX_SHA256, TIME_SETTINGS_SHA256, VERSION
from arkengine.public_assets import CDN, download_pinned, install_file, read_url
from arkengine.runtime_data import DATA_ROOT
from arkengine.skill_assets import SCRIPT_ASSET_SHA256
from arkengine.unity_box_contact import UNITY_SHA256


GAMEDATA_URL = f"https://codeload.github.com/Kengxxiao/ArknightsGameData/tar.gz/{COMMIT}"
ARCHIVE_SHA = "e23cb15fa6d85c30b0249efeee77ea6b9819cd9e9af9750c50981b68a2bb8797"
APK_URL = ("https://ak.hycdn.cn/GzD1CpaWgmSq1wew/79.0/package/1/1/Android/"
           "79.0.0_R4C8v7EmosKsRK1Q/arknights-hg-2791.apk")
APK_SIZE = 1934292597
SPINE_URL = "https://raw.githubusercontent.com/EsotericSoftware/spine-runtimes/3.8/spine-ts/build/spine-core.js"
SPINE_SHA = "f1e0a31b9906e4d4daf2733857d21381ddbbe75adec7f4d83e1cc9b2b070dfc1"
MANAGER = "assets/bin/Data/globalgamemanagers"
SCRIPTS = [MANAGER + f".assets.split{i}" for i in range(9)]
UNITY = "lib/arm64-v8a/libunity.so"


class RemoteApk(RawIOBase):
    """Seek the publisher's ZIP by HTTP byte range instead of downloading the entire APK."""
    def __init__(self):
        self.position = 0

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=0):
        base = (0, self.position, APK_SIZE)[whence]
        self.position = max(0, base + offset)
        return self.position

    def readinto(self, buffer):
        start = self.position
        end = min(APK_SIZE, start + len(buffer))
        if end <= start:
            return 0
        data, headers = read_url(APK_URL, {"Range": f"bytes={start}-{end-1}"})
        if headers.get("Content-Range") != f"bytes {start}-{end-1}/{APK_SIZE}" or len(data) != end-start:
            raise OSError("官方服务器未返回请求的 V079 APK 分段。可用 --apk 指定同版官包。")
        buffer[:len(data)] = data
        self.position = end
        return len(data)


def pinned_file(path, digest):
    if not path.is_file():
        return False
    with path.open("rb") as reader:
        return file_digest(reader, "sha256").hexdigest() == digest


def prepare_tables(root):
    snapshot = root / "runs/2026-11-18-cn2791-gamedata"
    index_file = snapshot / "files.jsonl"
    if pinned_file(index_file, INDEX_SHA256):
        entries = [json.loads(line) for line in index_file.read_bytes().splitlines()]
        if all(pinned_file(snapshot / row["path"], row["sha256"]) for row in entries):
            return snapshot
    archive = root / "downloads" / f"{COMMIT}.tar.gz"
    download_pinned(GAMEDATA_URL, archive, ARCHIVE_SHA)
    entries = {}
    prefix = f"ArknightsGameData-{COMMIT}/zh_CN/"
    with tarfile.open(archive, "r|gz") as tar:
        for member in tar:
            if not member.isfile() or not member.name.startswith(prefix):
                continue
            path = PurePosixPath("zh_CN") / member.name[len(prefix):]
            raw = tar.extractfile(member).read()
            key = path.as_posix()
            digest = sha256(raw).hexdigest()
            git_blob = sha1(f"blob {len(raw)}\0".encode() + raw).hexdigest()
            entries[key] = dict(path=key, bytes=len(raw), git_blob=git_blob, sha256=digest)
            if not pinned_file(snapshot / path, digest):
                install_file(snapshot / path, raw)
    index = "".join(json.dumps(entries[k], ensure_ascii=False) + "\n" for k in sorted(entries)).encode()
    if sha256(index).hexdigest() != INDEX_SHA256:
        raise ValueError("公开配置归档生成的文件索引与 V079 不符")
    install_file(index_file, index)
    return snapshot


def prepare_player(release, extracted, apk):
    manager_path = extracted / MANAGER
    output = release / "runtime-members.zip"
    full = apk or release / "client.apk"
    if not apk and pinned_file(manager_path, TIME_SETTINGS_SHA256) and (output.exists() or full.exists()):
        return
    source = full.open("rb") if full.is_file() else BufferedReader(RemoteApk(), buffer_size=1024*1024)
    with source, ZipFile(source) as package:
        manager = package.read(MANAGER)
        scripts = {name: package.read(name) for name in SCRIPTS}
        unity = package.read(UNITY)
    for data, digest in ((manager, TIME_SETTINGS_SHA256), (b"".join(scripts.values()), SCRIPT_ASSET_SHA256),
                         (unity, UNITY_SHA256)):
        if sha256(data).hexdigest() != digest:
            raise ValueError("APK 中的运行文件与国服 2.7.91 不符")
    archive = BytesIO()
    with ZipFile(archive, "w", compression=ZIP_DEFLATED) as small:
        for name, raw in scripts.items():
            small.writestr(name, raw)
        small.writestr(UNITY, unity)
    install_file(output, archive.getvalue())
    install_file(manager_path, manager)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apk", type=Path, help="可选，使用已有的国服 Android 2.7.91 官包")
    args = parser.parse_args()
    if args.apk and not args.apk.is_file():
        parser.error(f"找不到 APK：{args.apk}")
    root = DATA_ROOT
    release = root / "runs/2026-11-18-core-cn2791"
    print(f"准备 V079 数据：{root}", flush=True)
    print("1/3 获取固定版本的国服配置并生成索引", flush=True)
    snapshot = prepare_tables(root)
    print("2/3 获取官方热更新目录和 Spine 动作读取库", flush=True)
    download_pinned(CDN + "hot_update_list.json", release / "hot_update_list.json", CATALOG_SHA256)
    download_pinned(SPINE_URL, root / "runs/2026-11-17-H9-6-independent/spine-runtime/spine-core.js", SPINE_SHA)
    print("3/3 从官包读取时钟、脚本和碰撞计算所需文件", flush=True)
    prepare_player(release, root / "runs/2026-11-18-cn2791-base-index/extracted", args.apk)
    manifest = snapshot / "manifest.json"
    if not manifest.exists():
        install_file(manifest, json.dumps(dict(source="Kengxxiao/ArknightsGameData community CN data export",
                     commit=COMMIT, version_label_in_commit=VERSION, index=dict(sha256=INDEX_SHA256),
                     asset_delivery="official_single")).encode())
    print("准备完成。首次运行一条操作轴时会从官方服务器获取所需的资源包。")


if __name__ == "__main__":
    main()
