# SolidWorks COM 自动化实战踩坑记录

在 **SolidWorks 2025 SP5 + pywin32（Windows 便携 Python 3.12）** 上，用一个真实课程零件
（C 形冲压机架：16 个特征、含回转体轴套、叉口、圆角）反复重建时实测出来的问题。
2026-10-23 二轮：同一课程的压杆/手柄建模 + 三个零件 14 张草图标注到完全定义，又补一批（第九节）。
每条都是"踩过、验证过、有规避办法"的，不是文档转抄。

---

## 一、连接与会话

### 1. 无窗口实例会毒化整个 COM 注册表
用 `Dispatch("SldWorks.Application")` 或 `New-Object -ComObject` 拉起的 SolidWorks，
如果卡在初始化就会留下一个 **`MainWindowHandle = 0` 的无窗口进程**。它占着 ROT，
之后所有 `GetActiveObject` 都返回死对象，报：

```
pywintypes.com_error: (-2147221021, '操作无法使用', None, None)
→ [SW_INSTANCE_NOT_READY] attach: 已有实例尚不可连接，已阻止重复启动
```

**排查**：
```powershell
Get-Process SLDWORKS | Format-List Id,Responding,MainWindowHandle,StartTime
```
`MainWindowHandle` 为 0 就是它。**只能杀掉那个进程**，再让用户自己开一个有窗口的实例。

### 2. 让他自己开 SolidWorks，别脚本冷启动
冷启动会弹许可/欢迎/文档恢复框，而且**杀进程后的下一次启动一定会弹"文档恢复"**。
启动前要先说清楚会弹什么。

### 3. `connect_solidworks()` 返回 2 个值，不是 3 个
```python
got = connect_solidworks()
sw = got[0]
```

---

## 二、坐标系与基准面（最容易想当然的地方）

### 4. 基准面映射和直觉相反，盲拉伸走负方向
实测 SW2025 默认零件模板：

| 基准面 | 草图 (x,y) → 模型坐标 | 法向 |
|---|---|---|
| Front Plane（前视） | → (x, y, 0)，即模型 **XY** 面 | Z |
| Top Plane（上视） | → (x, 0, **−y**)，即模型 **XZ** 面 | Y |
| Right Plane（右视） | → (0, y, **−x**)，即模型 **YZ** 面 | X |

**盲拉伸（blind）默认朝 −法向**。所以"上视画圆 + 默认方向拉伸"会往 −Y 走，切/凸都做不出来。

### 5. 回转体的轴线方向 = 草图所在基准面决定
这是最容易犯的错。圆画在哪张基准面上，拉伸出来的圆柱轴线就**垂直于那张面**。

> 真实教训：需要一个**轴线竖直**的 Ø15 轴套（竖直导孔要穿过它），
> 却把圆画在了前视基准面上 → 轴线变成沿宽度方向 → 出来两个朝侧面凸出的"圆饼"。
> 正确做法：轴线沿 Y → 圆画在 **Top Plane**。

**动手前先问一句：这个回转体的轴线沿哪根轴？** 再选草图面。

---

## 三、草图

### 6. `CreateArc` 必须比同草图里的直线**先画**
同一个草图内，先画直线再画圆弧 → 弧的端点与直线端点**不会自动重合** → 轮廓不闭合 →
拉伸**静默返回成功但体积为 0**，不报任何错。

```python
with sketch(model, "Front Plane") as ref:
    sketch_arc(...)        # ← 先画弧
    for a, b in segments:  # ← 后画直线
        sketch_line(...)
```

同一条轮廓，只调换画法：体积 `0` vs `38267.7 mm³`。

### 7. `CreateArc` 的弧段范围本身也不可控
多段弧/需要修剪的轮廓，接口会自己重算圆心半径，而且不报错。
**脚本只画整圆、直线、中心线、关系和尺寸**；需要精确弧段/修剪的轮廓交给人手工画。

### 8. 想绕开圆弧：用整圆做并集
需要圆头/轴套时，可以**主体轮廓用直线封顶 + 单独一个整圆做凸台**，
两者并集后的外形与"直线+半圆"完全一致，且完全不碰 `CreateArc`。

---

## 四、特征与 API 调用

### 9. `EditRebuild3` 在 pywin32 里是属性，不是方法
```python
model.EditRebuild3()   # TypeError: 'bool' object is not callable
model.ForceRebuild3(True)   # ✅ 用这个
```

### 10. `ForceRebuild3` 会让已拿到的 `IEdge` 指针失效
**顺序必须是：先重建取数 → 再枚举边 → 立刻选取 → 立刻做特征。**
中间只要再插一次 `ForceRebuild3`，之前枚举到的边对象就废了，
`Select2` 要么静默失败（选择集为空），要么直接抛
`-2147417848 被调用的对象已与其客户端断开连接`。

