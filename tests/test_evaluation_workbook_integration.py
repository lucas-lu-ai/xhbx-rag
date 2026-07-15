from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from xhbx_rag.evaluation.reporting import create_input_snapshot, safe_backfill
from xhbx_rag.evaluation.workbook import WorkbookAdapter


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_WORKBOOK = (
    REPO_ROOT
    / "docs"
    / "新华保险AI教练问答一批绩优案例测试集-含完整溯源.xlsx"
)
FIXED_NODE_BIN = Path(
    "/Users/milan/.cache/codex-runtimes/"
    "codex-primary-runtime/dependencies/node/bin/node"
)
FIXED_NODE_MODULES = Path(
    "/Users/milan/.cache/codex-runtimes/"
    "codex-primary-runtime/dependencies/node/node_modules"
)


def _sha256(path: Path) -> str:
    return sha256(path.read_bytes()).hexdigest()


def _payload() -> dict:
    rows = []
    for excel_row in range(2, 52):
        score = 90 if excel_row % 3 else 70
        grade = "优秀" if score >= 85 else "不合格"
        dimensions = (
            (32, 18, 18, 13, 9)
            if score == 90
            else (25, 15, 12, 10, 8)
        )
        rows.append(
            {
                "Excel行号": excel_row,
                "智能体回答": f"第{excel_row}行智能体回答。",
                "事实正确性得分": dimensions[0],
                "关键点覆盖得分": dimensions[1],
                "证据忠实性得分": dimensions[2],
                "引用及黄金来源命中得分": dimensions[3],
                "相关性与表达得分": dimensions[4],
                "总分": score,
                "评测等级": grade,
                "耗时（秒）": 8.25,
                "主chunk命中": "是",
                "黄金chunk召回率": 0.5,
                "检索chunk_id": "c1；c3",
                "扣分原因": "测试扣分原因。",
                "错误标签": "关键点缺失" if grade == "不合格" else "",
                "改进建议": "补充下一步行动。",
                "评测状态": "已完成",
            }
        )
    return {
        "运行信息": {
            "运行ID": "integration-run",
            "输入SHA256": "a" * 64,
            "Git提交": "abcdef1",
            "问答模型名": "answer-model",
            "裁判模型名": "judge-model",
            "同模型裁判": True,
            "初检候选数": 20,
            "最终证据数": 5,
            "问答并发数": 2,
            "裁判并发数": 2,
            "评分版本": "v1",
            "知识集合统计": {
                "xhbx_sales_chunks": {"存在": True, "数据量": 100}
            },
        },
        "汇总指标": {
            "总题数": 50,
            "平均分": 83.6,
            "保守通过率": 0.66,
            "有效通过率": 0.66,
            "优秀率": 0.66,
            "问答成功率": 1.0,
            "分数P50": 90,
            "分数P95": 90,
            "证据指标": {
                "主chunk命中率": 0.37,
                "平均黄金chunk召回率": 0.42,
                "平均引用及黄金来源命中得分": 11.5,
            },
            "溯源状态分层": {},
            "错误标签频次": {"关键点缺失": 17},
        },
        "逐题结果": rows,
    }


def _adapter(run_dir: Path) -> WorkbookAdapter:
    return WorkbookAdapter(
        run_dir,
        env={
            "EVALUATION_NODE_BIN": str(FIXED_NODE_BIN),
            "EVALUATION_ARTIFACT_NODE_MODULES": str(FIXED_NODE_MODULES),
        },
    )


def _write_payload(path: Path, payload: dict) -> None:
    path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )


