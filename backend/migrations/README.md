新增表由 `Base.metadata.create_all` 在启动时创建，已有数据卷会直接补上 `capture_submissions`、`capture_receipts`、`capture_events` 三张表。

升级说明：

- 历史发现保存在 `survey_captures`，首次读取某批次凭证时会一次性补建“回传凭证”（`capture_receipts`，批次号为 `legacy-<campaign>`），补建后原始回传总数与四类数量可对账。
- 历史已生成的补巡单 `coverage_plans.snapshot` 若是旧的 `Capture[]` 列表，计算时会按标签状态映射为计入/待核/无效，结果里仍输出四类汇总；如需按区段看到回传批次与核对轨迹，需重新锁定一次批次。
- `survey_captures.state` 新增合法值 `invalid`（人工判为无效的照片），旧数据无需迁移。