```python
before = volume(model)          # 重建放最前面
edges  = iter_model_edges(model)
model.ClearSelection2(True)
for e in picked: e["edge"].Select2(True, 0)   # 中间不要再重建
model.FeatureManager.FeatureFillet(195, mm(r), 0, 0)
```

### 11. `IEdge::GetLength` 返回 0
按长度筛边会全灭。改用 `GetStartVertex/GetEndVertex` 的 `GetPoint` 自己算距离，
或者按**方向 + 中点坐标**筛（更稳，且不受长度字段影响）。

### 12. 按点选实体（`SelectByID2("", "ARC", x,y,z, ...)`）不可信
它不按类型过滤，取容差内最近的实体。一律用创建时返回的对象 `seg.Select2`。

### 13. `extrude_cut` 可能返回 `None` 但特征其实成功了
判定必须看特征树 / 重建 / 预览，**不能只看返回值**。

### 14. 中面切除没有现成封装
`sw_part` 只给了 boss 版中面拉伸。切除要自己调：
```python
model.FeatureManager.FeatureCut4(
    True, False, True, 6, 0, depth, 0,          # 6 = swEndCondMidPlane
    False, False, False, False, 0.0, 0.0,
    False, False, False, False, False,
    True, True, False, False, False, 0, 0, False, False)
```

---

## 五、验证（这一节最重要）

### 15. ⚠️ 一条圆角命令里同时选"一凸一凹"两条边，体积会正负抵消
凸边圆角**减**材料，凹边圆角**加**材料。如果两条边对称，增量正好抵消，
于是"体积没变"这个判据会**误报"特征没生效"**——而特征其实完全成功了。

> 真实教训：把拱空左端(凸)和右端(凹)放在同一次圆角里，`d = +0.0`，
> 于是向用户报告"这个圆角没做进去，需要你手工补"。分开做之后：
> 左端 −53.7、右端 +53.7，两个都生效。**白报了一个缺陷。**

**规则：凸边和凹边分开两次做，各自打印体积增量。**

### 16. 每步都用质量属性核对，并且和手算对
`IModelDocExtension.CreateMassProperty` 是可靠的。
**pywin32 下体积字段是 `volume_mm3`**（不是 `volume_m3`），单位是 mm³。

```python
collect_mass_properties(model)["volume_mm3"]
collect_bounding_box(model)["size_mm"]
```

好的做法是**每一步都算一个手算期望值**，逐条对上：

| 特征 | 实测增量 | 手算 |
|---|---|---|
| 矩形槽 15×20 切穿 10 厚 | −3000.0 | 15×20×10 ✓ |
| Ø7 孔穿过 32.5 mm 材料 | −1250.7 | 38.48×32.5 ✓ |
| 四角 R10 | −858.4 | 4×(100−78.54)×10 ✓ |

对不上就是那一步的方向/深度/选边错了，**别往下走**。

### 17. 包围盒对了 ≠ 几何对了
包围盒只约束外切长方体。上面那种逐特征体积核对才有判别力。

### 18. 尺寸链全部闭合 ≠ 图读对了
**同一组尺寸可以安在不同拓扑上。** 竖向 15/10/20/10/35/10=100 加 R5=105 完全自洽，
横向 20+70+10=100 也完全自洽，但中间结构理解成"厚板打孔"还是"双轴套+让位槽"，
两套尺寸都闭合。**判拓扑只能看 3D 渲染/三视图，而且必须放大到能分辨每条线的粗细虚实。**

### 19. 导出 STEP 后特征树是空的
STEP 导入进来是"死实体"，只有 `实体(1)`，没有任何凸台/切除/圆角。
**要给人看特征树、要交作业，必须给原生 `.sldprt`**，STEP 只用于换格式/装配交换。
另外 STEP 体积会比原生差 ~0.0004%（精度损失，正常）。

---

## 六、保存与文件

### 20. `SaveAs` 到"当前会话已打开的同名文档"返回 False 且**不报错**
```python
ok = model.SaveAs(path)
if not ok: ...   # 必须检查
```
最稳的做法：**用带时间戳的唯一文件名存**，然后
`sw.CloseDoc(title)` → 重新 `open` → 复核体积一致，才算交付成功。

> 真实教训：`SaveAs` 静默 False，后面一步 review 打开的是**上一版**的旧文件，
> 差点把旧模型当成交付物发出去。

### 21. pywin32 的 COM 包装对象不能用 `is` 比较身份
`GetDocuments` 每次返回**新的包装对象**，`other is model` 永远 False。
按身份比较去跳过自己 → 结果把正要保存的那个文档自己关掉了。要按**标题**比。

