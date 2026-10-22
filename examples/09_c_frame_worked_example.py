"""C 形零件完整建模示例：16 特征 + 每一步体积核对。

一个真实课程零件（手动冲压机架）的定稿构建脚本，用来演示这个 skill 的推荐用法：
单实体、两侧对称拉伸、竖直回转体轴套、叉口、销孔、导孔、凸台、多组圆角，
**并且每个特征都打印体积增量与手算期望值对照**——这是判断"特征到底建对没有"的
唯一可靠手段（包围盒对了不代表几何对了）。

踩过的坑都写在注释里，完整清单见 docs/solidworks-com-pitfalls.md。

要点回顾：
  * Ø15 轴套的轴线必须竖直 -> 圆画在 **Top Plane**（画在前视基准面上会变成横向圆饼）
  * 圆弧要比同草图里的直线**先画**，否则轮廓不闭合、拉伸静默产出 0 体积
  * 凸边圆角和凹边圆角**分开两次做**，否则体积正负抵消会被误判成"没生效"
  * SaveAs 到同名已打开文档会静默返回 False -> 用唯一文件名 + reopen 复核

用法：先确认 SolidWorks 已开着有窗口的实例，然后
    python examples/09_c_frame_worked_example.py
期望末行：reopen vol=95273.397777
"""
import os
import sys
import tempfile
import time

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)          # examples/ 的上一级就是仓库根
sys.path.insert(0, SKILL_DIR)

from scripts.sw_connect import mm, get_com_member                      # noqa: E402
from scripts.sw_session import SolidWorksSession                       # noqa: E402
from scripts.sw_part import (sketch, sketch_line, sketch_corner_rectangle,   # noqa: E402
                             sketch_circle, sketch_arc, extrude_boss,
                             extrude_midplane, _ensure_sketch_selected)
from scripts.sw_measure import collect_mass_properties, collect_bounding_box  # noqa: E402
from scripts.sw_edge_select import iter_model_edges                    # noqa: E402

# 必须用国标模板，否则 NewPart 出来的文档单位是米
TPL = r"C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2025\templates\gb_part.prtdot"
# 产物先落本地磁盘再自行拷走：SolidWorks 对网络盘/WSL 路径的文件锁很不友好
OUT_DIR = os.path.join(tempfile.gettempdir(), "sw_c_frame_example")
os.makedirs(OUT_DIR, exist_ok=True)

POLY = [(30, 100), (30, 85), (60, 85), (60, 70), (75, 70), (90, 55), (90, 10),
        (60, 10), (60, 45), (20, 45), (20, 100)]
SEGS = [(a, b) for a, b in zip(POLY, POLY[1:] + POLY[:1])
        if not (a == (20, 100) and b == (30, 100))]


def cut_midplane(m, ref, depth):
    _ensure_sketch_selected(m, ref)
    return m.FeatureManager.FeatureCut4(
        True, False, True, 6, 0, depth, 0,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False, False,
        True, True, False, False, False, 0, 0, False, False)


def cut_thru_up(m, ref):
    _ensure_sketch_selected(m, ref)
    return m.FeatureManager.FeatureCut4(
        True, False, True, 1, 0, 0.01, 0,
        False, False, False, False, 0.0, 0.0,
        False, False, False, False, False,
        True, True, False, False, False, 0, 0, False, False)


def state(m):
    m.ForceRebuild3(True)
    return (collect_mass_properties(m)["volume_mm3"],
            collect_bounding_box(m)["size_mm"])


def step(tag, m, prev):
    v, size = state(m)
    print(f"  {tag:22s} vol={v:9.1f}  d={v - prev:+9.1f}  size={size}", flush=True)
    return v


def fillet_edges(m, label, axis, targets, radius, tol=0.9):
    before = state(m)[0]
    descs, _ = iter_model_edges(m)
    key = {"x": 0, "y": 1, "z": 2}[axis]
    hit = [d for d in descs
           if d.get("mid_point_mm") is not None
           and abs((d.get("direction") or [0, 0, 0])[key]) >= 0.99
           and any(all(abs(a - b) <= tol for a, b in zip(d["mid_point_mm"], t))
                   for t in targets)]
    m.ClearSelection2(True)
    n = 0
    for d in hit:
        try:
            if d["edge"].Select2(True, 0):
                n += 1
        except Exception:
            pass
    ok = n > 0
    if ok:
        try:
            m.FeatureManager.FeatureFillet(195, mm(radius), 0, 0)
        except Exception as exc:
            print(f"  !! {label} {repr(exc)[:90]}", flush=True)
            ok = False
    after = state(m)[0]
    print(f"  {label:22s} 选中{n}/{len(hit)} r={radius} d={after - before:+8.1f}  "
          f"{'OK' if ok and abs(after - before) > 1e-6 else 'FAIL'}", flush=True)
    return ok


