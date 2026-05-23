import io
import os
import tarfile
import pytest
from zimport_tools import archive


def _make_tgz(path, files, fmt=tarfile.PAX_FORMAT):
    """files: dict of arcname -> bytes content."""
    with tarfile.open(path, "w:gz", format=fmt) as tar:
        for arcname, content in files.items():
            info = tarfile.TarInfo(name=arcname)
            info.size = len(content)
            tar.addfile(info, io.BytesIO(content))


def test_unpack_pax_archive(tmp_path):
    tgz = tmp_path / "a.tgz"
    longname = "Re_ " + "入出库通知" * 6 + ".eml"  # >100 bytes, non-ASCII
    _make_tgz(str(tgz), {longname: b"From: a@b\r\n\r\nhi"})
    dest = tmp_path / "out"
    archive.unpack_tgz(str(tgz), str(dest))
    assert (dest / longname).read_bytes() == b"From: a@b\r\n\r\nhi"


def test_unpack_rejects_path_traversal(tmp_path):
    tgz = tmp_path / "evil.tgz"
    _make_tgz(str(tgz), {"../escape.eml": b"x"})
    with pytest.raises(ValueError):
        archive.unpack_tgz(str(tgz), str(tmp_path / "out2"))
    assert not (tmp_path / "escape.eml").exists()


def test_unpack_rejects_symlink_entry(tmp_path):
    tgz = tmp_path / "sym.tgz"
    with tarfile.open(str(tgz), "w:gz") as tar:
        info = tarfile.TarInfo(name="link")
        info.type = tarfile.SYMTYPE
        info.linkname = "../outside"
        tar.addfile(info)
    with pytest.raises(ValueError):
        archive.unpack_tgz(str(tgz), str(tmp_path / "out3"))


def test_detect_eml_bundle(tmp_path):
    d = tmp_path / "b1"
    d.mkdir()
    (d / "1.eml").write_bytes(b"x")
    (d / "2.eml").write_bytes(b"y")
    assert archive.detect_kind(str(d)) == "eml-bundle"


def test_detect_zimbra_export(tmp_path):
    d = tmp_path / "b2"
    sub = d / "Inbox"
    sub.mkdir(parents=True)
    (sub / "100").write_bytes(b"msg")
    (sub / "100.meta").write_bytes(b"<meta/>")
    assert archive.detect_kind(str(d)) == "zimbra-export"


def test_normalize_eml_bundle_from_pax_tgz(tmp_path):
    """回归:用长中文名 eml 打的 pax 包,归一化后应得到可读 eml 列表。"""
    inp = tmp_path / "input"
    inp.mkdir()
    longname = "Re_ " + "入出库通知采购" * 6 + ".eml"
    _make_tgz(str(inp / "bundle.tgz"), {longname: b"From: a@b\r\n\r\nbody"})
    work = tmp_path / "work"
    work.mkdir()
    result = archive.normalize(str(inp), str(work))
    assert result.kind == "eml-bundle"
    assert len(result.eml_paths) == 1
    assert open(result.eml_paths[0], "rb").read() == b"From: a@b\r\n\r\nbody"
    assert result.repacked_tgz is None


def test_normalize_loose_eml_files(tmp_path):
    inp = tmp_path / "input2"
    inp.mkdir()
    (inp / "m1.eml").write_bytes(b"a")
    (inp / "m2.eml").write_bytes(b"b")
    work = tmp_path / "work2"
    work.mkdir()
    result = archive.normalize(str(inp), str(work))
    assert result.kind == "eml-bundle"
    assert len(result.eml_paths) == 2


def test_normalize_zimbra_export_repacks_clean(tmp_path):
    inp = tmp_path / "input3"
    inp.mkdir()
    src = tmp_path / "src"
    (src / "Inbox").mkdir(parents=True)
    (src / "Inbox" / "100").write_bytes(b"msg")
    (src / "Inbox" / "100.meta").write_bytes(b"<meta/>")
    import tarfile as _t
    with _t.open(str(inp / "export.tgz"), "w:gz", format=_t.PAX_FORMAT) as tar:
        tar.add(str(src), arcname=".")
    work = tmp_path / "work3"
    work.mkdir()
    result = archive.normalize(str(inp), str(work))
    assert result.kind == "zimbra-export"
    assert result.repacked_tgz and os.path.exists(result.repacked_tgz)
    # 重打包后不含 pax 扩展头
    raw = open(result.repacked_tgz, "rb").read()
    import gzip
    assert b"PaxHeader" not in gzip.decompress(raw)
