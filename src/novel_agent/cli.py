from __future__ import annotations

import argparse
import asyncio
import json
import webbrowser

from .studio.settings import ROOT


def main():
    parser = argparse.ArgumentParser(
        prog="novel-agent", description="长卷 · 本地小说创作工作台"
    )
    subs = parser.add_subparsers(dest="command")
    serve = subs.add_parser("serve", help="启动创作工作台")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true")
    ev = subs.add_parser("eval", help="运行真实创作与可复现评估")
    ev.add_argument(
        "--mode",
        choices=[
            "suite",
            "sample",
            "baseline",
            "ablations",
            "integration",
            "audit",
            "report",
            "retrieval",
        ],
        default="report",
    )
    ev.add_argument("--chapters", type=int, default=12)
    ev.add_argument("--words", type=int, default=2500)
    ev.add_argument("--url", default="http://127.0.0.1:8765")
    ev.add_argument(
        "--split",
        choices=["dev", "heldout", "all"],
        default="heldout",
        help="独立检索评估分组，不用于旧生成实验",
    )
    ev.add_argument(
        "--dataset",
        choices=["retrieval-v2", "retrieval-novel-v1", "retrieval-novel-v1.1"],
        default="retrieval-v2",
    )
    ev.add_argument(
        "--rerank", action="store_true", help="比较已显式准备的本地重排器，不会下载"
    )
    ev.add_argument(
        "--parallel", type=int, default=8, help="执行图验收并行作品数，最多8"
    )
    subs.add_parser("terminal", help="通过同一服务进行命令行创作")
    mcp = subs.add_parser("mcp", help="通过官方MCP stdio复用本地故事查询与待确认提案")
    mcp.add_argument("--project-id", required=True)
    mcp.add_argument("--before-position", type=int, required=True)
    mcp.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()
    if args.command == "mcp":
        from .studio.story_mcp import StoryMCPConfig, serve as serve_mcp

        asyncio.run(
            serve_mcp(
                StoryMCPConfig(
                    root=ROOT,
                    project_id=args.project_id,
                    before_position=args.before_position,
                    port=args.port,
                )
            )
        )
        return
    if args.command == "eval":
        if args.mode == "retrieval":
            from .studio.retrieval_eval import evaluate_retrieval

            evaluate_retrieval(
                split=args.split, dataset=args.dataset, include_reranker=args.rerank
            )
            return
        from .studio.evaluation import evaluate

        asyncio.run(evaluate(args))
        return
    if args.command == "terminal":
        terminal()
        return
    import uvicorn
    from .studio.api import create_app

    port = getattr(args, "port", 8765)
    url = f"http://127.0.0.1:{port}"
    import httpx

    try:
        alive = httpx.get(
            f"http://127.0.0.1:{port}/api/health", timeout=1, trust_env=False
        )
        if alive.is_success and alive.json().get("version") == "1.0.0":
            print(f"长卷已在运行：http://127.0.0.1:{port}")
            if getattr(args, "open", False):
                webbrowser.open(url)
            return
    except (httpx.HTTPError, ValueError):
        pass
    if getattr(args, "open", False):
        import threading, time

        def open_when_ready():
            with httpx.Client(timeout=1, trust_env=False) as client:
                for _ in range(60):
                    try:
                        ready = client.get(url + "/api/health")
                        if ready.is_success and ready.json().get("version") == "1.0.0":
                            webbrowser.open(url)
                            return
                    except (httpx.HTTPError, ValueError):
                        pass
                    time.sleep(0.2)

        threading.Thread(target=open_when_ready, daemon=True).start()
    uvicorn.run(
        create_app(ROOT),
        host="127.0.0.1",
        port=port,
        log_level="info",
        access_log=False,
    )


def terminal():
    import httpx

    client = httpx.Client(base_url="http://127.0.0.1:8765", timeout=30, trust_env=False)
    pid = None
    print(
        "长卷终端 · 请先启动 novel-agent serve。/start 新建，/new_volume 新卷，/chapter 写章，/resume 查看进度，q 退出。"
    )
    while True:
        command = input("> ").strip()
        if command in ("q", "quit", "exit"):
            break
        try:
            if command == "/start":
                r = client.post(
                    "/api/projects",
                    json={"title": input("书名："), "premise": input("故事方向：")},
                )
                r.raise_for_status()
                pid = r.json()["id"]
                print(
                    client.post(
                        "/api/runs", json={"project_id": pid, "kind": "blueprint"}
                    ).json()
                )
            elif command == "/projects":
                print(client.get("/api/projects").json())
            elif command.startswith("/use "):
                pid = command.split(maxsplit=1)[1]
            elif command in ("/new_volume", "/chapter") and pid:
                p = client.get(f"/api/projects/{pid}").json()
                ch = next(
                    (x for x in p["chapters"] if x["status"] != "final" or x["stale"]),
                    p["chapters"][-1],
                )
                print(
                    client.post(
                        "/api/runs",
                        json={
                            "project_id": pid,
                            "kind": "volume" if command == "/new_volume" else "chapter",
                            "chapter_id": None
                            if command == "/new_volume"
                            else ch["id"],
                        },
                    ).json()
                )
            elif command == "/resume" and pid:
                print(
                    json.dumps(
                        client.get(f"/api/projects/{pid}").json()["runs"],
                        ensure_ascii=False,
                        indent=2,
                    )
                )
            else:
                print(
                    "在浏览器工作台中审阅正文与记忆。/projects 列出作品，/use ID 切换作品。"
                )
        except (httpx.HTTPError, KeyError) as e:
            print("请求失败：", type(e).__name__)


if __name__ == "__main__":
    main()
