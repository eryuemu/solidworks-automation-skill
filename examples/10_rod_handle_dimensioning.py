"""压杆建模 + 草图驱动尺寸标注示例：14 张草图完全定义工作流的最小样本。

一个真实课程零件（手动冲压装置的压杆：Ø7 杆身 + Ø15×2 轴环 + Ø3 小轴，全回转体）
的定稿构建脚本，演示 2026-10-23 二轮踩坑后的**草图标注工作流**：

  * 关『输入尺寸值』弹窗（SetUserPreferenceToggle(10, False)）后，
    草图编辑态连续 AddDimension2 **不会阻塞**（旧文档"必须退草图+SendKeys"作废）
  * 关系用 `SketchAddConstraints("sgHORIZONTAL"/...)` 字符串名直给
  * 直径尺寸 = 选**径向线 + 轴线构造线**（值自动 ×2 带 Ø）
  * 圆边定位走 `ICurve::CircleParams`（SW2025 下 GetCurveParams2 排列与假设不符，
    GetLength 恒 0、整圆无端点顶点——`describe_edge` 已修复）
  * `FullyDefineSketch` 要**跑两遍**；仍欠定义用 `AutoDimension2` 兜底；
    判据 `GetConstrainedStatus`（2=欠定义，3=完全定义）
  * 体积逐特征与手算对照；存盘 reopen 复核

完整踩坑清单见 docs/solidworks-com-pitfalls.md（第 25/26/31-33 条）。

用法：先确认 SolidWorks 已开着有窗口的实例，然后
    python examples/10_rod_handle_dimensioning.py
期望末行：reopen vol=3010.198
"""
import math
import os
import sys
import tempfile
import time

SCRIPT_DIR = os.path.abspath(os.path.dirname(__file__))
SKILL_DIR = os.path.dirname(SCRIPT_DIR)
sys.path.insert(0, SKILL_DIR)

from scripts.sw_connect import (mm, get_com_member,                     # noqa: E402
                                create_empty_dispatch_variant)
from scripts.sw_session import SolidWorksSession                        # noqa: E402
from scripts.sw_part import sketch, sketch_line, sketch_arc, revolve_boss  # noqa: E402
from scripts.sw_measure import collect_mass_properties, collect_bounding_box  # noqa: E402
from scripts.sw_edge_select import iter_model_edges                     # noqa: E402
from scripts.sw_sketch_quality import (inspect_sketch,                  # noqa: E402
                                       fully_define_sketch,
                                       inspect_model_sketches)
from scripts.sw_part import auto_dimension_sketch                       # noqa: E402

TPL = r"C:\ProgramData\SOLIDWORKS\SOLIDWORKS 2025\templates\gb_part.prtdot"
OUT_DIR = os.path.join(tempfile.gettempdir(), "sw_rod_dim_example")
os.makedirs(OUT_DIR, exist_ok=True)

# 半剖轮廓：X=半径方向，Y=轴向（旋转轴 = 草图 Y 轴）。全直线，不碰 CreateArc。
PROFILE = [(0, -6.5), (1.5, -6.5), (1.5, 0), (3.5, 0), (3.5, 50),
           (7.5, 50), (7.5, 52), (3.5, 52), (3.5, 70), (0, 70)]
KINDS = ["sgVERTICAL", "sgHORIZONTAL"] * 5      # 逐段横竖


def select_origin(model, append=False):
    """原点要素中英文界面名称不同，按真实特征名枚举。"""
    origins = ["Origin", "原点"]
    feat = get_com_member(model, "FirstFeature")
    while feat:
        try:
            if str(get_com_member(feat, "GetTypeName2")) == "OriginProfileFeature":
                origins.insert(0, str(get_com_member(feat, "Name")))
                break
        except Exception:
            pass
        feat = get_com_member(feat, "GetNextFeature")
    for name in dict.fromkeys(origins):
        for point in ("Point1", "点1"):
            try:
                if model.Extension.SelectByID2(f"{point}@{name}", "EXTSKETCHPOINT",
                                               0, 0, 0, append, 0,
                                               create_empty_dispatch_variant(), 0):
                    return True
            except Exception:
                pass
    return False


def rel(model, segs, rtype):
    """对选中实体加几何关系；segs 里的 True 代表原点。"""
    model.ClearSelection2(True)
    for i, seg in enumerate(segs):
        if seg is True:
            if not select_origin(model, append=i > 0):
                return False
            continue
        if not seg.Select2(i > 0, 0):
            return False
    model.SketchAddConstraints(rtype)
    return True


def dim(model, ents, x_mm, y_mm, with_origin=False):
    """给选中实体加驱动尺寸；标注文字放草图坐标 (x_mm, y_mm)。"""
    model.ClearSelection2(True)
    n = 0
    if with_origin:
        if not select_origin(model, append=False):
            return None
        n = 1
    for seg in ents:
        if not seg.Select2(n > 0, 0):
            return None
        n += 1
    return model.AddDimension2(mm(x_mm), mm(y_mm), 0)


