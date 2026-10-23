# ValueLadder

A free, open-source plugin for **Cinema 4D 2027** that brings Houdini-style "ladder" value scrubbing to numeric fields: hold the middle mouse button over a value, pick a step size (0.001 … 1000) on the ladder, and move left or right to change the value.

一个免费开源的 **Cinema 4D 2027** 插件，把 Houdini 的中键"阶梯"调值方式带到 C4D 的数值框：在数值上按住鼠标中键，在阶梯上选步长（0.001 … 1000），左右移动改变数值。

> Status / 状态: beta (Windows only / 仅支持 Windows)

> **0.8.12 automatic-focus build / 自动选中版**: hold MMB directly over a numeric field. The plugin clicks it automatically, commits pending text, then probes and restores its value. Legacy simulated mouse-drag discovery remains disabled.
>
> **直接在数值框上按住中键**：插件自动左键选中数值框，确认已有输入，再探测并恢复原值。无需预先手动左键点击；旧的模拟拖动探测继续关闭。

## Install / 安装

1. Copy `plugin/ValueLadder` into your Cinema 4D plugins folder, e.g.
   `%APPDATA%\Maxon\Maxon Cinema 4D 2027_XXXXXXXX\plugins\ValueLadder`
2. Restart Cinema 4D.

把 `plugin/ValueLadder` 整个文件夹复制到 C4D 的 plugins 目录（如上），然后重启 C4D。

## Use / 用法

| Action / 操作 | Result / 效果 |
| --- | --- |
| Hold **MMB** directly over a numeric field / 在数值框上直接按住**中键** | Automatically focuses the field, tries ±0.001, ±1, then native increments, restores the value / 自动选中数值框，依次尝试 ±0.001、±1、原生步进，识别后恢复原值 |
| Move over the ladder / 光标停在阶梯上 | Picks the step size / 选择步长 |
| Move left or right beside the ladder / 在阶梯旁左右移动 | Changes the value by that step, 6 px per step / 按该步长改值，每 6 像素一步 |
| Reach the screen edge / 拖到屏幕边缘 | The pointer wraps to the other edge and keeps going / 光标从另一侧绕回，继续累加 |
| Release **MMB** / 松开**中键** | Commits. The whole drag is one undo step. / 确认，整个拖动只占一步撤销 |
| **Right-click** while holding / 按住时**右键** | Cancels and restores the value / 取消并恢复原值 |

Hold MMB directly over the numeric text. The plugin automatically clicks the field and commits pending edits before recording its baseline. It then appends +0.001 and commits; if no readable value changes, it tries -0.001, +1, -1, then native Up and Down increments. The 0.001 uses the field's displayed numeric units (scale 1 becomes 1.001; angle 40° becomes 40.001°). A successful probe is restored exactly before ladder adjustment. Releasing MMB or changing focus during preparation cancels probing. Legacy simulated mouse-drag discovery remains disabled.

直接在数值文字上按住中键，无需提前左键点击。插件会自动点击数值框、确认已有输入，再记录原值。随后先在数值文字末尾追加 +0.001 并确认，未检测到数值变化时依次尝试 -0.001、+1、-1、原生 ↑、原生 ↓。0.001 按显示数值单位解释，例如缩放 1 → 1.001、角度 40° → 40.001°。识别成功后准确恢复原值，再通过参数接口调整。准备期间松开中键或切换焦点会退出探测；旧的模拟拖动探测继续关闭。

The probe still needs a readable numeric parameter among the supported candidates; it does not make every C4D widget editable. Multiple distinct parameter changes cause cancellation, except for verified project-boundary/preview-range clamps. The small-step probe uses C4D numeric expressions and does not use the clipboard. Focus loss, input errors and ambiguous changes stop the gesture; fallback is only for attempts that yield no readable numeric change. Cancellation restores the numeric values. If C4D does not expose enough information to prove that the newest undo entry belongs to this gesture, an empty native undo entry can remain after cancellation; the plugin does not blindly undo the preceding action.

这种方式仍需从支持的候选项中读到对应数值，并不能让所有自定义控件都变成可调参数。除已验证的工程起止时间与预览范围联动外，多个不同参数同时变化时会恢复已知变化并退出。小步探测使用 C4D 数值表达式，不使用剪贴板；焦点变化、输入错误或多个不同参数同时变化时退出，仅在未检测到数值变化时继续回退。取消会恢复原值；若 C4D 未提供足够信息确认栈顶撤销属于本次手势，取消后可能保留一步空的原生撤销记录，以免误撤销上一次操作。

