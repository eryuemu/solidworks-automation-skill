"""交付格式版本核验：文件展开与 VersionHistory 解析。"""
from __future__ import annotations

from scripts import sw_version_guard as guard


def test_release_number_reads_format_prefix():
    assert guard.release_number("16000[2025/268]") == 16000
    assert guard.release_number(" 18000[2025/268]") == 18000
    assert guard.release_number("") is None
    assert guard.release_number(None) is None


def test_native_files_skips_lock_and_filters_suffix(tmp_path):
    (tmp_path / "a.SLDPRT").write_bytes(b"x")
    (tmp_path / "b.SLDASM").write_bytes(b"x")
    (tmp_path / "c.slddrw").write_bytes(b"x")
    (tmp_path / "~$a.SLDPRT").write_bytes(b"x")
    (tmp_path / "note.txt").write_text("x")
    sub = tmp_path / "sub"
    sub.mkdir()
    (sub / "d.SLDPRT").write_bytes(b"x")
    found = {path.name for path in guard.native_files([str(tmp_path)])}
    assert found == {"a.SLDPRT", "b.SLDASM", "c.slddrw", "d.SLDPRT"}


def test_native_files_deduplicates_same_file(tmp_path):
    target = tmp_path / "a.SLDPRT"
    target.write_bytes(b"x")
    assert len(guard.native_files([str(target), str(tmp_path)])) == 1


def test_audit_blocks_without_native_files(tmp_path):
    assert guard.audit([str(tmp_path)])["status"] == "blocked"
