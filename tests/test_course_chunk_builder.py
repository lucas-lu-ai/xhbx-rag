from xhbx_rag.course_chunk_builder import build_course_chunks
from xhbx_rag.course_enrichment import CourseEnrichment
from xhbx_rag.source_loader import ParsedSourceFile


def _pptx_source(text: str, filename: str = "06促成及异议处理.pptx") -> ParsedSourceFile:
    return ParsedSourceFile(
        source_id="s1",
        source_type="pptx",
        filename=filename,
        source_path=f"新人专属会课程集锦1028/{filename}",
        text=text,
    )


_PPTX_TEXT = "\n\n".join(
    [
        "## 第 1 页\n促成及异议处理\n总公司培训部",
        (
            "## 第 2 页\n"
            "促成的含义及重要性\n"
            "促成：帮助及鼓励客户做出购买决定，达成客户和营销员共赢的过程，"
            "促成很重要，没有促成就没有成交，这一页还补充了促成动作的关键流程，"
            "以及在面谈中如何观察客户的购买信号并及时提出成交请求。\n"
            "### 讲师备注\n"
            "教学时间：1.5分钟\n"
            "教学目标：使营销员明白促成的含义及重要性\n"
            "教学方式：询问、讲授\n"
            "教学流程及要点：\n"
            "询问：你的销售过程中有促成吗？\n"
            "讲授：促成的含义和重要性。\n"
            "辅助资料：投影片"
        ),
    ]
)


def test_pptx_course_chunks_split_by_slide_and_parse_notes() -> None:
    chunks = build_course_chunks(_pptx_source(_PPTX_TEXT))

    overview = chunks[0]
    assert overview.chunk_id.endswith("__overview")
    assert overview.chunk_type == "training_course"
    assert "促成及异议处理" in overview.text

    page_chunks = [chunk for chunk in chunks if not chunk.chunk_id.endswith("__overview")]
    merged = next(chunk for chunk in page_chunks if "促成的含义及重要性" in chunk.text)

    assert merged.chunk_type == "training_course"
    assert "教学流程及要点" in merged.text
    assert "教学时间" not in merged.text
    assert "教学方式：询问" not in merged.text
    assert merged.metadata["teaching_goals"] == ["使营销员明白促成的含义及重要性"]
    assert merged.metadata["course_name"] == "06促成及异议处理"
    assert merged.metadata["course_series"] == "新人专属会课程集锦1028"
    assert merged.metadata["knowledge_type"] == "培训课程"

    citation = merged.citations[0]
    assert citation.locator_confidence == "exact"
    assert citation.locator["slide_start"] >= 1
    assert citation.source_path == "新人专属会课程集锦1028/06促成及异议处理.pptx"


def test_pptx_notes_fields_with_bullet_prefix_are_parsed() -> None:
    text = (
        "## 第 1 页\n"
        "计划100的使用与训练，本页详细讲解名单收集的意义与操作步骤，"
        "帮助新人理解客户开拓的基本方法。\n"
        "### 讲师备注\n"
        "•      教学时间：2分钟\n"
        "•      教学目标：掌握计划100填写方法\n"
        "•      教学方式：讲授\n"
        "•      教学流程及要点：现场进行计划100填写"
    )

    chunks = build_course_chunks(_pptx_source(text))
    page = next(chunk for chunk in chunks if not chunk.chunk_id.endswith("__overview"))

    assert "教学时间" not in page.text
    assert "教学方式" not in page.text
    assert "教学流程及要点：现场进行计划100填写" in page.text
    assert page.metadata["teaching_goals"] == ["掌握计划100填写方法"]


def test_pptx_short_slides_merge_into_following_page() -> None:
    text = "\n\n".join(
        [
            "## 第 1 页\n目录",
            "## 第 2 页\n" + "促成动作详细讲解，" * 30,
        ]
    )

    chunks = build_course_chunks(_pptx_source(text))
    page_chunks = [chunk for chunk in chunks if not chunk.chunk_id.endswith("__overview")]

    assert len(page_chunks) == 1
    assert page_chunks[0].metadata["slide_start"] == 1
    assert page_chunks[0].metadata["slide_end"] == 2


