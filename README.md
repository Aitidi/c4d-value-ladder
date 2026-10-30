<p align="center">
  <img src="docs/icon/valueladder_512.png" width="96" alt="ValueLadder icon">
</p>

<h1 align="center">ValueLadder</h1>

<p align="center">
  Houdini-style ladder value scrubbing for Cinema 4D<br>
  把 Houdini 的中键阶梯调值带到 Cinema 4D
</p>

<p align="center">
  <img src="docs/media/demo.gif" alt="ValueLadder demo: scrubbing position, rotation, size and segments of a cube">
  <br>
  <a href="docs/media/demo.mp4">Full demo video / 完整演示视频 (MP4)</a>
</p>

Hold the middle mouse button over any numeric field in the Attribute Manager, pick a step size on the ladder, and move left or right to change the value. Release to commit, right-click to cancel.

在属性管理器的任意数值框上按住鼠标中键，在弹出的阶梯上选择步长，左右移动即可改值。松开确认，右键取消。

- **Cinema 4D 2027**, Windows only / 仅支持 Windows
- Free and open source, [MIT](LICENSE) / 免费开源

## Install / 安装

1. Download `ValueLadder-1.0.0.zip` from [Releases](https://github.com/Aitidi/c4d-value-ladder/releases) and quit Cinema 4D.
2. Copy the `ValueLadder` folder from the ZIP into your plugins folder, e.g. `%APPDATA%\Maxon\Maxon Cinema 4D 2027_XXXXXXXX\plugins\ValueLadder`. Replace any older copy; do not keep two.
3. Start Cinema 4D. **Extensions → ValueLadder** shows the plugin.
4. **After the first install, enable the plugin:** open **Extensions → ValueLadder Settings...** (or **Edit → Preferences → ValueLadder**) and tick both **Enabled now (this session)** and **Enable at startup**.

1. 从 [Releases](https://github.com/Aitidi/c4d-value-ladder/releases) 下载 `ValueLadder-1.0.0.zip`，先退出 C4D。
2. 把压缩包中的 `ValueLadder` 文件夹复制到 plugins 目录，例如 `%APPDATA%\Maxon\Maxon Cinema 4D 2027_XXXXXXXX\plugins\ValueLadder`。替换旧版，不要同时保留两份。
3. 启动 C4D，**扩展 → ValueLadder** 中可以看到插件。
4. **初次安装后请先启用插件**：打开 **扩展 → ValueLadder Settings...**（或 **编辑 → 偏好设置 → ValueLadder**），勾选 **当前启用（本次会话）** 和 **启动时启用**。

## Use / 用法

| Action / 操作 | Result / 效果 |
| --- | --- |
| Hold **MMB** over a numeric field / 在数值框上按住**中键** | The ladder opens next to the cursor / 光标旁弹出阶梯 |
| Move up or down the ladder / 在阶梯上移动 | Picks the step: 1000 … 0.001 / 选择步长：1000 … 0.001 |
| Move left or right / 左右移动 | Changes the value by one step every 6 px / 每 6 像素改变一个步长 |
| Reach the screen edge / 移到屏幕边缘 | The pointer wraps around and keeps counting / 光标从另一侧绕回，继续累加 |
| Release **MMB** / 松开**中键** | Commits; the whole drag is one undo step / 确认，整个拖动只占一步撤销 |
| **Right-click** while holding / 按住时**右键** | Cancels and restores the value / 取消并恢复原值 |

- Integer parameters hide the fractional steps; time fields move in whole frames.
- Minimum and maximum limits of the parameter are respected.
- The ladder, its font and the pixels per step follow the monitor's DPI scaling.

- 整数参数自动隐藏小数挡位；时间参数按整帧调整。
- 遵守参数自身的最小值、最大值。
- 阶梯尺寸、字体和每步像素随显示器 DPI 缩放。

## Where it works / 支持范围

**Works on / 支持：** objects, tags (including tags shown inside an object, e.g. Phong Angle), materials and shaders, user data, project settings, render settings and effects, the active tool, viewport settings, takes, layers, XPresso nodes, field layers, selected keyframes, node-material ports (Redshift and others), Scene Nodes input ports (including vector rows such as `Point [x] [y] [z]`) and the Preferences window. Redshift Camera is covered in detail: [Redshift Camera coverage](docs/redshift-camera-coverage.md).

对象、标签（含对象属性中内嵌的标签页，如 Phong 角度）、材质与着色器、用户数据、工程设置、渲染设置与效果、当前工具、视图设置、Take、图层、XPresso 节点、域图层、选中的关键帧、节点材质端口（Redshift 等）、场景节点输入端口（含 `Point [x] [y] [z]` 这类向量行）以及偏好设置窗口。Redshift 相机的详细覆盖见 [Redshift Camera coverage](docs/redshift-camera-coverage.md)。

**Not supported / 不支持：** colour fields, gradients, splines, check boxes and drop-downs; node ports with an incoming connection; the Structure Manager, Picture Viewer and Render Queue. A field without a parameter label next to it shows a status-bar message instead of the ladder.

颜色、渐变、样条、复选框和下拉菜单；已有输入连线的节点端口；结构管理器、图片查看器和渲染队列。旁边没有参数名称标签的数值框不弹出阶梯，只在状态栏提示。

**Undo / 撤销：** object, tag and material changes and node ports are one undo step each. Project settings such as FPS are not covered by Cinema 4D's undo: right-click still restores them during the drag, but a committed change cannot be undone with Ctrl+Z.

对象、标签、材质参数和节点端口每次拖动为一步撤销。帧率等工程设置不在 C4D 的撤销范围内：拖动中右键仍可恢复，但确认后无法用 Ctrl+Z 撤销。

## Settings / 设置

Open **Extensions → ValueLadder Settings...** or **Edit → Preferences → ValueLadder**.

| Setting / 设置 | Meaning / 说明 |
| --- | --- |
| Enabled now (this session) / 当前启用（本次会话） | Turns the ladder on or off until Cinema 4D quits. **Extensions → ValueLadder** does the same. / 仅在本次运行中开关，**扩展 → ValueLadder** 效果相同 |
| Enable at startup / 启动时启用 | Whether the ladder is on when Cinema 4D starts / 启动 C4D 时是否开启 |
| Ladder steps / 阶梯挡位 | Which of 10000 … 0.0001 the ladder shows (default 1000 … 0.001). A gesture starts on the step closest to 1. / 阶梯显示哪些步长（默认 1000 … 0.001），每次从最接近 1 的挡位开始 |

## How it works / 原理

Cinema 4D draws all Attribute Manager fields into a single window and offers no API that maps the mouse position to a parameter. Every parameter label, however, is a drag source that carries the parameter's exact `DescID` and the edited objects. On MMB, ValueLadder finds the field by its theme colour, locates its label, drags that label a few pixels into an invisible receiver window and refuses the drop. This takes about 130 ms and changes nothing. From then on the value is read and written through the regular parameter APIs, for node ports through the graph API.

C4D 把属性管理器的所有控件画在同一个窗口里，也没有把鼠标位置换算成参数的接口。但每个参数的名称标签都是拖放源，携带参数精确的 `DescID` 和正在编辑的对象。按下中键后，插件按主题颜色找到数值框，再找到它的标签，把标签拖动几个像素到一个不可见的接收窗口并拒绝放下。整个过程约 130 毫秒，不修改任何数据。之后通过常规参数接口读写数值，节点端口则通过节点图接口。

## Development / 开发

| File / 文件 | Purpose / 用途 |
| --- | --- |
| `plugin/ValueLadder/ValueLadder.pyp` | The plugin / 插件本体 |
| `tools/dev_reload.py` | Hot-reloads the plugin into a running Cinema 4D; a floating **ValueLadder dev** panel controls the test instance / 在运行中的 C4D 内热加载，由 **ValueLadder dev** 面板控制测试实例 |
| `tools/build_release.py` | Builds and verifies the release ZIP / 生成并校验发布包 |
| `tools/check_api_compat.py` | Run with a build's `c4dpy` to check that it has every `c4d` / `maxon` name the plugin uses / 用某版本的 `c4dpy` 运行，检查插件用到的接口是否都存在 |
| `tools/test_*.py` | SDK checks run inside Cinema 4D on disposable documents and copies / 在 C4D 内用临时文档和副本运行的检查 |

Set the environment variable `VALUELADDER_DEBUG=1` to log to `%TEMP%\valueladder_debug.log`.

设置环境变量 `VALUELADDER_DEBUG=1` 可把日志写入 `%TEMP%\valueladder_debug.log`。

Plugin IDs 1072634, 1072637, 1072639 and 1072640 are registered with Maxon.

插件 ID 1072634、1072637、1072639、1072640 均由 Maxon 正式分配。

## Credits / 致谢

The icon is drawn after the [Boghma Icon Template for Cinema 4D Plugin](https://www.figma.com/community/file/1653314126929745043/boghma-icon-template-for-cinema-4d-plugin) by DunHouGo (CC BY 4.0). Source: `docs/icon/valueladder.svg`.

图标参照 DunHouGo 的 [Boghma Icon Template for Cinema 4D Plugin](https://www.figma.com/community/file/1653314126929745043/boghma-icon-template-for-cinema-4d-plugin)（CC BY 4.0）绘制，源文件为 `docs/icon/valueladder.svg`。

## License / 许可

[MIT](LICENSE)