Integer parameters stop at a step of 1. Time fields move in whole frames.
整数参数最小步长为 1，时间参数按整帧调整。

### Where it works / 支持范围

Attribute Manager fields of selected objects, tags, materials and their shaders, user data, project settings (including Project Scale and the Scene tabs such as Bullet and Simulation), render settings and effects, the active tool, viewport settings, takes, layers, XPresso nodes, field list layers, selected keyframes, node-material ports (Redshift and others), Scene Nodes numeric input ports and the Preferences window.

属性管理器中：选中的对象、标签、材质及其着色器、用户数据、工程设置（含工程缩放和 Bullet、模拟等页签）、渲染设置与渲染效果、当前工具、视图设置、Take、图层、XPresso 节点、域列表图层、选中的关键帧、节点材质端口（Redshift 等）、Scene Nodes 场景节点的数值输入端口，以及偏好设置窗口。

Not yet / 暂不支持: colour fields stored only in descriptions, gradient and spline widgets, the Structure Manager, Picture Viewer and Render Queue settings. For graph ports only directly exposed bounds are read; resource-only limits and display units are not yet read.

Node materials (including Redshift OpenPBR and Ramp) use the same numeric adapter as Scene Nodes: floating-point numbers, integers and vector XYZ components, excluding connected inputs and read-only graphs. Selected graph nodes are discovered even when their material is not selected in the Material Manager. Other materials are searched within a 64-material / 20 ms budget; nested selections use the 512-node budget. Node-material container revision counters are excluded from snapshots, preventing false “multiple parameters changed” cancellations. Colour, ramp/gradient and spline widgets remain unsupported.

节点材质（含 Redshift OpenPBR、Ramp）与场景节点共用数值适配器：支持浮点数、整数和向量 XYZ 分量，跳过已有输入连线及只读图。即使材质管理器没有选中材质，也会查找图内选中的节点；补充查找最多 64 个材质，耗时预算 20 毫秒，组内选择查找上限为 512 个节点。排除材质容器内部的图版本计数，避免误报“多个参数变化”。颜色、渐变色带和样条控件仍不支持。

### Redshift Camera / Redshift 相机

`0.8.11` reads current numeric values through camera descriptions, excluding stale container aliases. Focal length, field of view and sensor-size changes are distinguished using the known probe expression in display units, including C4D's rounded text formatting. A replay on an inactive camera copy validates all linked changes before accepting the target. Unknown or independent simultaneous changes still cancel. Optical restoration retains the sensor preset and hidden sensor dimensions, with separate baselines for multi-selected cameras.

Redshift Camera XY gadgets are written as complete vectors while preserving the other component and unused Z padding. Field-of-view scrubbing drives the focal length through the current optical ratio. Camera integration checks cover all 14 exposed projection modes, five sensor-fit modes with ratio lock on/off, still/movie shutters, linked focus, background images, post effects and coordinates/frozen transforms. The matrix covers 85 numeric components over 34 configurations; it tests identification, restoration, drag, undo/redo and cancellation on disposable copies. This is SDK coverage, not an automated UI drag of every field.

`0.8.11` 按相机描述读取实时数值，排除容器中的旧值及内部别名；结合本次探测的显示单位步长（含 C4D 文本舍入）区分焦距、视角和传感器尺寸，并在未激活的相机副本上重放，核对完整联动后再确认目标。无法区分或存在其他独立变化时仍退出。恢复会保留传感器预设及隐藏尺寸，多选相机分别保存原值。二维控件整向量写入，保留另一分量及未使用的 Z；视角通过当前光学比例换算焦距来调整。

专项矩阵覆盖 14 种投影、5 种传感器适配模式及锁定比例开关、静态/电影快门、关联对焦、背景图片、镜头效果，以及坐标和冻结变换：共 34 组配置、85 个数值分量，检查识别、恢复、拖动、撤销/重做和取消。全部使用临时相机副本；这是 SDK 验证，不代表逐个数值框完成了真实界面拖动。灰色字段需先在 C4D 中启用对应功能；复选框、下拉菜单、颜色块、LUT 列表、曲线和文件选择器不作为阶梯调值目标。

