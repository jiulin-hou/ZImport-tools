import collections
import os
import tarfile


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
    traversal and link entries. Returns dest_dir."""
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
