import os
import uuid


def new_upload(temp_root):
    uid = uuid.uuid4().hex
    os.makedirs(os.path.join(temp_root, "uploads", uid, "input"))
    return uid


def upload_dir(temp_root, upload_id):
    return os.path.join(temp_root, "uploads", upload_id)


def _chunk_dir(temp_root, upload_id, file_index):
    return os.path.join(upload_dir(temp_root, upload_id), "chunks",
                        str(file_index))


def input_dir(temp_root, upload_id):
    return os.path.join(upload_dir(temp_root, upload_id), "input")


def save_chunk(temp_root, upload_id, file_index, chunk_index, data):
    d = _chunk_dir(temp_root, upload_id, file_index)
    os.makedirs(d, exist_ok=True)
    with open(os.path.join(d, str(chunk_index)), "wb") as fh:
        fh.write(data)


def missing_chunks(temp_root, upload_id, file_index, total_chunks):
    d = _chunk_dir(temp_root, upload_id, file_index)
    have = set(os.listdir(d)) if os.path.isdir(d) else set()
    return [i for i in range(total_chunks) if str(i) not in have]


def _safe_name(filename):
    return os.path.basename(filename.replace("\\", "/")) or "upload.bin"


def merge_file(temp_root, upload_id, file_index, total_chunks, filename):
    missing = missing_chunks(temp_root, upload_id, file_index, total_chunks)
    if missing:
        raise ValueError("missing chunks: %s" % missing)
    d = _chunk_dir(temp_root, upload_id, file_index)
    dest = os.path.join(input_dir(temp_root, upload_id), _safe_name(filename))
    with open(dest, "wb") as out:
        for i in range(total_chunks):
            with open(os.path.join(d, str(i)), "rb") as part:
                out.write(part.read())
    return dest
