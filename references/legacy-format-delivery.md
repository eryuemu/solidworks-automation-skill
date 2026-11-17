# 旧版本格式交付：改引用 / 改组件名 / 保持格式（pilot）

适用场景：交付文件必须是旧格式（本机为 `16000`，SW2023 可打开），同时要修改装配体引用和 FeatureManager 树里的组件名。
下面的事实均在 SW2025 SP5 + pywin32 上实测，坑的原始记录见 `docs/solidworks-com-pitfalls.md` #78–#87。

## 必须先知道的事实

- **普通保存会静默升格式**：打开旧格式文件后 `Save3` 会把磁盘文件写成当前格式（`16000` → `18000`），界面无提示。要求保持旧格式时，改完不能再保存。
- **`ReplaceReferencedDocument` 要求父文档关闭**：父文档打开时调用返回 `False`，不抛异常；关闭后调用成功。它只改引用路径，**不升格式**，但也**不写组件名**。
- **组件名是独立存储的**：系统选项 `swExtRefUpdateCompNames`（本机 toggle 值 `18`）开启时，重开装配体会按新引用名**临时显示**中文，`GetSaveFlag=False` 不能证明名字已存盘；把该选项关掉再开就退回旧名。
- **验收必须在偏好关闭状态做**：否则会被"显示中文"骗过。关闭后重开，`Name2` 仍是中文才算真正写盘。

## 场景 A：源文件已经是旧格式（推荐，最简单）

1. 从旧格式的原始/备份装配体出发，先备份到临时目录。
2. 确认所有相关文档已关闭（`sw.GetDocuments()` 为空，或用 `CloseDoc` 关闭本轮文档）。
3. 逐条改引用：

   ```python
   ok = sw.ReplaceReferencedDocument(asm_path, old_part_path, new_part_path)  # 父文档必须关闭
   assert ok
   ```

4. 从磁盘重新打开核验：格式（`VersionHistory` 末项 `16000[...]`）、引用路径、树名、配合健康、组件变换。
5. 全程不调用 `Save3`。若组件名仍是英文，按场景 C 落盘名字。

## 场景 B：手上只有新格式文件

需要先转出旧格式，再把引用改回目标路径：

1. 用 `IAdvancedSaveAsOptions` 把装配体转成旧格式：

   ```csharp
   // C# 实测签名（见 Save2023.cs）
   var ext = model.Extension;
   var options = (IAdvancedSaveAsOptions)ext.GetAdvancedSaveAsOptions(7);
   options.SaveAllAsCopy = true;
   options.SaveAsPreviousVersion = 16000;   // 数值从 VersionHistory 确认
   object ids, names, paths;
   options.GetItemsNameAndPath(out ids, out names, out paths);
   // names 可能带 " [未打开]" 后缀，先去掉；新路径必须与当前不同
   options.ModifyItemsNameAndPath(ids, oldNames, newPaths);
   ext.SaveAs3(target, 0, 3, null, options, ref errors, ref warnings);
   ```

   pywin32 下 `errors/warnings` 用 `VARIANT(VT_BYREF|VT_I4)`，`ExportData=null` 用空 dispatch VARIANT；`GetItemsNameAndPath` 的 out 参数按类型库声明 marshaling。
   官方要求：`SaveAsPreviousVersion` 必须配合 `ModifyItemsNameAndPath`，且新路径不能与当前相同，否则会因"不允许覆盖同一文件"失败。
2. 关闭文档后，用 `ReplaceReferencedDocument` 把副本引用从输出目录改回目标零件目录（保持 `16000`）。
3. 从磁盘重开核验。

## 场景 C：让中文组件名真正写进文件

`ReplaceReferencedDocument` 的"自动改名"只是显示层。要落盘（并保持旧格式）按这个顺序：

1. 关闭系统选项：`sw.SetUserPreferenceToggle(18, False)`；记下原值，结束时恢复。
2. 打开装配体，逐组件选中并设 `Name2`（传不带实例号的名字）：

   ```python
   comp.Select4(False, model.SelectionManager.CreateSelectData(), False)  # 优先
   # 或 ext.SelectByID2(comp.GetSelectByIDString(), "COMPONENT", 0,0,0,
   #                    False, 0, create_empty_dispatch_variant(), 0)
   comp.Name2 = "小齿轮"      # 读回 "小齿轮-1"；SW2024+ 显示 "小齿轮<1>"
   ```

3. 保存（此步会升到当前格式），再用场景 B 的 `SaveAsPreviousVersion=16000` 转回旧格式。
4. 关闭文档后 `ReplaceReferencedDocument` 改回目标引用（保持 `16000` 和已存名字）。
5. **把 `swExtRefUpdateCompNames` 关掉重开验收**：树名/`Name2` 仍是中文才算成功；然后恢复原设置。

## 核验清单

- `sw.VersionHistory(path)` 末项以 `16000[` 开头（交付要求的旧格式）。
- 组件 `Name2` 在偏好关闭时仍为目标中文名；`GetPathName` 指向交付目录内的零件。
- 配合全部 `healthy`，组件数量与变换未变；零件文件字节未被重写。
- 换目录解压测试：复制整包到另一目录，把副本引用改成不存在的路径后重开；能自动回退到装配体同目录找到零件，才算可移植。
- 只读核验可用 `scripts/sw_version_guard.py --input <文件夹> --expect 16000`。

## 环境注意

- pywin32 成员类型不统一：`GetTitle` 是属性，`GetDocuments`/`VersionHistory` 是方法；统一用 `get_com_member()`。
- Windows 便携 Python 访问不了 `\\wsl.localhost`；从 Git Bash 经 `wsl.exe` 传中文路径会被编码破坏。含中文路径的批量操作用 UTF-8 脚本文件，或直接在 Git Bash 用 `//wsl.localhost/Ubuntu/...`。
- 一个脚本里不要开两个 `SolidWorksSession`；前一个退出会关掉 SW，后一个立即连接会报 `SW_INSTANCE_NOT_READY`。