def test_pptx_long_slide_splits_by_paragraph() -> None:
    long_paragraphs = "\n".join(f"第{i}段：" + "促成话术要点，" * 40 for i in range(1, 10))
    text = f"## 第 1 页\n{long_paragraphs}"

    chunks = build_course_chunks(_pptx_source(text))
    page_chunks = [chunk for chunk in chunks if not chunk.chunk_id.endswith("__overview")]

    assert len(page_chunks) > 1
    assert all(chunk.metadata["slide_start"] == 1 for chunk in page_chunks)
    assert len({chunk.chunk_id for chunk in page_chunks}) == len(page_chunks)


def test_docx_course_chunks_split_by_heading() -> None:
    text = "\n\n".join(
        [
            "# 第一章 寿险的意义",
            "寿险保障家庭财务安全，" * 20,
            "# 第二章 销售流程",
            "销售流程包括接触、说明、促成，" * 20,
        ]
    )
    source = ParsedSourceFile(
        source_id="s2",
        source_type="docx",
        filename="寿险销售基础.docx",
        source_path="历史知识梳理/寿险销售基础.docx",
        text=text,
    )

    chunks = build_course_chunks(source)
    section_chunks = [chunk for chunk in chunks if not chunk.chunk_id.endswith("__overview")]

    assert len(section_chunks) == 2
    assert section_chunks[0].metadata["heading"] == "第一章 寿险的意义"
    assert "寿险保障家庭财务安全" in section_chunks[0].text
    assert section_chunks[1].metadata["heading"] == "第二章 销售流程"


def test_docx_without_headings_uses_paragraph_windows() -> None:
    text = "\n\n".join("这是一段没有标题的教材内容，" * 30 for _ in range(6))
    source = ParsedSourceFile(
        source_id="s3",
        source_type="docx",
        filename="教材.docx",
        source_path="历史知识梳理/教材.docx",
        text=text,
    )

    chunks = build_course_chunks(source)
    section_chunks = [chunk for chunk in chunks if not chunk.chunk_id.endswith("__overview")]

    assert len(section_chunks) > 1


def test_pdf_course_chunks_use_page_locator() -> None:
    text = "\n\n".join(
        [
            "## 第 1 页\n" + "养老年金销售逻辑，" * 30,
            "## 第 2 页\n" + "中高端客户经营方法，" * 30,
        ]
    )
    source = ParsedSourceFile(
        source_id="s4",
        source_type="pdf",
        filename="养老销售逻辑.pdf",
        source_path="TOP5000/河北/养老销售逻辑.pdf",
        text=text,
    )

    chunks = build_course_chunks(source)
    page_chunks = [chunk for chunk in chunks if not chunk.chunk_id.endswith("__overview")]

    assert page_chunks[0].metadata["page_start"] == 1
    assert page_chunks[0].citations[0].locator["page_start"] == 1


def test_overview_chunk_includes_enrichment_summary() -> None:
    enrichment = CourseEnrichment(
        summary="本课讲解促成动作与异议处理方法",
        audience="新人",
        sales_stages=("促成",),
    )

    chunks = build_course_chunks(_pptx_source(_PPTX_TEXT), enrichment=enrichment)
    overview = chunks[0]

    assert "本课讲解促成动作与异议处理方法" in overview.text
    assert overview.metadata["summary"] == "本课讲解促成动作与异议处理方法"
    assert overview.metadata["audience"] == "新人"


def test_audience_derived_from_course_series_path() -> None:
    cases = [
        ("第一批整理/新人培训/岗前培训/课件.pptx", "新人"),
        ("第二轮整理/主管培训/课件.pptx", "主管"),
        ("TOP5000/河北/课件.pptx", "绩优"),
        ("第一批整理/讲师/课件.pptx", "讲师"),
        ("第一批整理/产品/课件.pptx", "通用"),
    ]
    for source_path, expected in cases:
        source = ParsedSourceFile(
            source_id="s5",
            source_type="pptx",
            filename="课件.pptx",
            source_path=source_path,
            text="## 第 1 页\n" + "课程内容，" * 40,
        )
        chunks = build_course_chunks(source)
        assert chunks[0].metadata["audience"] == expected, source_path


def test_chunk_ids_are_deterministic() -> None:
    first = build_course_chunks(_pptx_source(_PPTX_TEXT))
    second = build_course_chunks(_pptx_source(_PPTX_TEXT))

    assert [chunk.chunk_id for chunk in first] == [chunk.chunk_id for chunk in second]
