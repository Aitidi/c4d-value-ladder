# ValueLadder

A free, open-source plugin for **Cinema 4D 2027** that brings Houdini-style "ladder" value scrubbing to numeric fields: hold the middle mouse button over a value, pick a step size (0.001 … 1000) on the ladder, and move left or right to change the value.

一个免费开源的 **Cinema 4D 2027** 插件，把 Houdini 的中键"阶梯"调值方式带到 C4D 的数值框：在数值上按住鼠标中键，在阶梯上选步长（0.001 … 1000），左右移动改变数值。

> Status / 状态: beta (Windows only / 仅支持 Windows)

## Install / 安装

1. Copy `plugin/ValueLadder` into your Cinema 4D plugins folder, e.g.
   `%APPDATA%\Maxon\Maxon Cinema 4D 2027_XXXXXXXX\plugins\ValueLadder`
2. Restart Cinema 4D.

把 `plugin/ValueLadder` 整个文件夹复制到 C4D 的 plugins 目录（如上），然后重启 C4D。

## Use / 用法

| Action / 操作 | Result / 效果 |
| --- | --- |
| Hold **MMB** over a numeric field / 在数值框上按住**中键** | The ladder opens under the pointer. Nothing changes yet. / 阶梯出现在光标处，数值还不会变 |
| Move over the ladder / 光标停在阶梯上 | Picks the step size / 选择步长 |
| Move left or right beside the ladder / 在阶梯旁左右移动 | Changes the value by that step, 6 px per step / 按该步长改值，每 6 像素一步 |
| Reach the screen edge / 拖到屏幕边缘 | The pointer wraps to the other edge and keeps going / 光标从另一侧绕回，继续累加 |
| Release **MMB** / 松开**中键** | Commits. The whole drag is one undo step. / 确认，整个拖动只占一步撤销 |
| **Right-click** while holding / 按住时**右键** | Cancels and restores the value / 取消并恢复原值 |

Integer parameters stop at a step of 1. Time fields move in whole frames.
整数参数最小步长为 1，时间参数按整帧调整。

### Where it works / 支持范围

Attribute Manager fields of selected objects, tags, materials and their shaders, user data, project settings (including Project Scale and the Scene tabs such as Bullet and Simulation), render settings and effects, the active tool, viewport settings, takes, layers, XPresso nodes, field list layers, selected keyframes, node-material ports (Redshift and others) and the Preferences window.

属性管理器中：选中的对象、标签、材质及其着色器、用户数据、工程设置（含工程缩放和 Bullet、模拟等页签）、渲染设置与渲染效果、当前工具、视图设置、Take、图层、XPresso 节点、域列表图层、选中的关键帧、节点材质端口（Redshift 等），以及偏好设置窗口。

Not yet / 暂不支持: colour fields stored only in descriptions, gradient and spline widgets, the Structure Manager, Picture Viewer and Render Queue settings, Scene Nodes graphs. Node-material ports have no range limits yet.

### Settings / 设置

**Edit → Preferences → ValueLadder → Enable at startup** decides whether the ladder is on when Cinema 4D starts. **Extensions → ValueLadder** turns it on or off for the current session.

**编辑 → 偏好设置 → ValueLadder → 启动时启用** 决定启动 C4D 时是否开启；**扩展 → ValueLadder** 可在当前会话中临时开关。

## How it works / 原理

Cinema 4D has no API that tells which parameter is under the cursor. ValueLadder watches the middle button with a low-level mouse hook, recognises edit fields by their theme colour, and, the first time the pointer leaves the ladder, lets the field scrub itself natively by a few pixels. Diffing snapshots of the candidate nodes then names the parameter, and the ladder drives it from there. The field's own scrub records the undo step that holds the original values.

C4D 没有"光标下是哪个参数"的接口。插件用底层鼠标钩子监听中键，按主题颜色识别数值框；光标第一次离开阶梯时，让该数值框原生拖动几个像素，再对比候选节点的快照找出被改动的参数，之后由阶梯接管。原生拖动留下的那一步撤销保存着原值。

## Development / 开发

`tools/dev_reload.py` hot-reloads the plugin into a running Cinema 4D without a restart (do not load it while the installed plugin is active). Set the environment variable `VALUELADDER_DEBUG=1` to log to `%TEMP%\valueladder_debug.log`.

The plugin IDs in `ValueLadder.pyp` are Maxon's test IDs and will be replaced with registered IDs before a 1.0 release.

## License / 许可

[MIT](LICENSE)
