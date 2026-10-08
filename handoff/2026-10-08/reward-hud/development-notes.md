# 开发记录与未完成的原生验收

## 依赖边界

已实际fetch PR27和证据提交f6c7588c932cd5406a238267783e0723a7cccb16。四份context裁片原字节与来源清单一致，未取得两份完整原PNG，没有新截图、OCR服务或游戏输入。

本环境已有NumPy、Pillow；OpenCV和psutil来自本会话已存在的`page-stop-test-deps`。默认Python和既有目录中未找到RapidOCR／ORT。本次自动审批拒绝以下独立开发依赖安装：

```text
python -m pip install --target /workspace/scratch/455e3b5c6726/pr28-ocr-deps rapidocr-onnxruntime==1.4.4
```

拒绝理由：审批认为授权仅复用已有OCR组件，下载PyPI包及可能的模型依赖未获授权。没有换源、重试同安装或使用在线模型。ROOT已有Windows引擎是后续原生验收端点。

## 聚焦开发检查

- 首版几何／声明OCR的4项通过，0.061秒；完整带与数字行边界补强后，同4项通过，0.058秒。
- 原Worker消费者首版3项通过，16.908秒。将两项拒绝原因断言收紧，避免因其他守卫拒绝而误通过。
- 其中完整链一项随后增强到原`finish_manual_phase`、`explicit_resume`、新epoch、当前`execute_plan`，单项通过，9.465秒。未直接删除manual、写代次或替换关键守卫。
- 最终统一冻结8项：7通过、0failure、1error、0skip，17.440596990秒。唯一error为`importlib.metadata.PackageNotFoundError: rapidocr-onnxruntime`。原生1项保留为error，不skip，不把协议预置的.98当真实OCR。
- 最终33源码／17素材测前后摘要一致，实际加载项目源码闭包无遗漏；`git diff --check`无输出。没有扩跑GUI／Rust／旧冻结类。

完整错误栈、各项时间及来源map保留在同目录`acceptance.json`／`acceptance.tests.txt`。不删除失败记录，不伪填测试HEAD，不宣称Windows通过或候选部署。

## 独立只读审查

首次审查发现高分数字行仅按中心归属、小ROI外相邻数字可能被遗漏。最终已补完整数字行边界与同源129×65币标／数字带否决。最终审查核源码SHA，未发现当前4支持范围内可直接放行低置信、错来源或局部截断金额的新缺口；未运行测试、OCR或修改代码。

几何支持不等于实际OCR通过。控件带止于既有金币HUD右端，更宽溢出、不同字体／尺度和全部多位金额仍未泛化验收。原纯准备复核的全局指纹门槛未放宽，自然页面仍须当前证据。