`tools/test_redshift_camera.py`: call `run_checks(existing_rs_camera)` inside C4D; it does not switch or edit the active scene.

Full SDK verification matrix / 完整 SDK 检查清单：[Redshift Camera coverage](docs/redshift-camera-coverage.md)。

### Scene Nodes usage / 场景节点用法

Select the node in the Scene Nodes editor, then hold MMB over a numeric input in the Attribute Manager or the Node Editor's parameter panel. This covers the document's existing Scene Nodes graph and graphs on selected capsules. Selections inside groups are searched within a 512-node budget; at most 30 selected nodes (or root-level nodes when nothing is selected) per graph are scanned. Graph discovery never creates missing graphs. Unset numeric inputs such as Arithmetic's initial zeros are included without initializing the graph.

Supported constants are floating-point numbers, integers and individual XYZ components of 3D vectors. Incoming connections (including vector component connections) and read-only graphs are left untouched. Enums, booleans, colours, matrices as whole values, arrays, geometry and output/data-inspector values are not scrub targets. Nested input bundles are scanned, with a 512-port budget per node. Only bounds exposed directly on the graph port are applied; resource-only bounds and display units are not yet read, so ladder steps use the stored numeric units for these graph ports.

在场景节点编辑器中选中节点，再在**属性管理器或节点编辑器参数面板的数值输入框**上按住中键。支持文档已有的场景节点图，以及选中胶囊对象的节点图；组内选中项的查找上限为 512 个节点，每个图最多扫描 30 个选中节点，未选中时扫描根层节点。检测不会自动创建节点图。Arithmetic 等尚未存值、界面显示初始零值的数值输入也可识别，检测时不会写入默认值。

支持浮点数、整数和三维向量的单个 XYZ 分量。跳过有输入连线的端口（含分量连线）及只读图，不改动连线。枚举、布尔、颜色、整个矩阵、数组、几何体和输出/数据检查器的值暂不作为调值目标。嵌套输入端口每个节点最多检查 512 个。当前只读取端口直接公开的上下限，尚未读取资源中的范围及显示单位；这些图端口的阶梯步长按内部数值单位计算。

### Settings / 设置

**Edit → Preferences → ValueLadder → Enable at startup** decides whether the ladder is on when Cinema 4D starts. **Extensions → ValueLadder** turns it on or off for the current session.

**编辑 → 偏好设置 → ValueLadder → 启动时启用** 决定启动 C4D 时是否开启；**扩展 → ValueLadder** 可在当前会话中临时开关。

## How it works / 原理

Cinema 4D does not expose a general Python API that maps the global mouse position to a parameter. ValueLadder recognises fields by their theme colour. MMB triggers an automatic click in the target field and a Return to commit pending text before snapshots are taken. It then re-enters the numeric editor, tries +0.001, -0.001, +1, -1, native Up, then native Down until a numeric change is identified. It restores the baseline immediately and uses parameter APIs for the rest of the gesture. Native input supplies the undo record; subsequent API writes reuse it.

C4D 没有公开一个通用的 Python 接口，把全局鼠标位置直接转换为参数 ID。插件先按主题颜色识别数值框；按住中键后自动点击目标数值框，按回车确认已有输入，再记录快照。随后重新进入编辑，依次尝试 +0.001、-0.001、+1、-1、原生 ↑、原生 ↓，识别变化后立即恢复原值，之后通过参数接口调整。原生输入建立撤销记录，后续参数写入复用它。

## Development / 开发

`tools/dev_reload.py` hot-reloads the plugin into a running Cinema 4D without a restart. It imports the new code before stopping the old instance, suspends running installed controllers, and starts one test controller driven by the floating **ValueLadder dev** panel. Keep the panel open while testing; **Reload code** loads subsequent edits and **End test** (or closing the panel) stops the test controller and resumes the suspended installed instances. Reloading requires an idle gesture. Registered commands and preferences are still supplied by the installed plugin; changing those definitions requires a restart. Set the environment variable `VALUELADDER_DEBUG=1` to log to `%TEMP%\valueladder_debug.log`.

