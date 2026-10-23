# Redshift 相机适配检查

版本：ValueLadder 0.8.12。环境：Cinema 4D 2027.0.0 / Windows，MCP Adapter 1.0.0.26，2026-10-23。

当前实测通过 34 组配置、1,650 个参数案例。其中 1,572 个案例在相应配置下可编辑，其余案例由 C4D 按当前模式禁用。合计覆盖 85 个不同数值分量（67 个 Redshift 专有分量，18 个坐标/冻结变换分量）。

每个可编辑案例检查：探测命中、还原原值、实际拖动写入、单步撤销、重做、取消、传感器预设与隐藏光学状态恢复。另检验多选相机分别恢复原值、两个独立光学变化不能合并为一个目标。测试使用未激活临时文档及相机副本；不向用户场景注入输入。此清单是 SDK 集成验证，未对每个真实数值框执行界面拖动。

复选框、枚举菜单、颜色控件、LUT 列表、文件选择器及曲线不作为数值阶梯目标。被禁用的灰色数值框需先启用所属功能。未公开或当前 C4D 不提供的控件不在本次已验证范围内。

修复包括：通过公开参数接口获取实时数值，排除容器旧值和内部别名；完整写入二维向量并保留另一分量；按已知探测步长和 C4D 文本舍入识别光学联动，并在副本重放验证；视角通过当前视角/焦距比例调整；分别恢复各相机的传感器尺寸、预设及焦距。

## 配置矩阵

| 配置 | 检查分量 | 可编辑分量 |
| --- | ---: | ---: |
| default | 72 | 30 |
| enabled | 72 | 65 |
| still-manual-shutter | 72 | 65 |
| linked-focus-background | 72 | 71 |
| rounded-editor | 72 | 71 |
| projection:Perspective | 72 | 71 |
| projection:Fisheye | 68 | 67 |
| projection:Spherical | 65 | 64 |
| projection:Cylindrical | 68 | 66 |
| projection:Stereo Spherical | 68 | 67 |
| projection:Orthographic | 68 | 67 |
| projection:Left | 68 | 67 |
| projection:Right | 68 | 67 |
| projection:Front | 68 | 67 |
| projection:Back | 68 | 67 |
| projection:Top | 68 | 67 |
| projection:Bottom | 68 | 67 |
| projection:Isometric | 68 | 67 |
| projection:Dimetric | 68 | 67 |
| sensor:Fill - Crop:lock=False | 5 | 5 |
| sensor:Horizontal:lock=False | 5 | 5 |
| sensor:Vertical:lock=False | 5 | 5 |
| sensor:Overscan - Fit:lock=False | 5 | 5 |
| sensor:Square:lock=False | 4 | 4 |
| sensor:Fill - Crop:lock=True | 5 | 5 |
| sensor:Horizontal:lock=True | 5 | 5 |
| sensor:Vertical:lock=True | 5 | 5 |
| sensor:Overscan - Fit:lock=True | 5 | 5 |
| sensor:Square:lock=True | 4 | 4 |
| square-locked | 5 | 5 |
| cylindrical-orthographic | 68 | 66 |
| background:In-Render | 72 | 71 |
| background:Pre-Composite | 72 | 71 |
| background:Post-Composite | 72 | 71 |

## 参数覆盖

“可编辑配置”仅计入本矩阵中的实际可修改案例。

