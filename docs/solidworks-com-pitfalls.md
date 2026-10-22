# SolidWorks COM 自动化实战踩坑记录

在 **SolidWorks 2025 SP5 + pywin32（Windows 便携 Python 3.12）** 上，用一个真实课程零件
（C 形冲压机架：16 个特征、含回转体轴套、叉口、圆角）反复重建时实测出来的问题。
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

### 25. 草图编辑态下调 `AddDimension2` 会**阻塞整个 SolidWorks**
尺寸属性管理器在等人工确认，之后所有 COM 调用全部卡死。

规避：**先 `InsertSketch2(True)` 退出草图**再标尺寸；每标完一个
`AppActivate "SOLIDWORKS"` + `SendKeys "{ESC}"` + `Sleep 250` 关面板。
代价：尺寸挂在基准面而不是草图里。

### 26. 所以"完全约束"这步基本只能手工做
坐标可以按尺寸链从原点精确给定（几何完全正确），但 SolidWorks 仍判欠定义，
`sw_review` 会报 `sketch_quality_review_required`。
**这一条要如实告诉用户"需要你在界面里手工标"，不要拿"几何是对的"糊过去。**

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
