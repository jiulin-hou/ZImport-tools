from zimport_tools import uploads


def test_chunk_save_resume_and_merge(tmp_path):
    root = str(tmp_path)
    uid = uploads.new_upload(root)
    # 乱序保存分片 2,0(故意漏 1)
    uploads.save_chunk(root, uid, 0, 2, b"CCC")
    uploads.save_chunk(root, uid, 0, 0, b"AAA")
    missing = uploads.missing_chunks(root, uid, 0, total_chunks=3)
    assert missing == [1]
    # 补上漏掉的分片
    uploads.save_chunk(root, uid, 0, 1, b"BBB")
    assert uploads.missing_chunks(root, uid, 0, total_chunks=3) == []
    dest = uploads.merge_file(root, uid, 0, total_chunks=3,
                              filename="big.tgz")
    assert open(dest, "rb").read() == b"AAABBBCCC"


def test_merge_rejects_when_chunk_missing(tmp_path):
    root = str(tmp_path)
    uid = uploads.new_upload(root)
    uploads.save_chunk(root, uid, 0, 0, b"AAA")
    import pytest
    with pytest.raises(ValueError):
        uploads.merge_file(root, uid, 0, total_chunks=2, filename="x.tgz")


def test_filename_sanitized_on_merge(tmp_path):
    root = str(tmp_path)
    uid = uploads.new_upload(root)
    uploads.save_chunk(root, uid, 0, 0, b"data")
    dest = uploads.merge_file(root, uid, 0, total_chunks=1,
                              filename="../../etc/passwd")
    # 合并后的文件必须落在该上传的 input 目录内
    assert uploads.upload_dir(root, uid) in dest
    assert "passwd" in dest and ".." not in dest.split(uid)[1]
