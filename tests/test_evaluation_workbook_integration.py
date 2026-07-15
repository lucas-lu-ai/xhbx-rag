from __future__ import annotations

import json
import shutil
from hashlib import sha256
from pathlib import Path

import pytest

from xhbx_rag.evaluation.reporting import safe_backfill
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
        rows.append(
            {
                "Excel行号": excel_row,
                "智能体回答": f"第{excel_row}行智能体回答。",
                "事实正确性得分": 32,
                "关键点覆盖得分": 18,
                "证据忠实性得分": 18,
                "引用及黄金来源命中得分": 13,
                "相关性与表达得分": 9,
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
            "溯源状态分层": {},
            "错误标签频次": {"关键点缺失": 17},
        },
        "逐题结果": rows,
    }


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
    adapter = WorkbookAdapter(
        run_dir,
        env={
            "EVALUATION_NODE_BIN": str(FIXED_NODE_BIN),
            "EVALUATION_ARTIFACT_NODE_MODULES": str(FIXED_NODE_MODULES),
        },
    )

    verification_path = safe_backfill(source, run_dir, adapter, _payload())

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
    rerun_adapter = WorkbookAdapter(
        rerun_dir,
        env={
            "EVALUATION_NODE_BIN": str(FIXED_NODE_BIN),
            "EVALUATION_ARTIFACT_NODE_MODULES": str(FIXED_NODE_MODULES),
        },
    )
    rerun_verification = safe_backfill(
        source,
        rerun_dir,
        rerun_adapter,
        _payload(),
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
    payload_path.write_text(
        json.dumps(payload, ensure_ascii=False),
        encoding="utf-8",
    )
    output = tmp_path / "不应生成.xlsx"
    adapter = WorkbookAdapter(
        tmp_path / "adapter",
        env={
            "EVALUATION_NODE_BIN": str(FIXED_NODE_BIN),
            "EVALUATION_ARTIFACT_NODE_MODULES": str(FIXED_NODE_MODULES),
        },
    )

    with pytest.raises(RuntimeError, match="运行元数据不得包含凭证字段"):
        adapter.backfill(source, payload_path, output)

    assert _sha256(source) == original_hash
    assert not output.exists()