def finish_sketch(model, label):
    """FullyDefineSketch 跑两遍；仍欠定义用 AutoDimension2 兜底。判据 status=3。"""
    last = None
    for _ in range(2):
        try:
            rec = fully_define_sketch(model)
            if rec["status"] == "pass":
                print(f"    {label}: 完全定义 OK dims={rec['dimension_count']} "
                      f"rels={rec['relation_count']}", flush=True)
                return True
            last = rec
        except Exception:
            last = None
    auto_dimension_sketch(model)
    rec = inspect_sketch(get_com_member(model.SketchManager, "ActiveSketch"))
    if rec["status"] == "pass":
        print(f"    {label}: 完全定义 OK（AutoDimension 兜底）", flush=True)
        return True
    print(f"    {label}: !! 未完全定义 {last}", flush=True)
    return False


def circle_edges(model):
    """圆边定位：SW2025 下唯一可靠的是 ICurve::CircleParams。

    GetLength 恒 0、整圆 GetStartVertex 返回 None、GetCurveParams2 排列与假设不符。
    """
    descs, _ = iter_model_edges(model)
    out = []
    for d in descs:
        try:
            curve = get_com_member(d["edge"], "GetCurve")
            if not bool(get_com_member(curve, "IsCircle")):
                continue
            p = list(get_com_member(curve, "CircleParams") or [])
            if len(p) >= 7:
                center = [float(p[i]) * 1000.0 for i in range(3)]
                radius = abs(float(p[6])) * 1000.0
                out.append((d["edge"], center, radius))
        except Exception:
            continue
    return out


def fillet_circles(model, label, radius, wanted, tol=0.2):
    before = state(model)[0]
    hits = []
    for e, c, r in circle_edges(model):
        for cx, cy, cz, wr in wanted:
            if (abs(r - wr) < tol and abs(c[0] - cx) < tol
                    and abs(c[1] - cy) < tol and abs(c[2] - cz) < tol):
                hits.append((e, c, r))
                break
    model.ClearSelection2(True)
    for edge, _, _ in hits:
        edge.Select2(True, 0)
    model.FeatureManager.FeatureFillet(195, mm(radius), 0, 0)
    after = state(model)[0]
    print(f"  {label:16s} {len(hits)}/{len(wanted)} 边 d={after - before:+8.3f}", flush=True)
    return hits


def state(model):
    model.ForceRebuild3(True)
    return (collect_mass_properties(model)["volume_mm3"],
            [round(v, 3) for v in collect_bounding_box(model)["size_mm"]])


with SolidWorksSession() as session:
    session.sw.SetUserPreferenceToggle(10, False)   # 关『输入尺寸值』弹窗（否则标注必卡）
    model = session.new_part(template_path=TPL)

    with sketch(model, "Front Plane") as ref:
        centerline = sketch_line(model, 0, mm(-12), 0, mm(82))   # 回转轴（构造线）
        centerline.ConstructionGeometry = True
        lines = [sketch_line(model, mm(a[0]), mm(a[1]), mm(b[0]), mm(b[1]))
                 for a, b in zip(PROFILE, PROFILE[1:] + PROFILE[:1])]
        for seg, kind in zip(lines, KINDS):
            rel(model, [seg], kind)
        rel(model, [lines[9], centerline], "sgCOLINEAR")   # 轴线段与中心线共线
        rel(model, [True, centerline], "sgCOINCIDENT")     # 中心线过原点
        rel(model, [True, lines[2]], "sgCOINCIDENT")       # 杆身下端面过原点
        rel(model, [lines[3], lines[7]], "sgCOLINEAR")     # 上下两段杆身同半径
        # 指导书尺寸：Ø3 / Ø7 / Ø15 / 6.5 / 50 / 2 / 70
        dim(model, [lines[1], centerline], 5, -10)              # Ø3（线+中心线=直径）
        dim(model, [lines[3], centerline], 10, 25)              # Ø7
        dim(model, [lines[5], centerline], 11, 42)              # Ø15
        dim(model, [lines[0]], 4, -9, with_origin=True)         # 6.5 小轴长
        dim(model, [lines[4]], 9.5, 18, with_origin=True)       # 50 轴环下端
        dim(model, [lines[4], lines[6]], 12, 51)                # 2 轴环厚
        dim(model, [lines[8]], -5, 35, with_origin=True)        # 70 总长
        finish_sketch(model, "旋转轮廓")
    revolve_boss(model, ref, 2 * math.pi)
    v, size = state(model)
    print(f"  旋转           vol={v:.3f}（期望 3016.322）size={size}", flush=True)

    fillet_circles(model, "顶面 R0.5", 0.5, [(0, 70, 0, 3.5)])
    fillet_circles(model, "轴环下端 R0.5", 0.5, [(0, 50, 0, 7.5)])
    fillet_circles(model, "轴环上端 R0.5", 0.5, [(0, 52, 0, 7.5)])
    v, size = state(model)
    print(f"FINAL vol={v:.3f}（期望 3010.198）size={size}", flush=True)

    out = os.path.join(OUT_DIR, f"rod_{time.strftime('%H%M%S')}.SLDPRT")
    if not model.SaveAs(out):
        raise RuntimeError("SaveAs 失败")
    title = get_com_member(model, "GetTitle")
    session.sw.CloseDoc(title)
    m2 = session.open(out)
    v2, _ = state(m2)
    print(f"reopen vol={v2:.3f}", flush=True)
    for rec in inspect_model_sketches(m2)["sketches"]:
        print(f"  草图[{rec['name']}] status={rec['status']} "
              f"constrained={rec['constrained_status']} dims={rec['dimension_count']}", flush=True)
    print(f"FINAL_PATH {out}", flush=True)