在 C4D 内运行 `tools/dev_reload.py` 可实时加载代码，无需重启。脚本先导入新代码，再暂停正式版的鼠标与键盘钩子，仅启用一个由 **ValueLadder dev** 面板驱动的测试实例。保持面板打开测试；点击“重新加载代码”可加载后续改动，“结束测试”或关闭面板会停止测试实例并恢复正式版。重载前须松开中键结束手势。正式注册的命令和偏好页定义仍需重启才能更新。

The plugin IDs in `ValueLadder.pyp` are Maxon's test IDs and will be replaced with registered IDs before a 1.0 release.

Run `tools/test_scene_nodes.py` in C4D's Script Manager for SDK integration checks. It uses a disposable document without switching the active document. The installed plugin does not have to be disabled: the checks import the module without starting another mouse hook.

`tools/test_focus_probe.py` checks focused probing, exact restoration, integer/bound handling, ambiguous changes, cancellation and undo safety using disposable documents.

`tools/test_material_nodes.py`: call `run_checks(existing_node_material)` inside C4D to verify discovery, numeric round trips, revision-counter filtering, restoration and undo/redo on disposable material copies. The source material is not modified.

`tools/test_overlay.py` checks hidden/borderless creation, client size, foreground focus, mouse pass-through and resource cleanup. It includes 50 open/refresh/close cycles, 300 consecutive frame updates with source-pixel checks, drawing/presentation failure handling, unchanged-frame caching and exclusion of the old control-paint path. No input is injected and no scene is edited.

`0.8.0` adds the focused native-keyboard probe and retains the original direct-MMB entry. `0.7.0` adds Scene Nodes support, narrows floating Node Editor candidate scans to node ports, and caps viewport redraws at 30 FPS while dragging (with a forced final refresh).

`0.8.2` excludes parameters marked `DESC_HIDE` from numeric snapshots, including derived global/absolute transforms. Editing one visible PSR component therefore no longer produces several false targets. SDK regression checks cover all nine visible PSR components and a child under a rotated/scaled parent. The focused-only restriction introduced in `0.8.1` remains in effect.

`0.8.2` 从快照中排除标为隐藏的参数，包括自动联动的全局、变换后坐标，修复位置/旋转/缩放单个分量被误判成多个参数而退出的问题。SDK 回归覆盖九个 PSR 分量及带旋转、缩放父对象的情况，继续保持旧探测关闭。

`0.8.3` includes tags attached to selected objects even when the tag itself is not selected. This covers object-integrated Attribute Manager tabs such as **Phong → Phong Angle**. At most 200 attached tags are scanned per gesture; explicitly selected tags are deduplicated. SDK regression checks cover discovery, degree conversion and undo/redo.

`0.8.3` 补充扫描选中对象所附的标签，支持从对象属性中的 **Phong → Phong Angle** 等内嵌标签页进入，即使标签本身没有被选中也能识别。每次手势最多扫描 200 个附属标签，并与已选标签去重；SDK 回归覆盖候选发现、角度换算及撤销/重做。

`0.8.4` tries ±0.001 numeric expressions before native keyboard increments. Proven no-op undo entries are removed before retries and graph/object handles are reacquired. Diagnostic traces record the original value, the actual probed value and the displayed delta.

`0.8.4` 优先用 ±0.001 数值表达式探测，无变化时才回退到原生方向键步进。重试前移除能够确认归属的空撤销记录，并重新获取对象/节点图句柄；日志记录原值、实际探测值及显示单位下的差值。

`0.8.5` fixes node-material discovery when the Material Manager has no selected material and filters graph revision counters from classic material snapshots. Material graphs share the bounded, typed numeric adapter with Scene Nodes, including nested selection discovery and graph-specific parameter keys. Verified with disposable copies of a C4D 2027 Redshift OpenPBR/Ramp graph.

`0.8.5` 修复材质未被选中时节点参数漏扫，以及材质内部图版本计数引发的误判；材质图与场景节点共用带扫描上限、保留 SDK 数值类型的适配器，补充组内选择及不同图的参数键隔离。已用 C4D 2027 的 Redshift OpenPBR/Ramp 材质副本验证。

`0.8.6` removes the required prior left click. MMB automatically focuses the numeric field before committing pending text and recording the baseline. Preparation checks cancellation and foreground focus, preserves pointer motion and balances cursor visibility. The old native drag probe remains disabled.

`0.8.6` 去掉预先手动左键点击的要求。直接按住中键会自动选中数值框、确认已有输入，再记录原值并探测；准备阶段检查松键、取消和前台焦点，保留鼠标移动并恢复光标显示，继续禁用旧的原生拖动探测。

