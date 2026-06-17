#!/usr/bin/env python3
"""Seed the Athena memory vault with an interlinked synthetic wiki (Phase 18).

Generates one Obsidian-style markdown note per topic / project / skill / concept,
densely cross-linked with [[wikilinks]] in Andrej Karpathy's "LLM Wiki" style,
then regenerates `_index.md`. Notes use the real `agent/memory.py` writer, so the
on-disk format is identical to what reflection produces.

`source` is "explicit" for personal-fact / profile notes (things Varun would have
told the agent) and "auto" for synthesized concept / entity / tech pages (the kind
background reflection authors) — so the /memory source badge shows a realistic mix.

Usage:
    python scripts/seed_memory.py [output_dir]     # default: ./seed-vault

Then load it into the cluster (see the Phase 18 handoff): copy the *.md files into
the agent pod's /data/memory PVC, e.g.
    kubectl cp ./seed-vault <agent-pod>:/data/memory -n athena
"""
import os
import sys

OUT = sys.argv[1] if len(sys.argv) > 1 else os.path.join(os.path.dirname(__file__), "seed-vault")
os.environ["MEMORY_DIR"] = os.path.abspath(OUT)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "agent"))

import memory  # noqa: E402  — imported AFTER MEMORY_DIR is set (read at import time)


