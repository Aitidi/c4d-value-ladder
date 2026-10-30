# ValueLadder Help

**English** | [简体中文](help.zh-CN.md)

ValueLadder adds Houdini-style ladder scrubbing to numeric fields in Cinema 4D 2026 and 2027 (Windows).

## Contents

- [Getting started](#getting-started)
- [Changing a value](#changing-a-value)
- [Choosing a step](#choosing-a-step)
- [Where you can use it](#where-you-can-use-it)
- [Undo and cancel](#undo-and-cancel)
- [Settings](#settings)
- [Troubleshooting](#troubleshooting)
- [Updating and uninstalling](#updating-and-uninstalling)

## Getting started

1. Quit Cinema 4D.
2. Copy the `ValueLadder` folder from `ValueLadder-x.y.z.zip` into the plugins folder of your Cinema 4D version:
   - 2027: `%APPDATA%\Maxon\Maxon Cinema 4D 2027_XXXXXXXX\plugins\`
   - 2026: `%APPDATA%\Maxon\Maxon Cinema 4D 2026_XXXXXXXX\plugins\`

   Tip: in Cinema 4D, **Edit → Preferences → Open Preferences Folder...** opens the right `XXXXXXXX` folder.
3. Start Cinema 4D.
4. Open **Extensions → ValueLadder Settings...** and tick **Enabled now (this session)** and **Enable at startup**.

ValueLadder is ready when **Extensions → ValueLadder** shows a check mark.

## Changing a value

1. Point at a numeric field in the Attribute Manager, e.g. **Position Y** of an object.
2. Press and hold the **middle mouse button**. A ladder of step sizes appears next to the cursor.
3. Move up or down onto the step you want.
4. Move left or right. Every 6 px of travel changes the value by one step: right increases, left decreases.
5. Release the middle mouse button to apply the change.

While you drag:

- The value updates live in the Attribute Manager and the viewport.
- At the edge of the screen the cursor wraps to the other side, so you can keep dragging.
- The value never goes past the parameter's own minimum and maximum.

## Choosing a step

The ladder shows the steps enabled in the settings (by default 1000, 100, 10, 1, 0.1, 0.01 and 0.001). It opens on the step closest to 1.

| Parameter type | Behaviour |
| --- | --- |
| Distances, angles, percentages and other decimals | All enabled steps are shown. Steps are in the units the field displays, e.g. cm or °. |
| Integers (segments, counts) | Steps below 1 are hidden. |
| Time (frames) | The value moves in whole frames. |

## Where you can use it

**Supported**

- Objects, tags (also tag pages shown inside an object, such as Phong Angle), materials and shaders
- User data
- Project settings, render settings and render effects
- Active tool, viewport settings, takes, layers
- XPresso nodes, field layers, selected keyframes
- Node materials, including Redshift, and Scene Nodes input ports, including X/Y/Z vector rows
- The Preferences window

**Not supported**

- Colour fields, gradients, splines, check boxes and drop-down menus
- Node ports that have an incoming connection
- Structure Manager, Picture Viewer and Render Queue
- Fields without a parameter name next to them

ValueLadder recognises a parameter by the name label to the left of the field. If a field has no such label, nothing happens and the status bar explains why.

## Undo and cancel

| Action | Result |
| --- | --- |
| Right-click while holding the middle button | Cancels the drag and restores the original value |
| Ctrl+Z after a drag | Undoes the whole drag in one step |

Project settings such as frame rate are an exception: Cinema 4D does not record them in undo. Right-click still cancels during the drag, but once applied, the change cannot be undone with Ctrl+Z.

## Settings

Open **Extensions → ValueLadder Settings...** or **Edit → Preferences → ValueLadder**.

| Setting | What it does |
| --- | --- |
| **Enabled now (this session)** | Turns ValueLadder on or off until you quit Cinema 4D. **Extensions → ValueLadder** toggles the same switch. |
| **Enable at startup** | Whether ValueLadder is on when Cinema 4D starts. Changing it does not affect the running session. |
| **Ladder steps** | Which steps the ladder shows: 10000, 1000, 100, 10, 1, 0.1, 0.01, 0.001, 0.0001. At least one step stays on. Changes apply to the next drag. |

Tip: disable steps you never use to keep the ladder short.

## Troubleshooting

**Nothing happens when I hold the middle mouse button.**

- Check that **Extensions → ValueLadder** is ticked, or tick **Enabled now (this session)** in the settings.
- Point at the number itself, not at the label or the slider next to it.
- Read the status bar at the bottom of the Cinema 4D window:
  - *"no parameter label found"*: the field has no name label ValueLadder can use.
  - *"label not recognized"*: ValueLadder could not identify the parameter.
  - *"no numeric parameter found under the cursor"*: the parameter is not a supported number type.

**ValueLadder is not in the Extensions menu.**

- Make sure the folder is `plugins\ValueLadder\` with `ValueLadder.pyp` directly inside it, not `plugins\ValueLadder\ValueLadder\`.
- Open **Extensions → Console** and look for a Python error that mentions ValueLadder.

**ValueLadder behaves oddly or reacts twice.**

- Make sure only one copy is installed. Remove older copies from every plugins folder.

**The middle mouse button no longer pans the node editor.**

- This should not happen; panning on the node editor canvas is left alone. If it does, please report it (see below).

**Reporting a problem**

Open an issue at <https://github.com/Aitidi/c4d-value-ladder/issues> with your Cinema 4D version, the parameter you tried and the status-bar text. For a detailed log, set the Windows environment variable `VALUELADDER_DEBUG=1`, restart Cinema 4D, reproduce the problem and attach `%TEMP%\valueladder_debug.log`.

## Updating and uninstalling

- **Update:** quit Cinema 4D, replace the `ValueLadder` folder with the new one and start Cinema 4D. Your settings are kept.
- **Uninstall:** quit Cinema 4D and delete the `plugins\ValueLadder` folder.