with SolidWorksSession() as s:
    m = s.new_part(template_path=TPL)

    # 1 C 形主体（圆弧必须第一个画）
    with sketch(m, "Front Plane") as r1:
        sketch_arc(m, mm(25), mm(100), mm(20), mm(100), mm(30), mm(100), -1)
        for a, b in SEGS:
            sketch_line(m, mm(a[0]), mm(a[1]), mm(b[0]), mm(b[1]))
    extrude_midplane(m, r1, mm(10))
    v = step("1 主体 mid10", m, 0)
    if v < 1000:
        print("!! 轮廓无效", flush=True); sys.exit(2)

    # 2 竖直轴套立柱：Ø15 画在上视基准面 -> 沿 +Y 拉 85
    with sketch(m, "Top Plane") as r2:
        sketch_circle(m, mm(40), mm(0), mm(7.5))
    extrude_boss(m, r2, mm(85), direction=False)
    v = step("2 轴套立柱 Ø15 竖直", m, v)

    # 3 拱空切除：把立柱在 Y=10..45 段切掉（恢复 35 净空）
    with sketch(m, "Front Plane") as r3:
        sketch_corner_rectangle(m, mm(20), mm(10), mm(60), mm(45))
    cut_midplane(m, r3, mm(24))
    v = step("3 拱空切除", m, v)

    # 4 让位槽切除：把立柱在 Y=55..75 段切掉 -> 剩上下两段轴套
    with sketch(m, "Front Plane") as r4:
        sketch_corner_rectangle(m, mm(30), mm(55), mm(50), mm(75))
    cut_midplane(m, r4, mm(24))
    v = step("4 让位槽 20x20", m, v)

    # 5 手柄叉口
    with sketch(m, "Front Plane") as r5:
        sketch_corner_rectangle(m, mm(20), mm(96), mm(30), mm(105))
    cut_midplane(m, r5, mm(3))
    v = step("5 叉口 w3", m, v)

    # 6 Ø3.5 横向销孔
    with sketch(m, "Front Plane") as r6:
        sketch_circle(m, mm(25), mm(100), mm(1.75))
    cut_midplane(m, r6, mm(24))
    v = step("6 销孔 Ø3.5", m, v)

    # 7 Ø7 竖直导孔（底板还没长，只切到两段轴套）
    with sketch(m, "Top Plane") as r7:
        sketch_circle(m, mm(40), mm(0), mm(3.5))
    cut_thru_up(m, r7)
    v = step("7 导孔 Ø7", m, v)

    # 8 底板
    with sketch(m, "Top Plane") as r8:
        sketch_corner_rectangle(m, mm(0), mm(-32.5), mm(100), mm(32.5))
    extrude_boss(m, r8, mm(10), direction=False)
    v = step("8 底板 100x65x10", m, v)

    # 9 砧面凸台 Ø15x2.5
    with sketch(m, "Top Plane") as r9:
        sketch_circle(m, mm(40), mm(0), mm(7.5))
    extrude_boss(m, r9, mm(12.5), direction=False)
    v = step("9 砧面凸台 Ø15", m, v)

    # 10 Ø3 通孔
    with sketch(m, "Top Plane") as r10:
        sketch_circle(m, mm(40), mm(0), mm(1.5))
    cut_thru_up(m, r10)
    v = step("10 通孔 Ø3", m, v)

    print("圆角:", flush=True)
    fillet_edges(m, "R10 底板四角", "y",
                 [(0, 5, -32.5), (0, 5, 32.5), (100, 5, -32.5), (100, 5, 32.5)], 10)
    fillet_edges(m, "R5 右侧轮廓", "z",
                 [(60, 85, 0), (60, 70, 0), (75, 70, 0), (90, 55, 0)], 5)
    fillet_edges(m, "R5 让位槽角", "z",
                 [(30, 55, 0), (50, 55, 0), (50, 75, 0), (30, 75, 0)], 5)
    fillet_edges(m, "R5 立壁-底板根部", "z", [(90, 10, 0), (60, 10, 0)], 5)
    # 拱空两端必须分开做：左端(20,45)凸 -53.65，右端(60,45)凹 +53.65，
    # 合在一条命令里体积正好抵消，会被误判成"圆角没生效"。
    fillet_edges(m, "R5 拱空左端", "z", [(20, 45, 0)], 5)
    fillet_edges(m, "R5 拱顶右端", "z", [(60, 45, 0)], 5)

    v, size = state(m)
    print("FINAL vol =", v, "size =", size, flush=True)
    print("期望 vol = 95273.397777", flush=True)
    out = os.path.join(OUT_DIR, f"c_frame_{time.strftime('%H%M%S')}.sldprt")
    if not m.SaveAs(out):
        print("!! 保存失败", flush=True); sys.exit(3)
    print("saved", out, flush=True)
    s.sw.CloseDoc(get_com_member(m, "GetTitle"))
    got, gs = state(s.open(out))
    print(f"reopen vol={got:.4f} size={gs}", flush=True)
    print("FINAL_PATH " + out, flush=True)