| SDK 参数 | ID | 分量 | 可编辑配置 |
| --- | ---: | --- | ---: |
| RSCAMERAOBJECT_FOCAL_LENGTH | 500 | — | 20 |
| ID_BASEOBJECT_REL_POSITION | 903 | X | 23 |
| ID_BASEOBJECT_REL_POSITION | 903 | Y | 23 |
| ID_BASEOBJECT_REL_POSITION | 903 | Z | 23 |
| ID_BASEOBJECT_REL_ROTATION | 904 | X | 23 |
| ID_BASEOBJECT_REL_ROTATION | 904 | Y | 23 |
| ID_BASEOBJECT_REL_ROTATION | 904 | Z | 23 |
| ID_BASEOBJECT_REL_SCALE | 905 | X | 23 |
| ID_BASEOBJECT_REL_SCALE | 905 | Y | 23 |
| ID_BASEOBJECT_REL_SCALE | 905 | Z | 23 |
| ID_BASEOBJECT_FROZEN_POSITION | 917 | X | 23 |
| ID_BASEOBJECT_FROZEN_POSITION | 917 | Y | 23 |
| ID_BASEOBJECT_FROZEN_POSITION | 917 | Z | 23 |
| ID_BASEOBJECT_FROZEN_ROTATION | 918 | X | 23 |
| ID_BASEOBJECT_FROZEN_ROTATION | 918 | Y | 23 |
| ID_BASEOBJECT_FROZEN_ROTATION | 918 | Z | 23 |
| ID_BASEOBJECT_FROZEN_SCALE | 919 | X | 23 |
| ID_BASEOBJECT_FROZEN_SCALE | 919 | Y | 23 |
| ID_BASEOBJECT_FROZEN_SCALE | 919 | Z | 23 |
| RSCAMERAOBJECT_FOCUS_DISTANCE | 1010 | — | 3 |
| RSCAMERAOBJECT_NEAR_CLIPPING | 1122 | — | 22 |
| RSCAMERAOBJECT_FAR_CLIPPING | 1128 | — | 22 |
| RSCAMERAOBJECT_FNUMBER_VALUE | 1201 | — | 23 |
| RSCAMERAOBJECT_SHUTTER_ANGLE | 1212 | — | 21 |
| RSCAMERAOBJECT_SHUTTER_EFFICIENCY | 1214 | — | 22 |
| RSCAMERAOBJECT_EXPOSURE | 1220 | — | 23 |
| RSCAMERAOBJECT_ISO | 1231 | — | 22 |
| RSCAMERAOBJECT_APERTURE_BLADES | 1301 | — | 22 |
| RSCAMERAOBJECT_APERTURE_ANGLE | 1302 | — | 22 |
| RSCAMERAOBJECT_APERTURE_SPHERICAL_ABBERATION | 1303 | — | 22 |
| RSCAMERAOBJECT_APERTURE_ASPECT | 1306 | — | 22 |
| RSCAMERAOBJECT_VIGNETTING | 1321 | — | 23 |
| RSCAMERAOBJECT_FOV | 4601 | X | 20 |
| RSCAMERAOBJECT_FOV | 4601 | Y | 20 |
| RSCAMERAOBJECT_LAYOUTHELP_DRAW_GRID_CELLS | 5003 | — | 22 |
| RSCAMERAOBJECT_LAYOUTHELP_DRAW_GOLDENSECTION_I | 5005 | — | 22 |
| RSCAMERAOBJECT_LAYOUTHELP_DRAW_CROSSHAIR_SCALE | 5036 | — | 22 |
| RSCAMERAOBJECT_SENSOR_SIZE | 7002 | X | 18 |
| RSCAMERAOBJECT_SENSOR_SIZE | 7002 | Y | 18 |
| RSCAMERAOBJECT_SENSOR_SIZE_FLOAT | 7003 | — | 2 |
| RSCAMERAOBJECT_FISHEYE_SCALE | 7006 | X | 1 |
| RSCAMERAOBJECT_FISHEYE_SCALE | 7006 | Y | 1 |
| RSCAMERAOBJECT_FISHEYE_ANGLE_OF_VIEW | 7007 | — | 1 |
| RSCAMERAOBJECT_CYLINDRICAL_ANGLE_OF_VIEW_X | 7009 | — | 2 |
| RSCAMERAOBJECT_CYLINDRICAL_ANGLE_OF_VIEW_Y | 7010 | — | 1 |
| RSCAMERAOBJECT_CYLINDRICAL_HEIGHT | 7011 | — | 1 |
| RSCAMERAOBJECT_SENSOR_SHIFT | 7012 | X | 18 |
| RSCAMERAOBJECT_SENSOR_SHIFT | 7012 | Y | 18 |
| RSCAMERAOBJECT_STEREO_SPHERICAL_ANGLE_OF_VIEW | 7015 | X | 1 |
| RSCAMERAOBJECT_STEREO_SPHERICAL_ANGLE_OF_VIEW | 7015 | Y | 1 |
| RSCAMERAOBJECT_STEREO_SPHERICAL_SEPARATION | 7018 | — | 1 |
| RSCAMERAOBJECT_TONE_MAPPING_SATURATION | 7020 | — | 22 |
| RSCAMERAOBJECT_ORTHOGRAPHIC_ZOOM_VIRTUAL | 7022 | — | 9 |
| RSCAMERAOBJECT_FOCUS_OFFSET | 7901 | — | 20 |
| RSCAMERAOBJECT_SHUTTER_TIME_RATIO | 8101 | — | 2 |
| RSCAMERAOBJECT_SHUTTER_OFFSET | 8102 | — | 21 |
| RSCAMERAOBJECT_SHUTTER_OFFSET_FRAMES | 8105 | — | 1 |
| RSCAMERAOBJECT_TONE_MAPPING_HIGHLIGHTS | 8302 | — | 22 |
| RSCAMERAOBJECT_TONE_MAPPING_BLACKS | 8304 | — | 22 |
| RSCAMERAOBJECT_TONE_MAPPING_BLACKS_THRESHOLD | 8305 | — | 22 |
| RSCAMERAOBJECT_BLOOM_THRESHOLD | 12101 | — | 22 |
| RSCAMERAOBJECT_BLOOM_SOFTNESS | 12102 | — | 22 |
| RSCAMERAOBJECT_BLOOM_INTENSITY | 12103 | — | 22 |
| RSCAMERAOBJECT_FLARE_THRESHOLD | 12201 | — | 22 |
| RSCAMERAOBJECT_FLARE_SOFTNESS | 12202 | — | 22 |
| RSCAMERAOBJECT_FLARE_CHROMATIC | 12203 | — | 22 |
| RSCAMERAOBJECT_FLARE_SIZE | 12204 | — | 22 |
| RSCAMERAOBJECT_FLARE_HALO | 12205 | — | 22 |
| RSCAMERAOBJECT_FLARE_INTENSITY | 12206 | — | 22 |
| RSCAMERAOBJECT_STREAK_THRESHOLD | 12301 | — | 22 |
| RSCAMERAOBJECT_STREAK_TAIL | 12302 | — | 22 |
| RSCAMERAOBJECT_STREAK_SOFTNESS | 12303 | — | 22 |
| RSCAMERAOBJECT_STREAK_NUMBER | 12304 | — | 22 |
| RSCAMERAOBJECT_STREAK_ANGLE | 12305 | — | 22 |
| RSCAMERAOBJECT_STREAK_INTENSITY | 12306 | — | 22 |
| RSCAMERAOBJECT_LUT_INTENSITY | 12404 | — | 22 |
| RSCAMERAOBJECT_COLOR_CONTROLS_CONTRAST | 12502 | — | 22 |
| RSCAMERAOBJECT_BACKGROUND_OFFSET_X | 13006 | — | 20 |
| RSCAMERAOBJECT_BACKGROUND_OFFSET_Y | 13007 | — | 20 |
| RSCAMERAOBJECT_BACKGROUND_GAMMA | 13008 | — | 20 |
| RSCAMERAOBJECT_BACKGROUND_EXPOSURE | 13009 | — | 20 |
| RSCAMERAOBJECT_BACKGROUND_HUE | 13010 | — | 20 |
| RSCAMERAOBJECT_BACKGROUND_SATURATION | 13011 | — | 20 |
| RSCAMERAOBJECT_DISPLAY_FOCUS_PLANE_OPACITY | 13103 | — | 22 |
| RSCAMERAOBJECT_DISPLAY_RESOLUTION_OPACITY | 13203 | — | 22 |