@pytest.mark.skipif(
    not REAL_WORKBOOK.is_file()
    or not FIXED_NODE_BIN.is_file()
    or not FIXED_NODE_MODULES.is_dir(),
    reason="缺少真实工作簿或 bundled artifact-tool 运行时",
)
def test_real_workbook_backfill_verifies_preservation_and_renders_five_sheets(
    tmp_path: Path,
) -> None:
    repository_workbook_hash = _sha256(REAL_WORKBOOK)
    source = tmp_path / REAL_WORKBOOK.name
    shutil.copy2(REAL_WORKBOOK, source)
    original_hash = _sha256(source)
    run_dir = tmp_path / "run"
    adapter = _adapter(run_dir)
    lock_root = tmp_path / "locks"
    _, input_sha256 = create_input_snapshot(
        source,
        run_dir,
        lock_root=lock_root,
    )

    verification_path = safe_backfill(
        source,
        run_dir,
        adapter,
        _payload(),
        expected_source_sha256=input_sha256,
        lock_root=lock_root,
    )

    assert source.is_file()
    assert _sha256(source) != original_hash
    assert _sha256(run_dir / "input-backup.xlsx") == original_hash
    verification = json.loads(verification_path.read_text(encoding="utf-8"))
    assert verification == {
        "验证通过": True,
        "检查项": {
            "工作表恰好五个且名称正确": True,
            "主表名称保持不变": True,
            "主表有效行数为51": True,
            "评测表头正确": True,
            "五十条评测状态完整": True,
            "原始主表保持不变": True,
            "溯源明细保持不变": True,
            "五十条评测结果逐格匹配": True,
            "总览证据指标取自汇总": True,
            "低分与错误案例完整": True,
            "公式错误为零": True,
            "五张预览图全部生成": True,
        },
        "主表名称": "绩优案例测试",
        "工作表数量": 5,
        "工作表名称": [
            "绩优案例测试",
            "溯源明细",
            "评测总览",
            "低分与错误案例",
            "运行元数据",
        ],
        "主表行数": 51,
        "评测状态数量": 50,
        "主chunk命中率": 0.37,
        "平均黄金chunk召回率": 0.42,
        "低分与错误案例行号": [
            3,
            6,
            9,
            12,
            15,
            18,
            21,
            24,
            27,
            30,
            33,
            36,
            39,
            42,
            45,
            48,
            51,
        ],
        "首个评测结果差异": None,
        "原始主表保持不变": True,
        "溯源明细保持不变": True,
        "公式错误数量": 0,
        "预览图数量": 5,
        "预览错误": [],
    }
    previews = sorted((run_dir / "预览").glob("*.png"))
    assert [preview.name for preview in previews] == [
        "01-绩优案例测试.png",
        "02-溯源明细.png",
        "03-评测总览.png",
        "04-低分与错误案例.png",
        "05-运行元数据.png",
    ]
    assert all(preview.stat().st_size > 0 for preview in previews)
    assert not (run_dir / "待验证.xlsx").exists()
    assert _sha256(REAL_WORKBOOK) == repository_workbook_hash

    rerun_dir = tmp_path / "rerun"
    rerun_adapter = _adapter(rerun_dir)
    _, rerun_sha256 = create_input_snapshot(
        source,
        rerun_dir,
        lock_root=lock_root,
    )
    rerun_verification = safe_backfill(
        source,
        rerun_dir,
        rerun_adapter,
        _payload(),
        expected_source_sha256=rerun_sha256,
        lock_root=lock_root,
    )
    rerun_result = json.loads(rerun_verification.read_text(encoding="utf-8"))
    assert rerun_result["验证通过"] is True
    assert rerun_result["工作表数量"] == 5
    assert rerun_result["工作表名称"] == verification["工作表名称"]
    assert _sha256(REAL_WORKBOOK) == repository_workbook_hash


@pytest.mark.skipif(
    not REAL_WORKBOOK.is_file()
    or not FIXED_NODE_BIN.is_file()
    or not FIXED_NODE_MODULES.is_dir(),
    reason="缺少真实工作簿或 bundled artifact-tool 运行时",
)
def test_backfill_rejects_nested_credential_metadata_without_touching_source(
    tmp_path: Path,
) -> None:
    source = tmp_path / REAL_WORKBOOK.name
    shutil.copy2(REAL_WORKBOOK, source)
    original_hash = _sha256(source)
    payload = _payload()
    payload["运行信息"]["知识集合统计"]["访问token值"] = "不得落盘"
    payload_path = tmp_path / "恶意回填.json"
    _write_payload(payload_path, payload)
    output = tmp_path / "不应生成.xlsx"
    adapter = _adapter(tmp_path / "adapter")

    with pytest.raises(RuntimeError, match="运行元数据不得包含凭证字段"):
        adapter.backfill(source, payload_path, output)

    assert _sha256(source) == original_hash
    assert not output.exists()