### 22. SolidWorks 会把扩展名写成大写 `.SLDPRT`
`glob("*.sldprt")` 找不到文件，会误判成"保存失败"。列目录或用大小写不敏感匹配。

### 23. 网络盘 / WSL 路径上，SolidWorks 打开着的文件删不掉也覆盖不了
报 `Device or resource busy`（9P 共享 + 文件锁）。
解法：COM `CloseDoc` 关掉，**不要杀进程**，也不要去强关用户正在看的文档。
产物先落**本地 Windows 路径**，再拷进 WSL。

### 24. `sw_review --views` 只认全名
`--views iso,front,...` 里的 `iso` **不匹配** `STANDARD_VIEWS`，会**静默不出等轴测图**
（截出来还是上一个视图）。必须写 `isometric`。
review 只出 BMP，看图要自己转 PNG。

---

## 七、草图完全定义

### 25. `AddDimension2` 会"阻塞"的根因是『输入尺寸值』弹窗 —— 彻底绕开（2026-10-23 修正）
2026-10-20 的结论是"草图编辑态标尺寸会卡死，只能退草图 + SendKeys"。**这个结论错了**：
卡死的不是 API，是创建尺寸时弹出的『修改』对话框在等人工输入值。关掉它就再也不卡：

```python
sw.SetUserPreferenceToggle(10, False)   # 10 = swInputDimValOnCreate
```

关掉之后，草图编辑态连续 `AddDimension2` 标几十个尺寸都不阻塞，
而且尺寸长在**草图里**（不是挂在基准面上）。旧规避办法作废。

### 26. 完全定义可以脚本化：两遍 `FullyDefineSketch` + `AutoDimension2` 兜底（2026-10-23 修正）
上一版说"完全约束基本只能手工做"，已推翻。实测流程（三个零件 14 张草图全部
完全定义，`sw_review` 82 → 92）：

1. 先加**几何关系**：`model.SketchAddConstraints("sgHORIZONTAL"/"sgVERTICAL"/
   "sgCOINCIDENT"/"sgCOLINEAR"/"sgCONCENTRIC"/"sgTANGENT")` —— 字符串名直给；
   **两个草图点**之间也能加水平/垂直（对齐用），**草图点 + 圆弧**能加同心，
   草图点用 `SketchManager.CreatePoint(x, y, 0)` 造。
2. 再加**驱动尺寸**：选实体（原点用
   `SelectByID2("Point1@Origin", "EXTSKETCHPOINT", 0,0,0, ...)`）→ `AddDimension2`。
3. `SketchManager.FullyDefineSketch` **要连续跑两遍**：第一遍补端点合并等关系，
   第二遍才补尺寸。仍欠定义时用 `ISketch::AutoDimension2` 兜底。
4. 判据：`ISketch::GetConstrainedStatus`（2=欠定义，3=完全定义）；
   存盘后要**独立进程**重开回读（见 37/38）。

圆弧圆心在 API 里**选不到**（没有圆心点实体）：定位圆心用
`CreatePoint` 造**构造点 + sgCONCENTRIC[点, 弧]**，点的位置再用尺寸/关系锁——
"到圆心距离"这类尺寸（如凸轮圆心距孔心 15）就是靠这个构造点对标出来的。

---

## 八、其他

### 27. 中文本地化
`SelectByID2("Front","PLANE",...)` 选不中，界面里是 `前视基准面/上视基准面/右视基准面`。
用 skill 的 `PLANE_NAME_ALIASES` 封装，别自己拼。

### 28. 单位
`NewPart` 出来的文档是**米**。必须显式指定国标模板（如 `gb_part.prtdot`）才拿到 mm。
脚本里所有坐标统一过 `mm()`。

### 29. PowerShell / .NET 这条路可能彻底不通
某些安装上任何成员调用都报 `TYPE_E_ELEMENTNOTFOUND (0x8002802B)`，
补类型库也无效。**只用 pywin32**。

### 30. 控制台编码
Windows 控制台是 GBK。Python 输出中文要**直接写 UTF-8 文件**，
别指望在 bash 里 `head`/`tail` 能看。内联 `python -c "..."` 里的中文路径也会被搞坏，
**写成脚本文件更可靠**。

---

## 九、标注实战与存盘复核（2026-10-23 二轮实测）

第二轮：同一课程的压杆（纯回转）、手柄（扁杆 + 回转握把 + 凸轮弧）建模，
并给三个零件 14 张草图补驱动尺寸到完全定义。这一轮把第 25/26 条彻底翻了案，
又撞出一批只有真跑才会撞上的静默失败。

