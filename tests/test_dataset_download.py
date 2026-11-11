"""Local HTTP-response controls for resume/checksum/storage, no dataset downloads."""

import hashlib
import importlib.util
import io
from pathlib import Path
from types import SimpleNamespace

import pytest

SPEC = importlib.util.spec_from_file_location(
    "rbo_download", Path(__file__).parents[1] / "scripts/download_rbo_dataset.py"
)
DOWNLOADER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOWNLOADER)


class Response(io.BytesIO):
    def __init__(self, value, status, headers=None):
        super().__init__(value)
        self.status = status
        self.headers = headers or {}


def row(data):
    return {
        "key": "example.tar.gz",
        "size": len(data),
        "checksum": "md5:" + hashlib.md5(data).hexdigest(),
        "links": {"self": "https://zenodo.org/api/records/1036660/files/example.tar.gz/content"},
    }


@pytest.mark.parametrize("range_supported", [True, False])
def test_resume_and_ignored_range_do_not_corrupt_archive(tmp_path, monkeypatch, range_supported):
    data = b"controlled archive bytes"
    (tmp_path / "example.tar.gz.part").write_bytes(data[:5])

    def open_url(request, timeout):
        assert request.headers["Range"] == "bytes=5-"
        assert "/records/1036660/files/" in request.full_url
        return Response(
            data[5:] if range_supported else data,
            206 if range_supported else 200,
            {"Content-Range": f"bytes 5-{len(data) - 1}/{len(data)}"},
        )

    monkeypatch.setattr(DOWNLOADER.urllib.request, "urlopen", open_url)
    assert DOWNLOADER.download_file(row(data), tmp_path, reserve_bytes=0)["status"] == "verified"
    assert (tmp_path / "example.tar.gz").read_bytes() == data
    assert not (tmp_path / "example.tar.gz.part").exists()


def test_wrong_checksum_and_wrong_resume_range_are_not_published(tmp_path, monkeypatch):
    data = b"abcdefg"
    monkeypatch.setattr(
        DOWNLOADER.urllib.request, "urlopen", lambda *a, **k: Response(b"bad-data", 200)
    )
    with pytest.raises(ValueError, match="checksum|size"):
        DOWNLOADER.download_file(row(data), tmp_path, reserve_bytes=0)
    assert not (tmp_path / "example.tar.gz").exists()
    (tmp_path / "example.tar.gz.part").write_bytes(data[:2])
    monkeypatch.setattr(
        DOWNLOADER.urllib.request,
        "urlopen",
        lambda *a, **k: Response(data[2:], 206, {"Content-Range": "bytes 0-4/7"}),
    )
    with pytest.raises(ValueError, match="resume range"):
        DOWNLOADER.download_file(row(data), tmp_path, reserve_bytes=0)


def test_disk_reserve_stops_transfer_and_preserves_partial(tmp_path, monkeypatch):
    data = b"small fixture"
    monkeypatch.setattr(DOWNLOADER.urllib.request, "urlopen", lambda *a, **k: Response(data, 200))
    monkeypatch.setattr(DOWNLOADER.shutil, "disk_usage", lambda p: SimpleNamespace(free=10))
    with pytest.raises(OSError, match="reserve"):
        DOWNLOADER.download_file(row(data), tmp_path, reserve_bytes=10)
    assert not (tmp_path / "example.tar.gz").exists()
    assert (tmp_path / "example.tar.gz.part").exists()


def test_verified_file_is_reused_without_network(tmp_path, monkeypatch):
    data = b"verified archive"
    (tmp_path / "example.tar.gz").write_bytes(data)
    monkeypatch.setattr(
        DOWNLOADER.urllib.request, "urlopen", lambda *a, **k: pytest.fail("network not needed")
    )
    assert DOWNLOADER.download_file(row(data), tmp_path, reserve_bytes=0)["status"] == "verified"
    (tmp_path / "example.tar.gz").write_bytes(b"corruption")
    with pytest.raises(ValueError, match="existing file fails"):
        DOWNLOADER.download_file(row(data), tmp_path, reserve_bytes=0)


def test_premature_eof_is_resumable_and_never_published(tmp_path, monkeypatch):
    import urllib.error

    data = b"abcdefghi"
    monkeypatch.setattr(
        DOWNLOADER.urllib.request, "urlopen", lambda *a, **k: Response(data[:4], 200)
    )
    with pytest.raises(urllib.error.URLError, match="ended early"):
        DOWNLOADER.download_file(row(data), tmp_path, reserve_bytes=0)
    assert (tmp_path / "example.tar.gz.part").read_bytes() == data[:4]
    assert not (tmp_path / "example.tar.gz").exists()
    monkeypatch.setattr(
        DOWNLOADER.urllib.request,
        "urlopen",
        lambda *a, **k: Response(data[4:], 206, {"Content-Range": "bytes 4-8/9"}),
    )
    assert DOWNLOADER.download_file(row(data), tmp_path, reserve_bytes=0)["status"] == "verified"
