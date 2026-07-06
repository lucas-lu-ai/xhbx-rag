from __future__ import annotations

import os
import json
import stat
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "ingest_cases.sh"


def test_ingest_cases_runs_list_file_entries_in_order(tmp_path: Path) -> None:
    base_dir = tmp_path / "data" / "绩优案例"
    case_names = [
        "【林洁玉】解读“国十条”走进高端",
        "案例 B 含空格",
    ]
    for case_name in case_names:
        (base_dir / case_name).mkdir(parents=True)

    case_list = tmp_path / "cases.txt"
    case_list.write_text(
        "\n# 注释行会被忽略\n" + "\n".join(case_names) + "\n",
        encoding="utf-8",
    )

    log_path = tmp_path / "uv.log"
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_uv = fake_bin / "uv"
    fake_uv.write_text(
        "#!/usr/bin/env python3\n"
        "import json\n"
        "import os\n"
        "import sys\n"
        "\n"
        "with open(os.environ['UV_LOG'], 'a', encoding='utf-8') as handle:\n"
        "    json.dump(sys.argv[1:], handle, ensure_ascii=False)\n"
        "    handle.write('\\n')\n",
        encoding="utf-8",
    )
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IXUSR)

    env = os.environ.copy()
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env["UV_LOG"] = str(log_path)

    result = subprocess.run(
        [str(SCRIPT), "--base-dir", str(base_dir), "--list", str(case_list)],
        cwd=ROOT,
        env=env,
        text=True,
        capture_output=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert [
        json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()
    ] == [
        [
            "run",
            "xhbx-rag",
            "ingest",
            "--case-dir",
            str(base_dir / case_names[0]),
            "--stream",
            "--reuse-section-evidence",
            "--no-thinking",
            "--trace",
        ],
        [
            "run",
            "xhbx-rag",
            "ingest",
            "--case-dir",
            str(base_dir / case_names[1]),
            "--stream",
            "--reuse-section-evidence",
            "--no-thinking",
            "--trace",
        ],
    ]
