import tempfile
import textwrap
import unittest
from pathlib import Path

from elevator_monitor.feature_requirements import (
    load_feature_requirement,
    summarize_feature_requirement,
    validate_feature_requirement,
)


VALID_REQUIREMENT = """\
---
name: alert-batch-export
status: ready
module: api
priority: high
owner: qiudejia
---

# 功能需求单

## 目标
- 为告警提供批量导出能力。

## 用户场景
- 运维人员需要下载最近 24 小时的告警。

## 范围
- 包含：
  - 新增一个导出接口。
- 不包含：
  - 不做权限系统。

## 实现位置
- elevator_monitor/api/routers/workflows.py
- elevator_monitor/api/schemas.py

## 接口与输入输出
- 输入：
  - GET /api/v1/workflows/export-alerts?hours=24
- 输出：
  - 返回 CSV 文本。

## 数据结构
- 无

## 业务规则
- `hours` 默认 24。
- `hours` 最大 168。
- 没有告警时返回只有表头的 CSV。

## 验收标准
- 接口请求成功时返回 200。
- 未传 `hours` 时使用默认值 24。
- `hours` 大于 168 时返回 400。

## 测试用例
- 正常导出最近 24 小时数据。
- 缺省参数走默认值。
- 非法参数返回 400。

## 非目标
- 不接入鉴权。

## 开放问题
- 无
"""


INVALID_REQUIREMENT = """\
---
name: [待填写-功能名]
status: draft
module: [待填写-api|monitor|training|report|new]
priority: medium
---

# 功能需求单

## 目标
- [待填写]

## 范围
- 包含：
  - [待填写]

## 实现位置
- [待填写]

## 接口与输入输出
- 输入：
  - [待填写]

## 业务规则
- [待填写]

## 验收标准
- [待填写]

## 测试用例
- [待填写]
"""


class TestFeatureRequirements(unittest.TestCase):
    def _write_requirement(self, content: str) -> Path:
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        path = Path(temp_dir.name) / "feature_request.md"
        path.write_text(textwrap.dedent(content), encoding="utf-8")
        return path

    def test_load_and_summarize_requirement(self):
        path = self._write_requirement(VALID_REQUIREMENT)
        requirement = load_feature_requirement(path)
        summary = summarize_feature_requirement(requirement)

        self.assertEqual(requirement.metadata["name"], "alert-batch-export")
        self.assertIn("实现位置", requirement.sections)
        self.assertEqual(summary["module"], "api")
        self.assertEqual(
            summary["implementation_targets"],
            [
                "elevator_monitor/api/routers/workflows.py",
                "elevator_monitor/api/schemas.py",
            ],
        )

    def test_validate_requirement_reports_placeholders_and_missing_checks(self):
        path = self._write_requirement(INVALID_REQUIREMENT)
        requirement = load_feature_requirement(path)
        errors = validate_feature_requirement(requirement)

        self.assertIn("metadata.name 未填写", errors)
        self.assertIn("metadata.module 未填写", errors)
        self.assertIn("section.验收标准 至少需要 3 条可验证标准", errors)
        self.assertIn("section.测试用例 至少需要 3 条测试点", errors)

    def test_validate_requirement_accepts_complete_document(self):
        path = self._write_requirement(VALID_REQUIREMENT)
        requirement = load_feature_requirement(path)
        errors = validate_feature_requirement(requirement)

        self.assertEqual(errors, [])


if __name__ == "__main__":
    unittest.main()
