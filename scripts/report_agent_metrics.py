"""Generate a shareable metrics report from benchmark evidence, without credentials."""

import argparse
import json
from collections import defaultdict
from pathlib import Path

from benchmark_agent_metrics import dist


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("evidence", type=Path)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    runs = json.loads((args.evidence / "runs.json").read_text(encoding="utf-8"))
    summary = json.loads((args.evidence / "summary.json").read_text(encoding="utf-8"))
    queues, totals, active, first_text, tool_times = [], [], [], [], []
    tools = defaultdict(
        lambda: {"started": 0, "completed": 0, "failed": 0, "durations": []}
    )
    for run in runs:
        events = run["events"]
        queued = next(
            e["created_at"]
            for e in events
            if e["type"] == "run.status" and e["payload_json"]["status"] == "queued"
        )
        started = next(
            e["created_at"]
            for e in events
            if e["type"] == "run.status" and e["payload_json"]["status"] == "running"
        )
        ended = next(e["created_at"] for e in events if e["type"] == "run.completed")
        queues.append(started - queued)
        totals.append(ended - queued)
        active.append(ended - started)
        texts = [
            e["created_at"]
            for e in events
            if e["type"] == "message.delta" and e["payload_json"].get("text")
        ]
        if texts:
            first_text.append(texts[0] - queued)
        pending = defaultdict(list)
        for event in events:
            kind = event["type"]
            if kind not in ("tool.started", "tool.completed", "tool.failed"):
                continue
            name = event["payload_json"]["name"]
            state = kind.split(".")[1]
            tools[name][state] += 1
            if state == "started":
                pending[name].append(event["created_at"])
            elif pending[name]:
                elapsed = event["created_at"] - pending[name].pop(0)
                tools[name]["durations"].append(elapsed)
                tool_times.append(elapsed)
    summary.update(
        queue_s=dist(queues),
        task_event_wall_s=dist(totals),
        task_running_s=dist(active),
        task_first_text_s=dist(first_text),
        tool_latency_s=dist(tool_times),
        tools=dict(tools),
    )
    total_tools = sum(t["started"] for t in tools.values())
    completed_tools = sum(t["completed"] for t in tools.values())
    failed_calls = sum(c["status"] != "succeeded" for r in runs for c in r["calls"])
    summary.update(
        tool_calls=total_tools,
        completed_tool_calls=completed_tools,
        failed_model_attempts=failed_calls,
    )
    (args.evidence / "metrics.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    def pct(a, b):
        return f"{a / b:.2%}" if b else "未观测"

    lines = [
        "# Agent 运行指标基线实测",
        "",
        "日期：2026-11-11。当前工作区代码，真实已授权 DeepSeek 连接；隔离数据目录，串行单用户运行。未修改应用组件或现有业务数据。",
        "",
        f"模型标识：`{summary['model']}`。四种任务各重复三次，共 {len(runs)} 次，每次为新项目。输入包含算术问答、结构化提取、JSON 文件、CSV 汇总及总额文件。",
        "",
        "验收使用实际回答与下载 ZIP 的内容断言：391；指定字段的精确 JSON；配置对象精确匹配；east=35、west=32、total=67。任务成功不只依赖运行状态。",
        "",
        "## 成功率与消耗",
        "",
        "| 指标 | 实测 |",
        "| --- | --- |",
        f"| 任务成功率 | {summary['passed']}/{len(runs)}，{pct(summary['passed'], len(runs))} |",
        f"| 执行完成率 | {summary['completed']}/{len(runs)} |",
        f"| 模型请求次数 | {summary['calls']} |",
        f"| 模型请求失败次数 | {failed_calls} |",
        f"| 工具调用完成率（无异常返回） | {completed_tools}/{total_tools} |",
        f"| 输入 Token | {summary['input_tokens']:,} |",
        f"| 输出 Token | {summary['output_tokens']:,} |",
        f"| 总 Token | {summary['input_tokens'] + summary['output_tokens']:,} |",
        f"| 平均每任务 Token | {(summary['input_tokens'] + summary['output_tokens']) / len(runs):,.1f} |",
        f"| usage 返回覆盖率 | {summary['calls_with_usage']}/{summary['calls']} |",
        f"| 请求级缓存命中率 | {summary['cache_hit_calls']}/{summary['cache_observed_calls']}，{pct(summary['cache_hit_calls'], summary['cache_observed_calls'])} |",
        f"| 缓存读取 Token | {summary['cache_read_tokens']:,} |",
        f"| Token 级缓存读取占比 | {pct(summary['cache_read_tokens'], summary['cache_observed_input_tokens'])} |",
        "",
        "## 延迟",
        "",
        "| 指标（秒） | 样本数 | P50 | P95 | P99 | 最大值 |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for label, key in [
        ("任务入队到结束", "task_event_wall_s"),
        ("排队", "queue_s"),
        ("执行开始到结束", "task_running_s"),
        ("任务首段文本事件", "task_first_text_s"),
        ("模型请求", "model_latency_s"),
        ("模型首个有效输出块", "first_output_s"),
        ("模型首段文本", "first_text_s"),
        ("工具调用", "tool_latency_s"),
    ]:
        d = summary[key]
        if d["n"]:
            lines.append(
                f"| {label} | {d['n']} | {d['p50']:.3f} | {d['p95']:.3f} | {d['p99']:.3f} | {d['max']:.3f} |"
            )
    lines += [
        "",
        "分位数使用 nearest-rank。12 个任务的 P95/P99 都会落在最大值，不能据此推断生产长尾。混合任务总值仅描述本次固定任务组合；分组如下。",
        "",
        "| 任务 | 通过数 | 每次耗时（秒，含 Worker 启停） |",
        "| --- | --- | --- |",
    ]
    for case in summary["by_case"]:
        group = [r for r in runs if r["case"] == case]
        lines.append(
            f"| {case} | {sum(r['passed'] for r in group)}/{len(group)} | "
            + " / ".join(f"{r['harness_wall_s']:.3f}" for r in group)
            + " |"
        )
    lines += [
        "",
        "## 口径与未测项",
        "",
        "- 任务耗时取持久化 queued → run.completed；执行时间不包含排队。本轮没有人工等待。脚本外层耗时另包含 Worker 启停，未混用。",
        "- 模型时间由本地适配器发起一次请求到流结束；首个有效输出块包含文本、推理或工具参数。它是客户端 TTFT 的近似，不是模型服务端逐 Token 时间；纯工具请求不计入首段文本样本。",
        "- 模型调用包括交付规划与实际执行；初始化文本/工具联通探测排除。重试尝试计入总调用数。本轮若无失败尝试，则没有观测到失败重试。",
        "- 缓存使用服务商返回的 prompt_cache_hit_tokens；输入使用 prompt_tokens，命中量已包含其中，不重复相加。没有强制清除或预热服务端缓存，不能解释为冷/热缓存对照实验。",
        "- 工具完成率代表工具包装层未抛出异常，业务正确性由最终产物断言补充；不能推广成所有 MCP 服务的业务成功率。",
        "- 未做并发压测、多专家对照、Office 大文件、故障恢复或预算耗尽测试；不报告它们的成功率与容量。",
        "- 没有已确认的该连接计费单价或账单，因此费用及成本节省不编造。已保留原始 usage，可补价格后计算。",
        "- 这是一轮小样本轻量任务基线，三次重复通过不代表复杂业务任务总体成功率为 100%。",
        "",
        "## 复现与证据",
        "",
        "```powershell",
        "backend/.venv/Scripts/python.exe scripts/benchmark_agent_metrics.py --source-env <已授权配置路径> --output <新隔离目录>",
        "backend/.venv/Scripts/python.exe scripts/report_agent_metrics.py <同一目录> --report docs/validation/agent-metrics-baseline.md",
        "```",
        "",
        f"本次证据目录：`{args.evidence.as_posix()}`。`runs.json` 保存逐任务用量、计时与事件；`summary.json`、`metrics.json` 保存聚合。`private-data` 含连接凭证，只留本地，不提交或分享。",
    ]
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False))


if __name__ == "__main__":
    main()
