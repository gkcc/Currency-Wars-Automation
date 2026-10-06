# B-007：离线测试依赖边界修正

基准为 `4830dfe8510171519b1961b2116968495d51a292`。已完整读取
`origin/main` 的 `8e5bebd5d598cfde0ba2c452e9e9a8a446293606` 中
`handoff/2026-10-07/ROOT_B007_WINDOWS_FEEDBACK.json` 和
`ROOT_B007_BUSINESS_WINDOWS_FEEDBACK.json`。其中 Windows 107 项与 50 项运行的失败及另行真实 installed 检查结果均为本机回报，不是本次云端实测。

本补丁只修既有测试 fixture：

- `RuntimeRootTests` 的两个失败方法将身份替身同时放到公开 adapter 和实际 provider 的模块中，并直接检查创建、子进程保护函数的 `__globals__`。保留真实目录、marker、子进程保护和清理方法；marker 中的身份及 owner/child 查询也有断言。仅替换公开 alias 不再可能掩盖 provider 内部真实依赖。
- 若运行环境实际选中 installed provider，这两个原有方法会分别执行真实 installed provider 和从仓库原源码加载的 standalone provider。若只有 standalone，则执行真实 standalone；不伪造 installed wrapper，也不跳过失败方法。
- 业务原收据归档测试隔离 `input_bridge.runtime_location`，返回自己的临时目录合同；仍核对调用传入生产 `PINNED`、`inherited=None`，以及启动命令准确传递目录合同。该测试继续检查启动前归档、原请求/回执、脱敏和不可变性。实际 loader、目录及 pin/config 拒绝测试未删除或放松。

没有修改生产身份规则、pin、机器安装、输入协议或游戏逻辑。本次游戏输入、截图均为 0。

在本次云端环境，实际选中的 provider 是仓库 **standalone**，无 installed provider。这里的通过结果只证明 standalone 实际函数及其测试依赖边界，**不等于 Windows installed 已通过**。有真实 installed provider 的本机还需重跑相同命令；其真实函数会自动进入上述双 provider 子案例。

## 实际验证

在仓库根目录运行：

```bash
PYTHONPATH=/workspace/scratch/5c64bf8ff52e/test-deps:tools python -B -X utf8 -m unittest -v \
  test_local_runtime_compatibility.RuntimeRootTests.test_runner_load_reuses_authenticated_root_for_normal_and_emergency_channels \
  test_local_runtime_compatibility.RuntimeRootTests.test_worker_marker_child_registration_and_cleanup_keep_selected_root \
  test_currency_wars_business.BusinessTests.test_complete_original_receipt_is_redacted_immutable_and_archived_before_launch
```

结果：**3/3 PASS，0 skipped，0.225 秒**。

```bash
PYTHONPATH=/workspace/scratch/5c64bf8ff52e/test-deps:tools python -B -X utf8 -m unittest \
  test_local_runtime_compatibility.RuntimeRootTests \
  test_currency_wars_business.BusinessTests
```

结果：**22/22 PASS，0 skipped，7.804 秒**（9 个 RuntimeRoot 方法、13 个 Business 方法）。两个修改后的 Python 文件 AST 解析及 `git diff --check` 通过。未运行 Windows 全套、完整 GUI 构建、机器安装或实机自动化，不以此补丁宣称这些验收已完成。

随后按 PR 7、PR 8 原始报告的完整 `test_selection` 重跑，未自行改选或省略方法：

```bash
PYTHONPATH=/workspace/scratch/5c64bf8ff52e/test-deps:tools python -B -X utf8 - <<'PY'
import json
from pathlib import Path
import unittest
for filename in ('B007_RUNTIME_VERIFICATION.json', 'B007_BUSINESS_VERIFICATION.json'):
    data = json.loads((Path('handoff/2026-10-07') / filename).read_bytes())
    suite = unittest.defaultTestLoader.loadTestsFromNames(data['test_selection'])
    print(filename, 'selected', suite.countTestCases(), flush=True)
    result = unittest.TextTestRunner(verbosity=1).run(suite)
    if not result.wasSuccessful():
        raise SystemExit(1)
PY
```

结果分别为 **PR 7 原选择 13/13 PASS，0.054 秒**、**PR 8 原选择 50/50 PASS，12.227 秒**，均 0 failure/error/skip。两份选择及上面的 22 项有重叠，不相加为覆盖数量。未重跑本机回报中的 107 项 Windows 组合。