# (title, content, tags, source, events)
NOTES: list[dict] = [
    # ---- Person / hub ----------------------------------------------------
    dict(
        title="Varun",
        source="explicit",
        tags=["person", "profile"],
        events=[{"date": "2028-05-15", "kind": "graduation"}],
        content=(
            "Computer Engineering student at [[University of Maryland]] (GPA 3.9, "
            "expected graduation May 2028) and a member of the "
            "[[Academy of Machine Learning]]. Currently an unpaid remote intern at "
            "[[Cortex Armor]]; previously did quantum computing research at "
            "[[DoQuantum]]. Flagship project is [[Athena]]; other builds include "
            "[[LeetCoach]], [[GenTerp]], [[Chess Engine]], and an [[MCP RAG server]]. "
            "Grinding the [[LeetCode grind]] toward 500 problems while preparing for "
            "[[Summer 2027 recruiting]]. Works mainly in [[Python]], [[Rust]], "
            "[[OCaml]], [[Verilog]], [[Kubernetes]], and [[AWS]]. See "
            "[[Working style and preferences]]. Based in the DC / Maryland area."
        ),
    ),
    # ---- Academics -------------------------------------------------------
    dict(
        title="University of Maryland",
        source="auto",
        tags=["school", "umd"],
        content=(
            "UMD — where [[Varun]] studies Computer Engineering. Home of the "
            "[[Academy of Machine Learning]] and the [[DoQuantum]] quantum research "
            "group. Relevant coursework: [[CMSC330]], [[ENEE245]], [[ENEE205]], and "
            "[[Algorithms]]. [[GenTerp]] was built to help UMD students plan courses."
        ),
    ),
    dict(
        title="Academy of Machine Learning",
        source="auto",
        tags=["umd", "ml"],
        content=(
            "A selective machine-learning group at [[University of Maryland]] that "
            "[[Varun]] is a member of. Connects to his broader AI/ML work — "
            "[[LoRA and QLoRA]] fine-tuning, [[RAG]], and local models via [[Ollama]]."
        ),
    ),
    dict(
        title="CMSC330",
        source="auto",
        tags=["course", "umd"],
        content=(
            "UMD programming-languages course taken by [[Varun]]: [[OCaml]], "
            "[[Rust]], and automata theory. Part of his work at "
            "[[University of Maryland]]."
        ),
    ),
    dict(
        title="ENEE245",
        source="auto",
        tags=["course", "umd", "hardware"],
        content=(
            "Digital circuits lab at [[University of Maryland]]: [[Verilog]] on an "
            "[[FPGA]] (Basys3) — FSM design, sequential multipliers, counters. Pairs "
            "with [[ENEE205]]."
        ),
    ),
    dict(
        title="ENEE205",
        source="auto",
        tags=["course", "umd", "hardware"],
        content=(
            "Electric circuits course at [[University of Maryland]], the analog "
            "counterpart to the digital work in [[ENEE245]]."
        ),
    ),
    dict(
        title="Algorithms",
        source="auto",
        tags=["cs", "theory"],
        content=(
            "Graph algorithms and the Master Theorem, studied at "
            "[[University of Maryland]]. The foundation under the [[LeetCode grind]] "
            "and the search in [[Chess Engine]]."
        ),
    ),
    dict(
        title="DoQuantum",
        source="auto",
        tags=["research", "quantum", "umd"],
        content=(
            "Quantum computing research group at [[University of Maryland]] where "
            "[[Varun]] did prior research."
        ),
    ),
    # ---- Work ------------------------------------------------------------
    dict(
        title="Cortex Armor",
        source="auto",
        tags=["internship", "ai-security", "startup"],
        content=(
            "AI security startup in Cupertino, CA where [[Varun]] is an unpaid remote "
            "intern. He builds a policy-enforcement system that restricts AI-agent "
            "access to tools, data, and cloud resources via [[IAM]] and "
            "[[Kubernetes]]. Thematically close to [[Athena]]'s agent-governance "
            "concerns."
        ),
    ),
    # ---- Projects --------------------------------------------------------
    dict(
        title="Athena",
        source="auto",
        tags=["project", "flagship", "ai"],
        content=(
            "[[Varun]]'s flagship: a self-hosted, JARVIS-style AI assistant on a "
            "3-node bare-metal [[Kubernetes]] (k3s) cluster. Stack: [[LangGraph]] "
            "orchestration, [[RAG]] via [[LlamaIndex]] + [[Qdrant]], [[FastAPI]], "
            "cron pipelines, and local models through [[Ollama]]. Observability via "
            "[[Prometheus & Grafana]] and LangSmith. Includes an internship-hunter "
            "pipeline. Uses a root CLAUDE.md as a living runbook and splits planning "
            "vs. implementation across separate contexts — see "
            "[[Working style and preferences]]."
        ),
    ),
    dict(
        title="LeetCoach",
        source="auto",
        tags=["project", "chrome-extension", "ai"],
        content=(
            "An MV3 Chrome extension by [[Varun]]: an AI coaching sidebar for "
            "LeetCode. Backend on [[AWS Lambda]] with [[Amazon Bedrock]] (Claude "
            "Sonnet / Haiku), [[DynamoDB]] rate limiting, an [[AWS Budgets]] kill "
            "switch, and chunked HTTP streaming via a Lambda C-extension "
            "monkey-patch. Supports the [[LeetCode grind]]."
        ),
    ),
    dict(
        title="GenTerp",
        source="auto",
        tags=["project", "full-stack", "umd"],
        content=(
            "Full-stack [[University of Maryland]] course-discovery platform "
            "(genterp.vercel.app). A recursive-descent boolean parser, a [[Supabase]] "
            "backend, Gemini-powered summaries, and a weekly calendar with conflict "
            "detection, deployed on [[Vercel]]."
        ),
    ),
    dict(
        title="Chess Engine",
        source="auto",
        tags=["project", "ai", "search"],
        content=(
            "[[Varun]]'s chess engine: alpha-beta search (see [[Algorithms]]) with "
            "PyTorch Texel tuning over 200k+ positions and a PyPy subprocess for a "
            "4–7x speedup. Written in [[Python]]."
        ),
    ),
    dict(
        title="MCP RAG server",
        source="auto",
        tags=["project", "rag", "mcp"],
        content=(
            "A RAG-as-an-MCP-server project: [[LlamaIndex]] with ChromaDB / "
            "[[Qdrant]] and [[Ollama]]. Same [[RAG]] lineage as [[Athena]]'s "
            "retrieval stack."
        ),
    ),
    # ---- Infra / tools ---------------------------------------------------
    dict(
        title="Kubernetes",
        source="auto",
        tags=["infra", "k8s"],
        content=(
            "Container orchestration (k3s flavor) underpinning [[Athena]]'s 3-node "
            "cluster, and a building block of the [[Cortex Armor]] policy system "
            "(alongside [[IAM]])."
        ),
    ),
    dict(
        title="LangGraph",
        source="auto",
        tags=["ai", "agents"],
        content=(
            "Graph-based agent orchestration framework ([[LangChain]] ecosystem) used "
            "for [[Athena]]'s agent."
        ),
    ),
    dict(
        title="LlamaIndex",
        source="auto",
        tags=["ai", "rag"],
        content=(
            "Document ingestion / indexing framework powering [[RAG]] in [[Athena]] "
            "and the [[MCP RAG server]]."
        ),
    ),
    dict(
        title="Qdrant",
        source="auto",
        tags=["ai", "vector-db"],
        content=(
            "Vector database used for [[RAG]] retrieval in [[Athena]] and the "
            "[[MCP RAG server]]."
        ),
    ),
    dict(
        title="FastAPI",
        source="auto",
        tags=["python", "web"],
        content=(
            "Python web framework serving [[Athena]]'s agent API. Written in "
            "[[Python]]."
        ),
    ),
    dict(
        title="Rust",
        source="auto",
        tags=["language"],
        content=(
            "Systems language [[Varun]] learned in [[CMSC330]] and uses for "
            "[[Athena]]'s MCP server component."
        ),
    ),
    dict(
        title="OCaml",
        source="auto",
        tags=["language", "functional"],
        content="Functional language from [[CMSC330]] at [[University of Maryland]].",
    ),
    dict(
        title="Verilog",
        source="auto",
        tags=["hardware", "hdl"],
        content=(
            "Hardware description language [[Varun]] writes for [[FPGA]] work in "
            "[[ENEE245]] (Basys3 board, Vivado)."
        ),
    ),
    dict(
        title="FPGA",
        source="auto",
        tags=["hardware"],
        content=(
            "Field-programmable gate array (Basys3 + Vivado) programmed in "
            "[[Verilog]] for [[ENEE245]]."
        ),
    ),
    dict(
        title="AWS",
        source="auto",
        tags=["cloud"],
        content=(
            "Amazon Web Services — [[Varun]] is AWS Cloud Practitioner certified. He "
            "uses [[AWS Lambda]], [[Amazon Bedrock]], [[DynamoDB]], and "
            "[[AWS Budgets]], most notably in [[LeetCoach]]."
        ),
    ),
    dict(
        title="AWS Lambda",
        source="auto",
        tags=["cloud", "serverless"],
        content=(
            "Serverless compute on [[AWS]] running the [[LeetCoach]] backend, "
            "including a C-extension monkey-patch for chunked HTTP streaming."
        ),
    ),
    dict(
        title="Amazon Bedrock",
        source="auto",
        tags=["cloud", "ai"],
        content=(
            "Managed LLM service on [[AWS]] (Claude Sonnet / Haiku) powering "
            "[[LeetCoach]]'s coaching."
        ),
    ),
    dict(
        title="DynamoDB",
        source="auto",
        tags=["cloud", "database"],
        content="[[AWS]] NoSQL store used for rate limiting in [[LeetCoach]].",
    ),
    dict(
        title="AWS Budgets",
        source="auto",
        tags=["cloud", "cost"],
        content="[[AWS]] cost guardrail wired as a kill switch in [[LeetCoach]].",
    ),
    dict(
        title="Ollama",
        source="auto",
        tags=["ai", "local-llm"],
        content=(
            "Local LLM runtime (gemma models) for [[Athena]] and the "
            "[[MCP RAG server]] — keeps inference self-hosted."
        ),
    ),
    dict(
        title="RAG",
        source="auto",
        tags=["ai", "retrieval"],
        content=(
            "Retrieval-augmented generation — the pattern behind [[Athena]] and the "
            "[[MCP RAG server]], built from [[LlamaIndex]] + [[Qdrant]]."
        ),
    ),
    dict(
        title="Prometheus & Grafana",
        source="auto",
        tags=["observability", "infra"],
        content="Metrics + dashboards stack providing observability for [[Athena]].",
    ),
    dict(
        title="LoRA and QLoRA",
        source="auto",
        tags=["ai", "fine-tuning"],
        content=(
            "Parameter-efficient fine-tuning concepts (Unsloth, Axolotl) in "
            "[[Varun]]'s AI/ML toolkit, connected to the "
            "[[Academy of Machine Learning]]."
        ),
    ),
    dict(
        title="Python",
        source="auto",
        tags=["language"],
        content=(
            "[[Varun]]'s primary language — [[Athena]], [[Chess Engine]], [[FastAPI]] "
            "services, and most of the [[LeetCode grind]]."
        ),
    ),
    # ---- Goals / recruiting ----------------------------------------------
    dict(
        title="LeetCode grind",
        source="explicit",
        tags=["goal", "interview-prep"],
        content=(
            "Goal: 500 total solved (~300 done), targeting roughly a 1:3 Hard-to-"
            "Medium ratio on the remainder, with parallel mock-interview practice. "
            "Rests on [[Algorithms]], is assisted by [[LeetCoach]], and feeds "
            "[[Summer 2027 recruiting]]."
        ),
    ),
    dict(
        title="Summer 2027 recruiting",
        source="explicit",
        tags=["goal", "recruiting"],
        events=[{"date": "2027-06-01", "kind": "recruiting"}],
        content=(
            "Preparing for Summer 2027 internship recruiting. Three tailored LaTeX "
            "resume variants — Generalist SWE, AI/Agents, Infrastructure/Platform — "
            "produced by [[Resume automation]]. Interview prep via the "
            "[[LeetCode grind]]."
        ),
    ),
    dict(
        title="Resume automation",
        source="auto",
        tags=["tooling", "recruiting"],
        content=(
            "A resume-maker repo that uses Claude Code as the agent to generate the "
            "tailored LaTeX resumes for [[Summer 2027 recruiting]]. Reflects "
            "[[Varun]]'s [[Working style and preferences]] (Claude Code-heavy "
            "workflows)."
        ),
    ),
    # ---- Meta ------------------------------------------------------------
    dict(
        title="Working style and preferences",
        source="explicit",
        tags=["preferences", "workflow"],
        content=(
            "[[Varun]] prefers plain-text explanations over diagrams/widgets, "
            "iterative narrow-scope edits (\"don't change anything else\"), and "
            "drill-based learning with immediate follow-up problems over long "
            "explanations. Heavy [[Athena]]-style Claude Code use: skills, subagent "
            "definitions, MCP servers, PostToolUse hooks, and a deliberate split of "
            "planning vs. implementation contexts."
        ),
    ),
    dict(
        title="IAM",
        source="auto",
        tags=["security", "infra"],
        content=(
            "Identity & access management — a primitive in the [[Cortex Armor]] "
            "policy-enforcement system, layered with [[Kubernetes]] controls."
        ),
    ),
    dict(
        title="Supabase",
        source="auto",
        tags=["backend", "database"],
        content="Postgres-backed backend-as-a-service powering [[GenTerp]].",
    ),
    dict(
        title="Vercel",
        source="auto",
        tags=["hosting"],
        content="Frontend hosting platform where [[GenTerp]] is deployed.",
    ),
    dict(
        title="LangChain",
        source="auto",
        tags=["ai"],
        content=(
            "LLM application framework; the ecosystem [[LangGraph]] and [[LlamaIndex]] "
            "sit alongside in [[Athena]]'s stack."
        ),
    ),
]


def main() -> None:
    for note in NOTES:
        r = memory.write_note(
            note["title"],
            note["content"],
            note.get("tags", []),
            source=note.get("source", "auto"),
            events=note.get("events", []),
            replace=False,
        )
        print(f"{r['action']:9} {r['slug']}.md  [{r['source']}]")
    memory.write_index()
    print(f"\n{len(NOTES)} notes written to {os.environ['MEMORY_DIR']}")


if __name__ == "__main__":
    main()
