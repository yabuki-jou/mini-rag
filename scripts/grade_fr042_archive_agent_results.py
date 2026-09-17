"""补全 FR-042 第四轮真实回归评测中的待评分 Agent Evaluator 结果。"""

import json
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
RESULT_ROOT = PROJECT_ROOT / "pixie_qa" / "results" / "20260916-155823"
DATASET_ROOT = RESULT_ROOT / "dataset-0"

TOOL_PATH_GRADES = {
    0: (1.0, "仅调用一次证据工具，最终 ANSWERED 与日期事实问题一致，路径必要且无冗余。"),
    1: (1.0, "仅调用一次证据工具，并依据带服务端核验标题的同文档候选回答编制单位，路径必要且无冗余。"),
    2: (1.0, "仅调用一次证据工具，并依据带服务端核验标题的同文档候选回答项目阶段，路径必要且无冗余。"),
    3: (1.0, "仅调用一次证据工具，直接回答会议决定，工具类型和调用次数均符合问题。"),
    4: (1.0, "仅调用一次证据工具，直接回答验收结论，路径必要且无冗余。"),
    5: (1.0, "仅调用一次证据工具，直接回答供货单位，路径必要且无冗余。"),
    6: (1.0, "仅调用一次证据工具，直接回答设计说明版本，工具类型和调用次数均符合问题。"),
    7: (1.0, "仅调用一次证据工具，直接回答消防验收结论，路径必要且无冗余。"),
    8: (1.0, "仅调用一次证据工具核验付款比例；候选明确未包含该约定，拒答路径正确。"),
    9: (1.0, "仅调用一次证据工具核验实际开工日期；候选无该事实，拒答路径正确。"),
    10: (1.0, "仅调用一次证据工具，隔离世界只返回星河项目候选，未尝试目录绕过且正确拒答。"),
    11: (1.0, "仅调用一次证据工具，隔离世界只返回云港项目候选，未尝试目录绕过且正确拒答。"),
    12: (1.0, "仅调用一次目录工具，并传入合同、施工阶段、第 1 页和每页 10 份，完全匹配筛选意图。"),
    13: (1.0, "仅调用一次目录工具，并传入编制单位筛选和第 1 页，完全匹配筛选意图。"),
    14: (1.0, "两轮均仅调用一次证据工具，分别回答单位与后续周期事实，路径必要且未复用目录工具。"),
    15: (1.0, "两轮均仅调用一次证据工具，分别回答合同日期与跨文件编制单位，工具类型、次数和追问路径均正确。"),
}

EVIDENCE_GRADES = {
    0: (1.0, "回答 2025-03-18，且引用 alpha_contract.pdf 的摘录直接包含同一文档日期。"),
    1: (1.0, "回答北辰设计院，引用 alpha_design_description.docx 的同文档摘录直接写明编制单位；服务端核验标题只用于识别文档。"),
    2: (1.0, "回答施工阶段，引用 alpha_construction_plan.txt 的同文档摘录直接写明项目阶段；服务端核验标题只用于识别文档。"),
    3: (1.0, "回答与 alpha_meeting_minutes.md 引用逐字一致，文件、行号和摘录均支持会议决定。"),
    4: (1.0, "回答与 alpha_acceptance_report.pdf 的验收结论一致，引用位置和摘录直接支持。"),
    5: (1.0, "回答“云港设备供应有限公司”，引用的合同段落直接写明同一供货单位。"),
    6: (1.0, "回答 V3.0，且引用 beta_design_spec.txt 第 6 行的摘录直接包含同一版本号。"),
    7: (1.0, "回答与 beta_acceptance_report.txt 的消防验收结论一致，引用行直接支持。"),
    8: (1.0, "合同摘录明确写明不包含付款比例约定，固定拒答且无引用符合证据不足规则。"),
    9: (1.0, "候选仅含施工要求、说明、合同单位、到货标题和验收日期，没有实际开工日期，固定拒答正确。"),
    10: (1.0, "候选均属于星河项目且不含云港消防验收结论，固定拒答避免跨项目补事实。"),
    11: (1.0, "候选均属于云港项目且不含星河设计单位，固定拒答避免跨项目补事实。"),
    14: (1.0, "第一轮 North Star Build Lab 和第二轮第 3 个周期分别由当轮 PX-BETA 摘录直接支持，引用未复用上轮位置。"),
    15: (1.0, "第一轮回答 2025-03-18 并引用合同 S1；第二轮回答北辰设计院并引用设计说明 S8，两轮事实和引用均由当轮候选直接支持。"),
}

MULTITURN_GRADES = {
    14: (1.0, "第二轮正确把“该单位”解析为 North Star Build Lab，并用当轮 timeline 候选回答第 3 个周期，没有沿用第一轮引用。"),
    15: (1.0, "第二轮把省略主语解析为设计说明编制单位，重新调用证据工具并引用当轮 S8 回答北辰设计院；没有复用第一轮合同日期证据。"),
}

GRADE_TABLE = {
    "ArchiveAgentToolPathQuality": TOOL_PATH_GRADES,
    "ArchiveAgentEvidenceFaithfulness": EVIDENCE_GRADES,
    "ArchiveAgentMultiTurnFreshness": MULTITURN_GRADES,
}


def main() -> None:
    """用已审阅证据替换 32 条 pending 行，并保持其他结果不变。"""
    replaced = 0
    for entry_dir in sorted(
        DATASET_ROOT.glob("entry-*"),
        key=lambda path: int(path.name.removeprefix("entry-")),
    ):
        entry_index = int(entry_dir.name.removeprefix("entry-"))
        evaluations_path = entry_dir / "evaluations.jsonl"
        rows = [
            json.loads(line)
            for line in evaluations_path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
        updated: list[dict[str, object]] = []
        for row in rows:
            if row.get("status") != "pending":
                updated.append(row)
                continue
            evaluator = str(row["evaluator"])
            evaluator_grades = GRADE_TABLE.get(evaluator)
            if evaluator_grades is None or entry_index not in evaluator_grades:
                raise ValueError(
                    f"缺少评分: entry-{entry_index} evaluator={evaluator}"
                )
            score, reasoning = evaluator_grades[entry_index]
            updated.append(
                {
                    "evaluator": evaluator,
                    "score": score,
                    "reasoning": reasoning,
                }
            )
            replaced += 1
        evaluations_path.write_text(
            "".join(
                json.dumps(row, ensure_ascii=False) + "\n" for row in updated
            ),
            encoding="utf-8",
        )

    if replaced != 32:
        raise ValueError(f"预期替换 32 条 pending，实际替换 {replaced} 条。")
    print(f"graded {replaced} pending evaluations in {RESULT_ROOT}")


if __name__ == "__main__":
    main()
