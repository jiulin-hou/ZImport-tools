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


def test_normalize_picks_up_uppercase_eml_extension(tmp_path):
    """Outlook / some exporters write .EML; the case-insensitive match must
    catch them or the user silently sees 0 imported messages."""
    inp = tmp_path / "input_upper"
    inp.mkdir()
    (inp / "MSG.EML").write_bytes(b"a")
    (inp / "other.Eml").write_bytes(b"b")
    work = tmp_path / "w_upper"
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


def test_unpack_rejects_unix_compress_file(tmp_path):
    """名为 .tgz 但实际是老式 Unix compress (.Z) 数据 —— 给清晰错误,
    不让 tarfile 抛 Python 内部堆栈。"""
    fake = tmp_path / "陈瑞收件箱邮件.tgz"
    # `compress` 魔术字 1F 9D + 一堆随便的 LZW 字节
    fake.write_bytes(b"\x1f\x9d\x90" + b"\x00" * 200)
    with pytest.raises(ValueError) as exc:
        archive.unpack_tgz(str(fake), str(tmp_path / "out"))
    msg = str(exc.value)
    # 错误信息应该提到 compress / .Z,而不是 ReadError 之类
    assert ".Z" in msg or "compress" in msg.lower()


def test_unpack_rejects_zip_file(tmp_path):
    """名为 .tgz 但是 ZIP 数据 —— 也给清晰错误。"""
    fake = tmp_path / "x.tgz"
    fake.write_bytes(b"PK\x03\x04" + b"\x00" * 200)
    with pytest.raises(ValueError) as exc:
        archive.unpack_tgz(str(fake), str(tmp_path / "out2"))
    assert "zip" in str(exc.value).lower()


def test_unpack_rejects_random_garbage(tmp_path):
    """随机字节 —— catch-all 错误信息。"""
    fake = tmp_path / "y.tgz"
    fake.write_bytes(b"this is just plain text, not an archive at all\n" * 5)
    with pytest.raises(ValueError) as exc:
        archive.unpack_tgz(str(fake), str(tmp_path / "out3"))
    assert "tgz" in str(exc.value) or "gzip" in str(exc.value) or "归档" in str(exc.value)