## 复现

在 C4D 内导入 `tools/test_redshift_camera.py`，对已存在的 Redshift 相机调用 `run_checks(camera)`。不切换当前文档。测试会创建并清理临时背景图片；不访问外部素材。原有 focused probe / project time / overlay 的 56 项回归检查通过。

## 参考

参数 ID 和枚举以当前实例描述为准，并参照 [Maxon Redshift Camera SDK](https://developers.maxon.net/docs/cpp/2026_3_0/page_resource_orscamera.html)、[Object Properties](https://help.maxon.net/c4d/en-us/Content/html/ORSCAMERA-ID_OBJECTPROPERTIES.html) 和 [Background](https://help.maxon.net/c4d/2025/en-us/Content/html/ORSCAMERA-RSCAMERAOBJECT_GROUP_BACKGROUND.html)。具体适配结论来自本机 SDK 回读和测试。


## 0.8.12 关联对焦回归

用户已实测确认 0.8.11 的尺寸、焦距及视角可调整；真实属性管理器操作 Offset 时同时更新 Focus Distance，旧的未激活文档测试未重现此更新。0.8.12 在对象关联/自动对焦生效时将 Focus Distance 作为派生输出排除，保留 Offset 输入，并恢复原始距离缓存。测试明确模拟这次属性管理器缓存变化；不将这部分模拟测试等同于完整的实际 UI 验证。

一次对 Redshift 参数描述更新消息的独立实验触发 C4D 应用错误，已按用户授权重启。插件代码不调用该消息；后续验证不再发送此类消息。重启后的新相机默认 Tone Mapping 关闭，专项测试显式启用后重跑通过；四个默认模式案例不再计为可编辑，因此实际可编辑案例由 1,576 更新为 1,572。

## 界面实测与构建

用户已确认 Size 宽/高、Focal Length 和 Angle of View X/Y 可以调整，并在 0.8.12 的独立测试文档中确认 Offset 正常。其余字段的覆盖结论来自上述 SDK 检查矩阵。

运行 `python tools/build_release.py` 构建安装包，输出至上级 `_deliverables`；验证 ZIP CRC 及每个文件的字节内容，同版本旧包会保留而不覆盖。
