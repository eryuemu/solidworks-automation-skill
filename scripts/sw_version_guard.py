"""交付文件格式版本核验：只读 ISldWorks::VersionHistory，不打开、不保存、不修改文档。

SolidWorks 打开旧格式文件后，普通保存（Save3）会把磁盘文件静默升级为当前版本格式
（本机 SW2025 实测 16000 → 18000）。要求交付件保持旧格式（例如 2023 可打开的 16000）
时，改完必须从磁盘核验，而不是只看内存里的已打开文档。
"""
from __future__ import annotations

import argparse
import contextlib
import io
import json
import re
from pathlib import Path
from typing import Iterable, Sequence

_NATIVE_SUFFIXES = {".sldprt", ".sldasm", ".slddrw"}
_VERSION_PREFIX = re.compile(r"^\s*(\d+)")


def native_files(inputs: Iterable[str]) -> list[Path]:
    """展开文件/目录为原生 SolidWorks 文件列表；跳过 ~$ 锁文件。"""
    result: list[Path] = []
    seen: set[str] = set()
    for item in inputs:
        path = Path(item).expanduser()
        candidates = sorted(path.rglob("*")) if path.is_dir() else [path]
        for candidate in candidates:
            if (candidate.is_file() and candidate.suffix.lower() in _NATIVE_SUFFIXES
                    and not candidate.name.startswith("~$")):
                key = str(candidate.resolve()).casefold()
                if key not in seen:
                    seen.add(key)
                    result.append(candidate)
    return result


def release_number(entry: str | None) -> int | None:
    """从 `16000[2025/268]` 这类历史项提取格式号。"""
    match = _VERSION_PREFIX.match(str(entry or ""))
    return int(match.group(1)) if match else None


def last_version(sw, path: str | Path) -> str | None:
    try:
        from .sw_connect import get_com_member
    except ImportError:
        from sw_connect import get_com_member
    history = get_com_member(sw, "VersionHistory", str(path))
    values = list(history) if isinstance(history, (list, tuple)) else []
    return str(values[-1]) if values else None


def _rows_for_files(sw, files: Sequence[Path], expected: int | None) -> list[dict]:
    rows: list[dict] = []
    for path in files:
        try:
            entry = last_version(sw, path)
        except Exception as error:  # 单个文件读取失败也保留其余结果
            rows.append({"path": str(path), "last_version": None, "release": None,
                         "ok": False, "error": f"{type(error).__name__}: {error}"})
            continue
        release = release_number(entry)
        rows.append({"path": str(path), "last_version": entry, "release": release,
                     "ok": (release == expected) if expected is not None else None})
    return rows


def audit(inputs: Sequence[str], expected: int | None = None, *, sw_version: int | None = None) -> dict:
    """核验一组交付文件的最后保存格式；expected 为 None 时只报告不判定。"""
    files = native_files(inputs)
    if not files:
        return {"status": "blocked", "error": "未找到 SLDPRT/SLDASM/SLDDRW 文件", "files": []}
    try:
        from .sw_connect import connect_solidworks
        from .sw_read_guard import SolidWorksReadGuard
    except ImportError:
        from sw_connect import connect_solidworks
        from sw_read_guard import SolidWorksReadGuard
    rows: list[dict] = []
    metadata: dict = {}
    launched = False
    try:
        with SolidWorksReadGuard():
            with contextlib.redirect_stdout(io.StringIO()):  # 连接日志不混入 JSON 输出
                sw, _model, metadata = connect_solidworks(
                    version=sw_version, wait_seconds=20, return_metadata=True)
            launched = bool(metadata.get("started_by_cad_studio"))
            rows = _rows_for_files(sw, files, expected)
    except Exception as error:
        return {"status": "blocked", "error": f"{type(error).__name__}: {error}", "files": rows}
    finally:
        if launched and "sw" in locals():
            try:
                sw.ExitApp()
            except Exception:
                pass
    failed = any(row.get("ok") is False for row in rows)
    status = "fail" if failed else ("pass" if expected is not None else "review_required")
    return {"status": status, "expected_release": expected,
            "solidworks": {"revision": metadata.get("actual_revision"), "year": metadata.get("actual_version")},
            "files": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="只读核验 SLDPRT/SLDASM/SLDDRW 的最后保存格式版本；不修改文件。")
    parser.add_argument("--input", action="append", required=True, help="文件或目录，可重复")
    parser.add_argument("--expect", type=int, help="期望格式号（如 16000）；不传则只报告")
    parser.add_argument("--sw-version", type=int, help="SolidWorks 年份（可选，用于固定 ProgID）")
    parser.add_argument("--json-out", help="把 JSON 结果写到该路径（父目录不存在时创建）")
    args = parser.parse_args(argv)
    result = audit(args.input, args.expect, sw_version=args.sw_version)
    text = json.dumps(result, ensure_ascii=False, indent=2)
    print(text)
    if args.json_out:
        output = Path(args.json_out).expanduser().absolute()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(text + "\n", encoding="utf-8")
    if result["status"] == "blocked":
        return 2
    return 0 if result["status"] in ("pass", "review_required") else 1


if __name__ == "__main__":
    raise SystemExit(main())