@pytest.mark.skipif(
    not REAL_WORKBOOK.is_file()
    or not FIXED_NODE_BIN.is_file()
    or not FIXED_NODE_MODULES.is_dir(),
    reason="缺少真实工作簿或 bundled artifact-tool 运行时",
)
@pytest.mark.parametrize(
    ("case_name", "field", "value", "expected_message"),
    [
        ("字符串分数", "事实正确性得分", "32", "必须是有限数值"),
        ("事实分越界", "事实正确性得分", 36, "必须在 0 到 35"),
        ("未知等级", "评测等级", "良好", "评测等级无效"),
        ("未知状态", "评测状态", "待处理", "评测状态无效"),
        ("总分不等于五维和", "总分", 89, "总分必须等于五维得分之和"),
        ("阈值等级不一致", "评测等级", "合格", "评测等级与总分不一致"),
        ("负耗时", "耗时（秒）", -0.1, "耗时（秒）必须大于等于 0"),
        ("召回率越界", "黄金chunk召回率", 1.1, "必须在 0 到 1"),
        ("主命中枚举无效", "主chunk命中", "未知", "主chunk命中无效"),
    ],
)
def test_backfill_rejects_noncanonical_completed_result_cells(
    tmp_path: Path,
    case_name: str,
    field: str,
    value: object,
    expected_message: str,
) -> None:
    del case_name
    payload = _payload()
    payload["逐题结果"][0][field] = value
    payload_path = tmp_path / "无效回填.json"
    _write_payload(payload_path, payload)

    with pytest.raises(RuntimeError, match=expected_message):
        _adapter(tmp_path / "adapter").backfill(
            REAL_WORKBOOK,
            payload_path,
            tmp_path / "不应生成.xlsx",
        )


@pytest.mark.skipif(
    not REAL_WORKBOOK.is_file()
    or not FIXED_NODE_BIN.is_file()
    or not FIXED_NODE_MODULES.is_dir(),
    reason="缺少真实工作簿或 bundled artifact-tool 运行时",
)
def test_backfill_rejects_fabricated_failure_scores(tmp_path: Path) -> None:
    payload = _payload()
    row = payload["逐题结果"][0]
    row.update(
        {
            "评测状态": "问答失败",
            "评测等级": "问答失败",
            "事实正确性得分": 1,
            "关键点覆盖得分": None,
            "证据忠实性得分": None,
            "引用及黄金来源命中得分": None,
            "相关性与表达得分": None,
            "总分": 0,
            "主chunk命中": None,
            "黄金chunk召回率": None,
            "检索chunk_id": "",
        }
    )
    payload_path = tmp_path / "伪造失败分数.json"
    _write_payload(payload_path, payload)

    with pytest.raises(RuntimeError, match="问答失败不得包含语义或证据分数"):
        _adapter(tmp_path / "adapter").backfill(
            REAL_WORKBOOK,
            payload_path,
            tmp_path / "不应生成.xlsx",
        )