`0.8.7` attempted transparent initialization of a C4D async dialog. Post-open opacity/style checks passed, but user testing still exposed frame flashes during initialization. Superseded by the native bitmap popup in `0.8.8`.

`0.8.7` 尝试让 C4D 异步浮窗透明初始化。打开后的透明度、样式检查虽通过，用户实测仍有初始化瞬间闪框，已由 `0.8.8` 的原生位图弹窗取代。

`0.8.8` draws the ladder into a bitmap and displays it in a native `STATIC` popup. It is created without visibility, caption or frame styles; the finished image is installed before showing. There is no C4D async dialog, menu grip, CBT creation hook or Python window procedure. Input probing and parameter adjustment are unchanged. Native integration checks cover the hidden creation state, displayed style/client size, mouse pass-through, focus and resource cleanup; the displayed ladder was also visually inspected.

`0.8.8` 将阶梯绘制为位图，使用原生无边框弹窗显示。创建时就不带标题栏、边框或可见标记，先放入完整图像再显示；不再创建 C4D 异步浮窗，也不使用顶部拖拽条、窗口创建钩子或 Python 窗口过程。参数探测与拖动逻辑沿用原有方案。检查覆盖初始隐藏状态、实际窗口样式与客户区尺寸、鼠标穿透、焦点和资源释放，并核对了实际显示外观。

`0.8.9` replaces `STATIC` bitmap swapping and ordinary repainting with complete off-screen frames submitted through `UpdateLayeredWindow(ULW_OPAQUE)`. The native window shell remains borderless; control background painting and `SetLayeredWindowAttributes` are not used. Unchanged frames are skipped, and a failed draw/presentation leaves the last successful frame intact. This addresses intermittent white flashes while dragging. Native tests check every submitted frame's background/active-rung pixels; a separate timer-driven preview verifies live updates without changing scene parameters.

`0.8.9` 将 `STATIC` 控件换位图、普通重绘的方式改成后台完成整帧后，通过 `UpdateLayeredWindow(ULW_OPAQUE)` 提交显示。窗口继续从创建起就无边框，不再使用控件背景绘制及 `SetLayeredWindowAttributes`。内容不变时跳过刷新，绘制或提交失败时保留上一张成功画面，用于解决拖动时偶发全白闪帧。原生检查验证每张提交画面的背景和当前档位像素，并用独立定时预览核对实时刷新，不改动场景参数。

`0.8.10` compares time snapshots as exact SDK times rather than frame counts, so changing FPS no longer creates false time targets. Probing falls back from ±0.001 to ±1 expressions before trying arrow keys. Project Time, Time Min/Max and Preview Min/Max are covered by SDK checks. Known preview-range clamps from changing project boundaries are attributed to the boundary parameter; restoring or cancelling restores the exact original ranges, including subframes. Unrelated simultaneous changes remain ambiguous. Label/OCR discovery is not implemented.

`0.8.10` 改用 SDK 精确时间做快照，避免 FPS 改变引发帧数换算误判；±0.001 无变化时先尝试 ±1 表达式，再回退方向键。SDK 检查覆盖 Project Time、Time Min/Max、Preview Min/Max。工程起止时间引起的已验证预览范围联动归于起止参数，恢复及取消会还原完整范围，保留原始子帧；其他多参数变化仍退出。尚未实现名称/OCR 定位。

`tools/test_project_time.py` checks FPS/time discovery, linked preview ranges, exact restoration, subframes and cancellation using disposable inactive documents.

## License / 许可

[MIT](LICENSE)

`0.8.12` excludes the derived Focus Distance output from discovery while an object link or automatic focus controls it. Offset remains editable, and restoring it also restores the recorded numeric focus-distance cache. The camera regression test explicitly simulates the native Attribute Manager cache change that inactive SDK-only setters do not reproduce. No Redshift description-update messages are sent.

`0.8.12` 在关联对象或自动对焦控制焦距时，不再把只读的 Focus Distance 输出当成可编辑目标；Offset 仍参与探测，恢复时一并还原记录的距离缓存。回归补充模拟真实属性管理器更新缓存的情况，避免仅用未激活文档的 SetParameter 检查漏掉此路径。插件不发送 Redshift 参数描述更新消息。
