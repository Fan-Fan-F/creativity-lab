"""CLI for agent integration, research runs and blinded evaluations."""
import argparse
import json
import os
from pathlib import Path
import sys
from . import __version__
from .engine import Engine, RunConfig
from .providers import ChatProvider, DemoProvider, ProviderError


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, data):
    output = Path(path)
    if output.exists():
        raise ValueError(f"Output already exists: {output}. Choose a new path.")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
    return str(output)


def provider_factory(demo):
    return DemoProvider if demo else ChatProvider


def judge_factory(demo):
    model = os.getenv("CREATIVITY_JUDGE_MODEL")
    return DemoProvider if demo else (lambda: ChatProvider(model=model)) if model else None


def main(argv=None):
    parser = argparse.ArgumentParser(description="Creativity Lab — search, screen and test ideas")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)
    serve = sub.add_parser("serve", help="Open the local studio")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--open", action="store_true")
    run = sub.add_parser("run", help="Generate and screen candidates")
    run.add_argument("task")
    run.add_argument("--demo", action="store_true")
    run.add_argument("--mode", choices=("lab", "baseline"), default="lab")
    for command in (run,):
        command.add_argument("--rounds", type=int, default=2)
        command.add_argument("--candidates", type=int, default=4)
        command.add_argument("--max-calls", type=int, default=30)
        command.add_argument("--seed", type=int, default=42)
    run.add_argument("--references", help="JSON list of prior solutions/evidence excerpts")
    run.add_argument("--output")
    bench = sub.add_parser("benchmark", help="Compare lab against repeated direct sampling")
    bench.add_argument("--demo", action="store_true")
    bench.add_argument("--tasks", default=str(Path(__file__).resolve().parent / "data" / "tasks.json"))
    bench.add_argument("--rounds", type=int, default=2)
    bench.add_argument("--candidates", type=int, default=4)
    bench.add_argument("--max-calls", type=int, default=30)
    bench.add_argument("--seed", type=int, default=42)
    bench.add_argument("--output", required=True)
    blind = sub.add_parser("blind", help="Make reviewer pack and private source mapping")
    blind.add_argument("benchmark")
    blind.add_argument("--output", required=True)
    blind.add_argument("--mapping", required=True)
    blind.add_argument("--seed", type=int, default=42)
    score = sub.add_parser("score", help="Summarize blinded votes")
    score.add_argument("pack")
    score.add_argument("mapping")
    score.add_argument("votes")
    score.add_argument("--output", required=True)
    template = sub.add_parser("evidence-template")
    template.add_argument("run")
    template.add_argument("--output", required=True)
    attach = sub.add_parser("attach-evidence")
    attach.add_argument("run")
    attach.add_argument("report")
    attach.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "serve":
            from .web import serve_main
            serve_main(port=args.port, open_browser=args.open)
        elif args.command == "run":
            provider = provider_factory(args.demo)()
            jf = judge_factory(args.demo)
            config = RunConfig(task=args.task, rounds=args.rounds, candidates_per_round=args.candidates,
                               max_calls=args.max_calls, seed=args.seed, mode=args.mode,
                               references=read_json(args.references) if args.references else [])
            try:
                result = Engine(provider, jf() if jf else None).run(config)
            except ProviderError as exc:
                partial = getattr(exc, "partial_result", None)
                if not isinstance(partial, dict):
                    raise
                output = args.output or f"runs/{partial['run_id']}.json"
                print(write_json(output, partial))
                print("Creativity Lab: provider failed; partial results and usage preserved", file=sys.stderr)
                return 1
            output = args.output or f"runs/{result['run_id']}.json"
            print(write_json(output, result))
        elif args.command == "benchmark":
            from .evaluation import run_benchmark
            data = run_benchmark(provider_factory(args.demo), judge_factory(args.demo), read_json(args.tasks),
                                 rounds=args.rounds, candidates=args.candidates, max_calls=args.max_calls, seed=args.seed)
            print(write_json(args.output, data))
        elif args.command == "blind":
            from .evaluation import make_pack
            pack, mapping = make_pack(read_json(args.benchmark)["pairs"], seed=args.seed)
            if Path(args.output).resolve() == Path(args.mapping).resolve():
                raise ValueError("Reviewer pack and private mapping require separate paths")
            if Path(args.output).exists() or Path(args.mapping).exists():
                raise ValueError("Choose unused pack and mapping paths")
            print(write_json(args.output, pack))
            print(write_json(args.mapping, mapping))
        elif args.command == "score":
            from .evaluation import summarize_votes
            data = summarize_votes(read_json(args.pack), read_json(args.mapping), read_json(args.votes))
            print(write_json(args.output, data))
        elif args.command == "evidence-template":
            from .validation import evidence_template
            print(write_json(args.output, evidence_template(read_json(args.run))))
        elif args.command == "attach-evidence":
            from .validation import attach_evidence
            print(write_json(args.output, attach_evidence(read_json(args.run), read_json(args.report))))
        return 0
    except (ValueError, OSError, ProviderError, KeyError) as exc:
        print(f"Creativity Lab: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
