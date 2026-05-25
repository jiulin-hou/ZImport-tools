import collections
import os
import tarfile


# Map of leading magic bytes to a human-readable name + hint of what to do.
# Anything not matching one of the "tar-readable" formats gets rejected up
# front so we never feed `tarfile.open()` something that will trigger the
# inscrutable "file could not be opened successfully" multi-method dump.
_TAR_READABLE = {"gzip", "bzip2", "xz"}  # tarfile.open("r:*") handles these

_KNOWN_FORMATS = (
    # (magic prefix, name, friendly hint)
    (b"\x1f\x8b",                  "gzip",     None),
    (b"BZh",                       "bzip2",    None),
    (b"\xfd7zXZ\x00",              "xz",       None),
    (b"\x1f\x9d",                  "Unix compress (.Z)",
     "看起来是老 Unix `compress` 工具产的 .Z 文件,被改成了 .tgz 后缀。"
     "请用 `uncompress` 解出再 `gzip` 重新打包,或在压缩工具里改选 gzip 格式。"),
    (b"PK\x03\x04",                "zip",
     "看起来是 ZIP 包,被改成了 .tgz 后缀。请用压缩工具改选 gzip/tar.gz 格式。"),
    (b"Rar!\x1a\x07",              "rar",
     "看起来是 RAR 包,本工具只支持 .tar.gz / .tgz。"),
    (b"7z\xbc\xaf\x27\x1c",        "7z",
     "看起来是 7z 包,本工具只支持 .tar.gz / .tgz。"),
)


def _check_archive_magic(path):
    """Sniff first bytes; raise ValueError with a friendly message if the
    file is not a tar-readable archive. Returns silently on success."""
    with open(path, "rb") as f:
        head = f.read(512)
    for magic, name, hint in _KNOWN_FORMATS:
        if head.startswith(magic):
            if name in _TAR_READABLE:
                return  # gzip/bzip2/xz — tarfile.open("r:*") will handle it
            msg = "文件不是 .tar.gz / .tgz 归档(检测到格式:%s)。" % name
            if hint:
                msg += " " + hint
            raise ValueError(msg)
    # Maybe a raw (uncompressed) tar? ustar magic at offset 257.
    if len(head) >= 263 and head[257:262] == b"ustar":
        return
    # Empty file gets its own message (common cause: partial/aborted upload)
    if not head:
        raise ValueError("文件为空 —— 可能是上传中断或文件本身就是 0 字节。")
    raise ValueError(
        "文件不是有效的 tgz/tar 归档(开头字节看不出来是哪种已知格式)。"
        "请确认它是用 gzip 压缩的 tar 包(`.tar.gz` / `.tgz`)。"
    )


def _safe_members(tar, dest):
    dest = os.path.realpath(dest)
    members = tar.getmembers()
    for m in members:
        if m.issym() or m.islnk():
            raise ValueError("archive contains a link entry: %s" % m.name)
        target = os.path.realpath(os.path.join(dest, m.name))
        if target != dest and not target.startswith(dest + os.sep):
            raise ValueError("unsafe path in archive: %s" % m.name)
    return members


def unpack_tgz(tgz_path, dest_dir):
    """Extract a .tgz to dest_dir. Handles pax/gnu formats. Rejects path
    traversal and link entries. Returns dest_dir.

    Raises ValueError (with a user-friendly Chinese message) if the file
    isn't actually a tar-readable archive.
    """
    _check_archive_magic(tgz_path)
    os.makedirs(dest_dir, exist_ok=True)
    with tarfile.open(tgz_path, "r:*") as tar:
        members = _safe_members(tar, dest_dir)
        tar.extractall(dest_dir, members=members)
    return dest_dir


def detect_kind(extracted_dir):
    """Zimbra 完整导出 tgz 的每个条目都带一个 .meta 旁挂文件;
    据此区分 'zimbra-export' 与纯 'eml-bundle'。"""
    for root, dirs, files in os.walk(extracted_dir):
        for f in files:
            if f.endswith(".meta"):
                return "zimbra-export"
    return "eml-bundle"


NormalizedInput = collections.namedtuple(
    "NormalizedInput", ["kind", "eml_paths", "repacked_tgz"])


def _collect_emls(directory):
    out = []
    for root, dirs, files in os.walk(directory):
        for f in sorted(files):
            if f.lower().endswith(".eml"):
                out.append(os.path.join(root, f))
    return out


def _repack_clean(src_dir, dest_tgz):
    """重新打包成 GNU 格式 tgz(无 pax 扩展头,长名用 @LongLink)。"""
    entries = []
    for root, dirs, files in os.walk(src_dir):
        for f in sorted(files):
            full = os.path.join(root, f)
            entries.append((full, os.path.relpath(full, src_dir)))
    entries.sort(key=lambda x: x[1])
    with tarfile.open(dest_tgz, "w:gz", format=tarfile.GNU_FORMAT) as tar:
        for full, arc in entries:
            tar.add(full, arcname=arc, recursive=False)
    return dest_tgz


def normalize(input_dir, work_dir):
    """把任务输入目录归一化。input_dir 内或是一个 .tgz,或是若干 .eml。"""
    os.makedirs(work_dir, exist_ok=True)
    entries = sorted(os.listdir(input_dir))
    tgzs = [e for e in entries if e.endswith((".tgz", ".tar.gz"))]
    if tgzs:
        extracted = os.path.join(work_dir, "extracted")
        unpack_tgz(os.path.join(input_dir, tgzs[0]), extracted)
        kind = detect_kind(extracted)
        if kind == "zimbra-export":
            repacked = os.path.join(work_dir, "clean.tgz")
            _repack_clean(extracted, repacked)
            return NormalizedInput("zimbra-export", [], repacked)
        return NormalizedInput("eml-bundle", _collect_emls(extracted), None)
    emls = [os.path.join(input_dir, e) for e in entries
            if e.lower().endswith(".eml")]
    return NormalizedInput("eml-bundle", emls, None)
