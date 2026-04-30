# 回归测试覆盖说明

这份说明用于避免“测试全绿但不知道测了什么”的问题。下面每条都对应曾经的高风险 finding；如果未来有人把对应修复删掉，相关测试应该失败。

| 风险点 | 防护代码 | 回归测试 |
| --- | --- | --- |
| PDF 下载信任数据库里的 `pdf_path`，可能目录穿越 | `app/services/contracts.py::_safe_upload_path()`、`download_contract_pdf()` | `test_pdf_download_rejects_unsafe_stored_path` |
| PDF 上传一次性读内存且没有大小限制 | `app/services/contracts.py::_write_limited_upload()`、`MAX_PDF_FILE_SIZE`、`MAX_BATCH_PDF_*` | `test_pdf_upload_rejects_oversized_file`、`test_batch_pdf_upload_count_limit_short_circuits_without_writing_files`、`test_batch_pdf_upload_total_size_limit_rolls_back_all_matches` |
| 看板环比同比被已退租等非有效合同污染 | `app/services/contracts.py::_is_currently_active()`、`monthly_snapshot()` | `test_dashboard_stats_exclude_retired_contracts_from_active_metrics` |
| PDF 待处理列表只查前 500 条导致漏报 | `/api/contracts/missing-pdfs` 全量接口 | `test_missing_pdf_endpoint_returns_all_matching_contracts`、`test_missing_pdf_and_integrity_checks_handle_more_than_frontend_page_limit` |
| 批量删除中途失败会部分删除 | `batch_delete_contracts()` 单事务提交 | `test_batch_delete_hides_all_selected_contracts`、`test_batch_delete_rolls_back_when_logging_fails` |
| 测试数据互相污染 | `tests/conftest.py::isolated_database()` 每个用例前后清表并清理上传/备份目录 | 全测试套件依赖该 autouse fixture；完整回归目前为 `28 passed, 1 skipped` |

注意：测试通过不等于系统没有风险，只说明这些已知风险路径当前被守住。后续如果增加新业务路径，需要同步补对应负向测试。