### 31. 两点关系与"点 + 弧"关系都是现成的
`SketchAddConstraints` 吃字符串名：`sgHORIZONTAL / sgVERTICAL / sgCOINCIDENT /
sgCOLINEAR / sgCONCENTRIC / sgTANGENT`。**两个草图点**之间能加水平/垂直（对齐用）；
**草图点 + 圆弧**能加同心。草图点用 `SketchManager.CreatePoint(x, y, 0)` 造。

### 32. 圆弧圆心在 API 里选不到 —— 用"构造点 + 同心"代
界面上能点弧心，COM 里没有这个实体。要标"到圆心距离"这类尺寸：
`CreatePoint` 放在圆心 → `sgCONCENTRIC[点, 弧]` → 点的位置再用尺寸/关系锁。
真实用法：手柄 R12 凸轮圆心在顶面上、距孔心 15 —— 构造点之间那把 "15" 就是这么标的。

### 33. 直径尺寸 = 选径向线 + 轴线构造线
`AddDimension2` 前把"径向线 + 作为回转轴的构造中心线"一起选中，
出来的是带 Ø 的直径尺寸（值自动 ×2）。只选圆时出来的是半径值。

### 34. 孔圆放进轮廓草图，拉伸会把它当内孔直接切掉
带内环的草图拉伸时 SW 把内轮廓当孔。要"单独一步切除"的孔，
把圆画在**独立的切除草图**里（圆心用与原点重合的关系锁）。
真实教训：Ø3.5 孔混在扁杆轮廓里，拉伸体积直接少了孔的 28.86 mm³，
后面那步切除切了个寂寞（增量为 0）。

### 35. 体积 + 包围盒查不出"特征整体平移"
手柄 R12 凸轮弧整体偏了 4 mm（标注基准取错：60/15 都从孔心起标，不是从杆端），
体积、包围盒、reopen 复核**全部一致**——纯平移不改体积。
这类错只能靠图纸像素基准、质心或位置尺寸抓。教训：先量准图纸的 datum 再定坐标。

### 36. `GetCurveParams2` 的排列与"圆心+轴向+半径"不是一回事
SW2025 实测整圆边 `GetCurveParams2` 返回的数组**前三项是圆上一点**、第 8 项是 2π，
技能库按 `params[6]=半径` 的假设取不到直径，`filter_edges(diameter_mm=...)` 全灭。
圆边定位改走 **`ICurve::CircleParams`**（返回 cx,cy,cz, ax,ay,az, r）。
`sw_edge_select.describe_edge` 已按此修复：圆边补充 `circle_center_mm / circle_radius_mm`，
`mid_point_mm` 对圆边取圆心（`position_mm` 筛选从此对圆心生效）。

### 37. 9P 文件系统区分大小写：`SaveAs` 写出大写扩展名 → 目录里两个件
`SaveAs("…\\机架.sldprt")` 实际生成 **`机架.SLDPRT`**，旧的小写文件原样留着
（WSL 挂载区分大小写，是两个真实文件）。更坑的是 **Windows 的
`os.path.exists("…\\机架.sldprt")` 返回 True**（按大小写不敏感解析），
于是"CloseDoc → reopen → 复核"打开的是**旧文件**——体积一模一样、检查全过，
差点把没标注的旧件当成交付。判据四件套：
1. 开建前先关同名文档（同名文档开着时 SaveAs 还可能静默不写盘）；
2. 存盘后看 **mtime** 确认真写盘（`SaveAs` 返回值不可信）；
3. `glob` 按大小写匹配清掉同名不同写法的文件；
4. **独立进程**重开复核（见 38）。

### 38. 同一会话里 `inspect_model_sketches` 可能回读 0 尺寸
刚 SaveAs/重开的文档，`RelationManager` 可能还没加载，回读 dims=0 / 欠定义
**不代表没存进去**（三个零件都被这样误报过）。要**独立进程**重开再读，那才是验收判据。

### 39. 不可见实例（`visible=False`）可以用来做纯查询
用户把 SolidWorks 关了之后，`SolidWorksSession(visible=False)` 冷启动一个无窗口实例
读文件/枚举草图完全可用，不占用户桌面。注意：**不要用它做标注/建模**——
窗口级操作（截屏验收等）需要可见实例；用完记得让它退出，别留下无窗口进程（见 1）。

### 40. "飞线"多数是基准面边框，不是几何
用户报"草图里有一条横贯屏幕的线"：先枚举草图实体排除几何问题
（按端点坐标过滤，超出零件包围盒的就是显示物），再看是不是
基准面以侧边缘对着视角（编辑特征/草图时 SW 会显示基准面）。
显示层问题让用户关"基准面"显示即可，不要去删实体。
