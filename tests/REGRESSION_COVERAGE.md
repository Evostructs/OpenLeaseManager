# 回归测试覆盖说明

这份说明用于避免“测试全绿但不知道测了什么”的问题。下面每条都对应曾经的高风险 finding；如果未来有人把对应修复删掉，相关测试应该失败。

| 风险点 | 防护代码 | 回归测试 |
| --- | --- | --- |
| PDF 下载信任数据库里的 `pdf_path`，可能目录穿越 | `app/services/contracts.py::_safe_upload_path()`、`download_contract_pdf()` | `test_pdf_download_rejects_unsafe_stored_path` |
| PDF 上传一次性读内存且没有大小限制 | `app/services/contracts.py::_write_limited_upload()`、`MAX_PDF_FILE_SIZE`、`MAX_BATCH_PDF_*` | `test_pdf_upload_rejects_oversized_file`、`test_batch_pdf_upload_count_limit_short_circuits_without_writing_files`、`test_batch_pdf_upload_total_size_limit_rolls_back_all_matches` |
| 看板环比同比被已退租等非有效合同污染 | `app/services/contracts.py::_is_currently_active()`、`monthly_snapshot()` | `test_dashboard_stats_exclude_retired_contracts_from_active_metrics` |
| PDF 待处理列表只查前 500 条导致漏报 | `/api/contracts/missing-pdfs` 全量接口 | `test_missing_pdf_endpoint_returns_all_matching_contracts`、`test_missing_pdf_and_integrity_checks_handle_more_than_frontend_page_limit` |
| 批量删除中途失败会部分删除 | `batch_delete_contracts()` 单事务提交 | `test_batch_delete_hides_all_selected_contracts`、`test_batch_delete_rolls_back_when_logging_fails` |
| 任意日期平均租金若直接平均合同单价，会忽略面积和有效天数 | `/api/rent-analytics` 按有效租金合计除以面积日合计 | `test_rent_analytics_returns_area_weighted_daily_segments_and_phases` |
| 免租日若同时删除在租面积，会虚高平均租金和虚低出租面积 | 日级计租保留有效房源面积，免租只把租金降为 0 | `test_rent_analytics_applies_rent_free_terms_without_dropping_area` |
| 已到期合同历史消失、软删除或不可计算合同混入曲线 | 历史按实际有效日保留；软删除排除；无日期、面积或租金依据时给出排除原因 | `test_rent_analytics_keeps_expired_history_and_reports_exclusions` |
| 空置日和有效免租日都显示为 0，导致“无法计算”与“确实免租”混淆 | 前端分析层仅在面积大于 0 时计算日租；免租保留面积，空置返回空值 | `frontend/tests/rentAnalyticsDomain.test.mjs` 的 vacant/rent-free 与 null-gap 用例 |
| 跨今天区间把历史与预测混成一个指标 | 摘要以今天为最后历史日，明天为首个已签预测日 | `frontend/tests/rentAnalyticsDomain.test.mjs` 的 history/forecast split 用例 |
| 前期比较少算一天、闰日错位或长区间仍强画每日刻度 | 日期工具按闭区间计算，跨年日期钳制到有效日，显示粒度随窗口调整 | `frontend/tests/rentAnalyticsDomain.test.mjs` 的 range helpers、granularity 和 comparison 用例 |
| 普通只读用户获得包含合同明细的 Excel | 租金分析页面允许登录用户查看，明细 Excel 仅 `editor/admin`，导出留操作日志 | `test_rent_analytics_excel_requires_editor_and_records_audit` |
| NAS 上的隐式 `docker-compose.override.yml` 固定旧镜像，使新版本和自动回滚都没有真正运行 | `deploy.sh` 只使用仓库内主 Compose，检查镜像名，并在失败后核对实际回滚镜像 ID | `test_deploy_ignores_implicit_overrides_and_verifies_real_rollback` |
| 测试数据互相污染 | `tests/conftest.py::isolated_database()` 每个用例前后清表并清理上传/备份目录 | 全测试套件依赖该 autouse fixture |

注意：测试通过不等于系统没有风险，只说明这些已知风险路径当前被守住。后续如果增加新业务路径，需要同步补对应负向测试。