@pytest.mark.skipif(
    not REAL_WORKBOOK.is_file()
    or not FIXED_NODE_BIN.is_file()
    or not FIXED_NODE_MODULES.is_dir(),
    reason="缺少真实工作簿或 bundled artifact-tool 运行时",
)
def test_backfill_accepts_canonical_answer_and_evaluation_failures(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["逐题结果"][0].update(
        {
            "智能体回答": "",
            "事实正确性得分": None,
            "关键点覆盖得分": None,
            "证据忠实性得分": None,
            "引用及黄金来源命中得分": None,
            "相关性与表达得分": None,
            "总分": 0,
            "评测等级": "问答失败",
            "主chunk命中": None,
            "黄金chunk召回率": None,
            "检索chunk_id": "",
            "评测状态": "问答失败",
        }
    )
    payload["逐题结果"][1].update(
        {
            "事实正确性得分": None,
            "关键点覆盖得分": None,
            "证据忠实性得分": None,
            "引用及黄金来源命中得分": 10,
            "相关性与表达得分": None,
            "总分": None,
            "评测等级": "评测失败",
            "评测状态": "评测失败",
        }
    )
    payload["逐题结果"][2].update(
        {
            "事实正确性得分": None,
            "关键点覆盖得分": None,
            "证据忠实性得分": None,
            "引用及黄金来源命中得分": None,
            "相关性与表达得分": None,
            "总分": None,
            "评测等级": "评测失败",
            "主chunk命中": None,
            "黄金chunk召回率": None,
            "检索chunk_id": "",
            "评测状态": "评测失败",
        }
    )
    payload_path = tmp_path / "规范失败记录.json"
    output = tmp_path / "允许生成.xlsx"
    _write_payload(payload_path, payload)

    _adapter(tmp_path / "adapter").backfill(REAL_WORKBOOK, payload_path, output)

    assert output.is_file()


@pytest.mark.skipif(
    not REAL_WORKBOOK.is_file()
    or not FIXED_NODE_BIN.is_file()
    or not FIXED_NODE_MODULES.is_dir(),
    reason="缺少真实工作簿或 bundled artifact-tool 运行时",
)
@pytest.mark.parametrize(
    ("key", "value"),
    [
        ("privateKey", "不得落盘"),
        ("accessKey", "不得落盘"),
        ("authKey", "不得落盘"),
        ("外部地址", "file:///Users/milan/private.txt"),
        ("编码地址", "file:///%55sers/milan/private.txt"),
        ("凭证地址", "file://user:pass@localhost/tmp/result.json"),
        ("主目录", "/home/operator/private.txt"),
    ],
)
def test_backfill_scans_all_metadata_before_allowlist_and_rejects_secrets(
    tmp_path: Path,
    key: str,
    value: str,
) -> None:
    payload = _payload()
    payload["运行信息"]["不会写入工作簿的扩展字段"] = {key: value}
    payload_path = tmp_path / "危险元数据.json"
    _write_payload(payload_path, payload)

    with pytest.raises(RuntimeError, match="运行元数据不得包含"):
        _adapter(tmp_path / "adapter").backfill(
            REAL_WORKBOOK,
            payload_path,
            tmp_path / "不应生成.xlsx",
        )


@pytest.mark.skipif(
    not REAL_WORKBOOK.is_file()
    or not FIXED_NODE_BIN.is_file()
    or not FIXED_NODE_MODULES.is_dir(),
    reason="缺少真实工作簿或 bundled artifact-tool 运行时",
)
def test_backfill_allows_noncredential_metadata_key_false_positives(
    tmp_path: Path,
) -> None:
    payload = _payload()
    payload["运行信息"]["不会写入工作簿的扩展字段"] = {
        "monkeyCount": 2,
        "keyboardLayout": "中文",
        "hockeyScore": 3,
        "tokenizerMode": "精确",
    }
    payload_path = tmp_path / "安全元数据.json"
    output = tmp_path / "允许生成.xlsx"
    _write_payload(payload_path, payload)

    _adapter(tmp_path / "adapter").backfill(REAL_WORKBOOK, payload_path, output)

    assert output.is_file()


@pytest.mark.skipif(
    not REAL_WORKBOOK.is_file()
    or not FIXED_NODE_BIN.is_file()
    or not FIXED_NODE_MODULES.is_dir(),
    reason="缺少真实工作簿或 bundled artifact-tool 运行时",
)
def test_final_verify_rejects_f_to_u_cell_that_differs_from_snapshot_payload(
    tmp_path: Path,
) -> None:
    adapter = _adapter(tmp_path / "adapter")
    payload = _payload()
    payload_path = tmp_path / "回填.json"
    candidate = tmp_path / "候选.xlsx"
    snapshot = tmp_path / "快照.json"
    _write_payload(payload_path, payload)
    adapter.backfill(REAL_WORKBOOK, payload_path, candidate)
    adapter.extract(REAL_WORKBOOK, snapshot)
    snapshot_payload = json.loads(snapshot.read_text(encoding="utf-8"))
    expected_payload = _payload()
    expected_payload["逐题结果"][0]["智能体回答"] = "与工作簿不同的规范答案"
    snapshot_payload["回填载荷"] = expected_payload
    _write_payload(snapshot, snapshot_payload)

    with pytest.raises(RuntimeError, match="五十条评测结果逐格匹配"):
        adapter.verify(
            candidate,
            snapshot,
            tmp_path / "验证.json",
            tmp_path / "预览",
        )


@pytest.mark.skipif(
    not REAL_WORKBOOK.is_file()
    or not FIXED_NODE_BIN.is_file()
    or not FIXED_NODE_MODULES.is_dir(),
    reason="缺少真实工作簿或 bundled artifact-tool 运行时",
)
def test_low_score_sheet_includes_quality_tags_and_completed_rows_without_evidence(
    tmp_path: Path,
) -> None:
    payload = _payload()
    for row in payload["逐题结果"]:
        row.update(
            {
                "事实正确性得分": 32,
                "关键点覆盖得分": 18,
                "证据忠实性得分": 18,
                "引用及黄金来源命中得分": 13,
                "相关性与表达得分": 9,
                "总分": 90,
                "评测等级": "优秀",
                "错误标签": "",
                "检索chunk_id": "c1",
            }
        )
    for excel_row, tag in ((2, "无依据扩写"), (3, "引用缺失"), (4, "检索未命中")):
        payload["逐题结果"][excel_row - 2]["错误标签"] = tag
    payload["逐题结果"][3]["检索chunk_id"] = ""

    source = tmp_path / REAL_WORKBOOK.name
    shutil.copy2(REAL_WORKBOOK, source)
    run_dir = tmp_path / "run"
    lock_root = tmp_path / "locks"
    _, input_sha256 = create_input_snapshot(
        source,
        run_dir,
        lock_root=lock_root,
    )
    verification_path = safe_backfill(
        source,
        run_dir,
        _adapter(run_dir),
        payload,
        expected_source_sha256=input_sha256,
        lock_root=lock_root,
    )
    verification = json.loads(verification_path.read_text(encoding="utf-8"))

    assert verification["低分与错误案例行号"] == [2, 3, 4, 5]
